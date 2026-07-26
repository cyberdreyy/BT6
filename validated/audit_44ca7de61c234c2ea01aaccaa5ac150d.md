### Title
Single-Oracle Median Manipulation via Incorrect `max_timestamp` Tracking Enables Full Price Control — (`File: crates/sui-oracle/move/oracle/sources/meta_oracle.move`)

---

### Summary

The `MetaOracle` module in the Sui oracle framework contains two compounding bugs. First, `add_simple_oracle` unconditionally overwrites `max_timestamp` with each successive oracle's timestamp instead of tracking the true maximum. Second, `median()` picks the element at index `n/2` from a sorted list, which for an odd oracle count is the true median and is fully controllable by a single oracle whose value falls between the other two. Together, an attacker who controls one oracle and can arrange for it to be added last can (a) shift the time-window reference point to exclude all other oracles' data, and (b) set the surviving median to an arbitrary value.

---

### Finding Description

**Bug 1 — `max_timestamp` is last-write, not max-write (`add_simple_oracle`, line 41)**

```move
// meta_oracle.move line 38-44
public fun add_simple_oracle<T: copy + drop + store>(meta_oracle: &mut MetaOracle<T>, oracle: &SimpleOracle) {
    let oracle_data = oracle::simple_oracle::get_latest_data(oracle, meta_oracle.ticker);
    if (option::is_some(&oracle_data)) {
        meta_oracle.max_timestamp = data::timestamp(option::borrow(&oracle_data)); // ← overwrites, never compares
    };
    vector::push_back(&mut meta_oracle.oracle_data, oracle_data);
}
```

