### Title
Unauthorized `TreasuryCap<T>` Theft via Frontrunning `new_currency<T>` in `CoinRegistry` — (File: `crates/sui-framework/packages/sui-framework/sources/registries/coin_registry.move`)

---

### Summary

`coin_registry::new_currency<T>` is a `public` function that accepts any type `T` satisfying `key` and immediately mints and returns a `TreasuryCap<T>` to the caller — with no check that the caller owns or is authorized to register type `T`. An attacker who observes a legitimate developer's pending call to `new_currency<MyCoin>` can submit an identical call first, stealing the `TreasuryCap<MyCoin>` and permanently blocking the legitimate developer from registering their coin type.

---

### Finding Description

`new_currency<T>` is declared `public` with only a `key` ability constraint on `T`:

```move
public fun new_currency<T: /* internal */ key>(
    registry: &mut CoinRegistry,
    ...
): (CurrencyInitializer<T>, TreasuryCap<T>) {
    assert!(!registry.exists<T>(), ECurrencyAlreadyExists);
    ...
    let treasury_cap = coin::new_treasury_cap(ctx);   // ← minting cap given to caller
``` [1](#0-0) 

The `/* internal */` annotation is a plain comment — it carries no Move language enforcement. The function is callable by any address. `CoinRegistry` is a shared object (created at `0xc`), so any transaction can pass it as a mutable argument. [2](#0-1) 

`coin::new_treasury_cap<T>` is `public(package)` and creates a `TreasuryCap<T>` without any one-time-witness check:

```move
public(package) fun new_treasury_cap<T>(ctx: &mut TxContext): TreasuryCap<T> {
    TreasuryCap {
        id: object::new(ctx),
        total_supply: balance::create_supply_internal(),
    }
}
``` [3](#0-2) 

Because `new_currency` lives in the same `sui` package, it can call this `public(package)` function freely. The result is that **any caller** can obtain a `TreasuryCap<T>` for **any** type `T` with the `key` ability — including types defined in third-party modules.

The only guard is the first-come-first-served `ECurrencyAlreadyExists` check:

```move
assert!(!registry.exists<T>(), ECurrencyAlreadyExists);
``` [4](#0-3) 

This is exactly the Audius pattern: a unique slot (coin type `T`) is claimed by whoever submits first, with no binding between the caller's identity and the type being registered.

The developer's own documentation acknowledges the intent but does not enforce it:

> *"This can be called from the module that defines `T` any time after it has been published."* [5](#0-4) 

---

### Impact Explanation

The `TreasuryCap<T>` is the sole minting authority for coin type `T`. Whoever holds it can call `coin::mint` to create an unbounded supply of `T`. An attacker who frontruns `new_currency<MyCoin>` receives this capability and can:

1. Mint unlimited `MyCoin` tokens.
2. Permanently block the legitimate developer from registering `MyCoin` (`ECurrencyAlreadyExists` is a one-way door — `derived_object::claim` marks the slot `Reserved` and the `ClaimedStatus` cannot be reverted).
3. If `MyCoin` is subsequently integrated into any DeFi protocol (AMM, lending, bridge), drain it by minting tokens at will.

This matches the **Critical** impact gate: *"direct fund theft … from unauthorized object creation"* — the `TreasuryCap<T>` is created for a type the attacker does not own, and it enables unlimited minting. [6](#0-5) 

---

### Likelihood Explanation

- `CoinRegistry` is a shared object; any transaction can include it as a mutable input.
- The type `T` is fully visible in the pending transaction's Move call arguments (package address + module + struct name).
- A full-node operator or any observer of the Sui gossip layer can see unconfirmed transactions before they are sequenced.
- The attacker's transaction requires only a small gas payment — no stake, no special capability.
- The window is the gap between a developer broadcasting their `new_currency<T>` call and its inclusion in a checkpoint.

---

### Recommendation

Require proof of ownership of type `T` at call time. The existing `new_currency_with_otw` path already does this correctly by demanding a one-time-witness value that can only be produced inside the module's `init` function:

```move
assert!(sui::types::is_one_time_witness(&otw), ENotOneTimeWitness);
``` [7](#0-6) 

For the `new_currency` path (post-`init` registration), the equivalent fix is to require a witness struct defined in the same module as `T`. For example, require a `&UpgradeCap` whose package matches the defining package of `T` (verifiable via `type_name::with_defining_ids<T>()`), or require a module-specific witness type `W` where `W` is defined in the same package as `T`. Until such a check is added, `new_currency` should be restricted to `public(package)` or removed in favour of the OTW path.

---

### Proof of Concept

```
// Step 1 – Developer publishes:
module dev::my_coin {
    public struct MyCoin has key { id: sui::object::UID }
}

// Step 2 – Developer broadcasts (but has not yet been sequenced):
sui::coin_registry::new_currency<dev::my_coin::MyCoin>(
    registry,  // 0xc – shared CoinRegistry
    6, "MC", "MyCoin", "My coin", "",
    ctx
)

// Step 3 – Attacker observes the pending transaction, extracts the type,
//           and submits the identical call from their own address:
sui::coin_registry::new_currency<dev::my_coin::MyCoin>(
    registry,
    6, "MC", "MyCoin", "My coin", "",
    attacker_ctx   // ← attacker's TxContext
)

// Step 4 – Attacker's transaction is sequenced first.
//   Result: attacker holds TreasuryCap<MyCoin>; can mint unlimited tokens.

// Step 5 – Developer's transaction aborts:
//   assert!(!registry.exists<T>(), ECurrencyAlreadyExists)  ← fires
```

The attacker now permanently controls the minting authority for `MyCoin`. The developer has no recourse: `derived_object::claim` has marked `CurrencyKey<MyCoin>` as `Reserved` in the registry's dynamic fields, and there is no recovery path. [8](#0-7) [9](#0-8)

### Citations

**File:** crates/sui-framework/packages/sui-framework/sources/registries/coin_registry.move (L170-201)
```text
/// Creates a new currency.
///
/// Note: This constructor has no long term difference from `new_currency_with_otw`.
/// This can be called from the module that defines `T` any time after it has been published.
public fun new_currency<T: /* internal */ key>(
    registry: &mut CoinRegistry,
    decimals: u8,
    symbol: String,
    name: String,
    description: String,
    icon_url: String,
    ctx: &mut TxContext,
): (CurrencyInitializer<T>, TreasuryCap<T>) {
    assert!(!registry.exists<T>(), ECurrencyAlreadyExists);
    assert!(is_ascii_printable!(&symbol), EInvalidSymbol);

    let treasury_cap = coin::new_treasury_cap(ctx);
    let currency = Currency<T> {
        id: derived_object::claim(&mut registry.id, CurrencyKey<T>()),
        decimals,
        name,
        symbol,
        description,
        icon_url,
        supply: option::some(SupplyState::Unknown),
        regulated: RegulatedState::Unregulated,
        treasury_cap_id: option::some(object::id(&treasury_cap)),
        metadata_cap_id: MetadataCapState::Unclaimed,
        extra_fields: vec_map::empty(),
    };

    (CurrencyInitializer { currency, is_otw: false, extra_fields: bag::new(ctx) }, treasury_cap)
```

**File:** crates/sui-framework/packages/sui-framework/sources/registries/coin_registry.move (L218-218)
```text
    assert!(sui::types::is_one_time_witness(&otw), ENotOneTimeWitness);
```

**File:** crates/sui-framework/packages/sui-framework/sources/registries/coin_registry.move (L666-672)
```text
fun create(ctx: &TxContext) {
    assert!(ctx.sender() == @0x0, ENotSystemAddress);

    transfer::share_object(CoinRegistry {
        id: object::sui_coin_registry_object_id(),
    });
}
```

**File:** crates/sui-framework/packages/sui-framework/sources/coin.move (L523-528)
```text
public(package) fun new_treasury_cap<T>(ctx: &mut TxContext): TreasuryCap<T> {
    TreasuryCap {
        id: object::new(ctx),
        total_supply: balance::create_supply_internal(),
    }
}
```

**File:** crates/sui-framework/packages/sui-framework/sources/derived_object.move (L32-44)
```text
public enum ClaimedStatus has store {
    /// The UID has been claimed and cannot be re-claimed or used.
    Reserved,
}

/// Claim a deterministic UID, using the parent's UID & any key.
public fun claim<K: copy + drop + store>(parent: &mut UID, key: K): UID {
    let addr = derive_address(parent.to_inner(), key);
    let id = addr.to_id();
    assert!(!df::exists(parent, Claimed(id)), EObjectAlreadyExists);
    df::add(parent, Claimed(id), ClaimedStatus::Reserved);
    object::new_uid_from_hash(addr)
}
```
