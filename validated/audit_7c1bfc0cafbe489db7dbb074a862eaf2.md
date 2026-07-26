### Title
Uninitialized `random_bytes` Read in `new_generator` Produces Predictable Seed Before First `RandomnessStateUpdate` - (File: crates/sui-framework/packages/sui-framework/sources/random.move)

---

### Summary

`sui::random::new_generator()` reads `inner.random_bytes` to derive a generator seed without checking whether the field has been initialized. The `Random` singleton is created at genesis with `random_bytes: vector[]`. Until the first `RandomnessStateUpdate` system transaction executes (after DKG completes), any user transaction that calls `new_generator()` receives a generator whose seed is `hmac_sha3_256(&[], &fresh_object_address)` — a value derived from an empty key, stripping the DKG beacon of all its entropy contribution and making the output predictable.

---

### Finding Description

`create()` initializes `RandomInner` with an empty byte vector:

```move
let inner = RandomInner {
    version,
    epoch: ctx.epoch(),
    randomness_round: 0,
    random_bytes: vector[],   // ← uninitialized
};
```

`update_randomness_state()` explicitly recognizes this uninitialized state and guards against it:

```move
if (inner.randomness_round == 0 && inner.epoch == 0 && inner.random_bytes.is_empty()) {
    // First update should be for round zero.
    assert!(new_round == 0, EInvalidRandomnessUpdate);
}
```

But `new_generator()` performs no equivalent guard:

```move
public fun new_generator(r: &Random, ctx: &mut TxContext): RandomGenerator {
    let inner = r.load_inner();
    let seed = hmac_sha3_256(
        &inner.random_bytes,          // ← empty vector[] if called before first update
        &ctx.fresh_object_address().to_bytes(),
    );
    RandomGenerator { seed, counter: 0, buffer: vector[] }
}
```

`load_inner()` only checks the version tag, not whether `random_bytes` is populated:

```move
fun load_inner(self: &Random): &RandomInner {
    let version = self.inner.version();
    assert!(version == CURRENT_VERSION, EWrongInnerVersion);
    let inner: &RandomInner = self.inner.load_value();
    assert!(inner.version == version, EWrongInnerVersion);
    inner   // ← returned with random_bytes == vector[]
}
```

User transactions are permitted to pass `SUI_RANDOMNESS_STATE_OBJECT_ID` as an immutable shared input, confirmed by the transaction-check allow-list:

```rust
(SUI_RANDOMNESS_STATE_OBJECT_ID, SharedObjectMutability::Immutable) => (),
```

There is no protocol-level gate that prevents a user PTB from calling `new_generator()` before the first `RandomnessStateUpdate` checkpoint is finalized.

---

### Impact Explanation

When `random_bytes` is empty, the HMAC key is the empty string — a publicly known constant. The only entropy in the seed is `fresh_object_address()`, which is derived deterministically from the transaction digest. An attacker can:

1. Sign a transaction that calls a randomness-dependent Move contract (lottery, game, NFT mint).
2. Compute `fresh_object_address()` from the signed digest.
3. Evaluate `hmac_sha3_256(&[], &address)` locally to predict every output of the generator.
4. Submit only if the predicted outcome is favorable; otherwise discard and retry with a new transaction.

This breaks the core security invariant of `sui::random`: that outputs are unpredictable to any party before the transaction is finalized. Any Move contract that uses `sui::random::new_generator()` during the initialization window is fully exploitable.

---

### Likelihood Explanation

The window exists at every epoch boundary: DKG requires multiple consensus rounds to complete, typically taking seconds to minutes. During that window, user transactions execute normally. Additionally, if DKG times out and fails for an epoch, `random_bytes` remains `vector[]` for the entire epoch, extending the window indefinitely for that epoch. The trigger requires only a standard user PTB with the `Random` object as an immutable shared input — no special privileges.

---

### Recommendation

Add an initialization guard in `new_generator()` (or in `load_inner()`) that aborts if `random_bytes` is empty:

```move
public fun new_generator(r: &Random, ctx: &mut TxContext): RandomGenerator {
    let inner = r.load_inner();
    assert!(!inner.random_bytes.is_empty(), ERandomnessNotInitialized);
    let seed = hmac_sha3_256(
        &inner.random_bytes,
        &ctx.fresh_object_address().to_bytes(),
    );
    RandomGenerator { seed, counter: 0, buffer: vector[] }
}
```

A new error constant `ERandomnessNotInitialized` should be added alongside the existing ones. This mirrors the pattern already used in `update_randomness_state()` and is the direct analog of the recommended fix in the external report.

---

### Proof of Concept

```move
// Deployed Move contract (attacker-controlled)
module attacker::exploit {
    use sui::random::{Self, Random, RandomGenerator};

    public entry fun exploit_uninitialized_random(
        r: &Random,
        ctx: &mut TxContext,
    ) {
        // Called before first RandomnessStateUpdate in a new epoch.
        // random_bytes == vector[] → seed is fully predictable.
        let mut gen: RandomGenerator = random::new_generator(r, ctx);
        // Attacker pre-computed this value off-chain using hmac_sha3_256(&[], &address).
        let predicted: u64 = random::generate_u64(&mut gen);
        // Use predicted value to win a lottery, claim an NFT, etc.
        assert!(predicted == ATTACKER_PRECOMPUTED_VALUE, 0);
    }
}
```

The attacker signs the transaction, computes `fresh_object_address()` from the digest, evaluates the HMAC locally, and submits only when the predicted outcome is favorable. No special permissions are required beyond holding SUI for gas.

---

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** crates/sui-framework/packages/sui-framework/sources/random.move (L47-52)
```text
    let inner = RandomInner {
        version,
        epoch: ctx.epoch(),
        randomness_round: 0,
        random_bytes: vector[],
    };
```

**File:** crates/sui-framework/packages/sui-framework/sources/random.move (L76-84)
```text
fun load_inner(self: &Random): &RandomInner {
    let version = self.inner.version();

    // Replace this with a lazy update function when we add a new version of the inner object.
    assert!(version == CURRENT_VERSION, EWrongInnerVersion);
    let inner: &RandomInner = self.inner.load_value();
    assert!(inner.version == version, EWrongInnerVersion);
    inner
}
```

**File:** crates/sui-framework/packages/sui-framework/sources/random.move (L101-103)
```text
    if (inner.randomness_round == 0 && inner.epoch == 0 && inner.random_bytes.is_empty()) {
        // First update should be for round zero.
        assert!(new_round == 0, EInvalidRandomnessUpdate);
```

**File:** crates/sui-framework/packages/sui-framework/sources/random.move (L143-150)
```text
public fun new_generator(r: &Random, ctx: &mut TxContext): RandomGenerator {
    let inner = r.load_inner();
    let seed = hmac_sha3_256(
        &inner.random_bytes,
        &ctx.fresh_object_address().to_bytes(),
    );
    RandomGenerator { seed, counter: 0, buffer: vector[] }
}
```

**File:** crates/sui-transaction-checks/src/lib.rs (L643-645)
```rust
                        | (SUI_CLOCK_OBJECT_ID, SharedObjectMutability::Immutable)
                        | (SUI_RANDOMNESS_STATE_OBJECT_ID, SharedObjectMutability::Immutable)
                        | (SUI_ACCUMULATOR_ROOT_OBJECT_ID, SharedObjectMutability::Immutable) => (),
```