The intended invariant is that `max_timestamp` holds the largest timestamp seen across all added oracles, so that `combine()` can compute a consistent `min_timestamp = max_timestamp - time_window_ms` window. The actual code replaces `max_timestamp` on every call, so after all oracles are added, `max_timestamp` equals the timestamp of whichever oracle was added last that had data — not the true maximum. [1](#0-0) 

**Bug 2 — `combine()` uses the corrupted `max_timestamp` as the sole time-window anchor (line 53)**

```move
// meta_oracle.move line 51-68
fun combine<T: copy + drop>(meta_oracle: MetaOracle<T>): (vector<T>, vector<address>) {
    let MetaOracle { mut oracle_data, threshold, time_window_ms, ticker: _, max_timestamp } = meta_oracle;
    let min_timestamp = max_timestamp - time_window_ms;   // ← derived from corrupted field
    ...
    if (data::timestamp(&oracle_data) > min_timestamp) {  // ← gate that can be weaponized
```

If an attacker's oracle is added last with timestamp `T_attack`, then `max_timestamp = T_attack` and `min_timestamp = T_attack - time_window_ms`. Any legitimate oracle whose timestamp is older than `min_timestamp` is silently dropped. The threshold check fires only after filtering, so if enough legitimate oracles are dropped the transaction aborts (DoS), or if the threshold is met with only the attacker's oracle(s) remaining, the attacker controls the entire price. [2](#0-1) 

**Bug 3 — `median()` picks index `n/2`, giving a single oracle full control of the center value (lines 71-77)**

```move
public fun median<T: copy + drop>(meta_oracle: MetaOracle<T>): TrustedData<T> {
    let (values, oracles) = combine(meta_oracle);
    let mut sortedData = quick_sort(values);
    let i = vector::length(&sortedData) / 2;   // ← integer division; for n=3, i=1
    let value = vector::remove(&mut sortedData, i);
    TrustedData { value, oracles }
}
```

For three oracles with prices `[598, X, 603]` (sorted), the median is always `X`. Any value the attacker submits in `[598, 603]` becomes the canonical price. This is the direct analog of the external report's Apollo Oracle finding. [3](#0-2) 

**Permissionless oracle creation amplifies the attack surface**

`SimpleOracle.create()` is a public entry function with no whitelist or capability check:

```move
// simple_oracle.move line 61-64
public entry fun create(name: String, url: String, description: String, ctx: &mut TxContext) {
    let oracle = SimpleOracle { id: object::new(ctx), address: tx_context::sender(ctx), name, description, url };
    transfer::share_object(oracle)
}
```

Any ordinary SUI holder can create a `SimpleOracle` and submit arbitrary data to it. The `MetaOracle` module has no on-chain mechanism to distinguish a trusted oracle from an attacker-created one; that responsibility is entirely delegated to the calling application. [4](#0-3) 

---

### Impact Explanation

Any DeFi application that uses `MetaOracle` for price feeds (e.g., the `trusted_fx` pattern shown in the test module) is exposed to:

1. **Full median control** — with an odd oracle count and one compromised oracle, the attacker sets the price to any value between the two honest extremes.
2. **Legitimate-oracle exclusion** — by submitting a timestamp far ahead of the other oracles and being added last, the attacker shifts the time window so that all honest oracle data falls outside it. If the threshold is met by the attacker's oracle(s) alone, the attacker becomes the sole price source.
3. **DoS** — if the threshold cannot be met after exclusion, every call to `median()` aborts, permanently blocking any protocol function that depends on the price feed.

This matches the "harmful smart-contract behavior" impact class. [5](#0-4) 

---

### Likelihood Explanation

- `SimpleOracle.create()` is permissionless; any SUI holder can create an oracle at zero cost beyond gas.
- The `MetaOracle` module provides no oracle-identity validation; the calling application must supply it.
- Applications that pass caller-supplied oracle object references (as `trusted_fx` does) allow an attacker to inject their own oracle as one of the inputs.
- The `max_timestamp` bug is triggered simply by being the last oracle added — a condition the attacker can satisfy by passing their oracle as the final argument.

Likelihood is **Medium**: exploitation requires the attacker's oracle to be accepted by the consuming application, but the framework provides no guard against this. [6](#0-5) 

---

### Recommendation

1. **Fix `max_timestamp` tracking** — replace the unconditional assignment with a max-comparison:
   ```move
   let ts = data::timestamp(option::borrow(&oracle_data));
   if (ts > meta_oracle.max_timestamp) {
       meta_oracle.max_timestamp = ts;
   };
   ```

2. **Add an oracle allowlist** — store a `VecSet<address>` of trusted oracle addresses in `MetaOracle` and assert membership in `add_simple_oracle`.

3. **Require a minimum oracle count above the manipulation threshold** — for `n` oracles, require at least `2f+1` where `f` is the maximum number of tolerated compromised oracles.

4. **Consider even-count averaging** — for even `n`, average the two center values to prevent a single oracle from controlling the exact median. [1](#0-0) 

---

### Proof of Concept

Setup: three `SimpleOracle` objects O₀, O₁, O₂; `MetaOracle` with `threshold=3`, `time_window_ms=60_000`.

**Scenario A — classic median manipulation (Bug 3 alone):**

| Oracle | Price submitted | Timestamp |
|--------|----------------|-----------|
| O₀     | 603            | T         |
| O₁     | 598            | T         |
| O₂ (attacker) | 600   | T         |

Sorted: `[598, 600, 603]`. Index `3/2 = 1` → median = **600**. Attacker can set this to any value in `[598, 603]` by changing their submission.

**Scenario B — time-window exclusion (Bugs 1+2, threshold=1):**

| Oracle | Price submitted | Timestamp |
|--------|----------------|-----------|
| O₀     | 603            | T         |
| O₁     | 598            | T         |
| O₂ (attacker, added last) | 1 | T + 120_000 |

After `add_simple_oracle` calls: `max_timestamp = T + 120_000`. `min_timestamp = T + 120_000 - 60_000 = T + 60_000`. O₀ and O₁ have timestamp `T < T + 60_000` → both excluded. Only O₂ passes. With `threshold=1`, `median()` returns **1** — the attacker's arbitrary price — with no honest oracle data contributing. [7](#0-6) [4](#0-3)

### Citations

**File:** crates/sui-oracle/move/oracle/sources/meta_oracle.move (L38-77)
```text
    public fun add_simple_oracle<T: copy + drop + store>(meta_oracle: &mut MetaOracle<T>, oracle: &SimpleOracle) {
        let oracle_data = oracle::simple_oracle::get_latest_data(oracle, meta_oracle.ticker);
        if (option::is_some(&oracle_data)) {
            meta_oracle.max_timestamp = data::timestamp(option::borrow(&oracle_data));
        };
        vector::push_back(&mut meta_oracle.oracle_data, oracle_data);
    }

    public struct TrustedData<T> has copy, drop {
        value: T,
        oracles: vector<address>,
    }

    fun combine<T: copy + drop>(meta_oracle: MetaOracle<T>, ): (vector<T>, vector<address>) {
        let MetaOracle { mut oracle_data, threshold, time_window_ms, ticker: _, max_timestamp } = meta_oracle;
        let min_timestamp = max_timestamp - time_window_ms;
        let mut values = vector<T>[];
        let mut oracles = vector<address>[];
        while (vector::length(&oracle_data) > 0) {
            let oracle_data = vector::remove(&mut oracle_data, 0);
            if (option::is_some(&oracle_data)) {
                let oracle_data = option::destroy_some(oracle_data);
                if (data::timestamp(&oracle_data) > min_timestamp) {
                    vector::push_back(&mut values, *data::value(&oracle_data));
                    vector::push_back(&mut oracles, *data::oracle_address(&oracle_data));
                };
            };
        };
        assert!(vector::length(&values) >= threshold, EValidDataSizeLessThanThreshold);
        (values, oracles)
    }

    /// take the median value
    public fun median<T: copy + drop>(meta_oracle: MetaOracle<T>): TrustedData<T> {
        let (values, oracles) = combine(meta_oracle);
        let mut sortedData = quick_sort(values);
        let i = vector::length(&sortedData) / 2;
        let value = vector::remove(&mut sortedData, i);
        TrustedData { value, oracles }
    }
```

**File:** crates/sui-oracle/move/oracle/sources/simple_oracle.move (L61-74)
```text
    public entry fun create(name: String, url: String, description: String, ctx: &mut TxContext) {
        let oracle = SimpleOracle { id: object::new(ctx), address: tx_context::sender(ctx), name, description, url };
        transfer::share_object(oracle)
    }

    public entry fun submit_data<T: store + copy + drop>(
        oracle: &mut SimpleOracle,
        clock: &Clock,
        ticker: String,
        value: T,
        identifier: String,
        ctx: &mut TxContext
    ) {
        assert!(oracle.address == tx_context::sender(ctx), ESenderNotOracle);
```
