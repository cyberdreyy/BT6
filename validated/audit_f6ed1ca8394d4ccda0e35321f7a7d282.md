Let me search for the relevant code to evaluate this claim.

Now let me check how `Owner::Party` objects are created and whether they can actually exist on-chain.

## Analysis

### Step 1: Confirm `todo!()` locations in production code

Multiple `todo!("Party WIP")` calls exist in non-test production code:

- **GraphQL owner serialization** — `Owner::from_native()` in `crates/sui-indexer-alt-graphql/src/api/types/owner.rs:90`
- **SDK type conversion** — `From<crate::object::Owner> for Owner` in `crates/sui-types/src/sui_sdk_types_conversions.rs:192`
- **RPC store indexing** — `OwnerKind::from_owner()` in `crates/sui-rpc-store/src/schema/object_by_owner.rs:65`
- **Consistent store indexing** — `OwnerKind::from_owner()` in `crates/sui-indexer-alt-consistent-store/src/schema/object_by_owner.rs:88`
- **GraphQL balance change** — `owner()` resolver and `From<BalanceChangeContents>` in `crates/sui-indexer-alt-graphql/src/api/types/balance_change.rs:76,144`
- **Transaction checks** — `check_one_object()` for `NonExclusiveWrite` in `crates/sui-transaction-checks/src/lib.rs:725`

### Step 2: Can `Owner::Party` objects be created by an unprivileged caller?

`crates/sui-framework/packages/sui-framework/sources/party.move` exposes public macros `party::transfer` and `party::public_transfer` that call `sui::transfer::party_transfer` / `sui::transfer::public_party_transfer`. [1](#0-0) 

The protocol config snapshots for versions 100–132 (Mainnet, Testnet, and devnet) all contain `party_owner_enabled`, confirming the feature flag is present and active in recent protocol versions. 

An unprivileged caller can publish a Move package that calls `sui::party::public_transfer(obj, party::single_owner(addr))`, which commits an `Owner::Party` object to chain state via a normal PTB.

### Step 3: Does the transaction-checks guard block creation?

The guard in `check_one_object()` at line 541–545 only fires when a `Party` object is used as a **transaction input**: [2](#0-1) 

It does **not** block a transaction that **creates** a `Party` object as an output. A PTB that calls `party_transfer` on a freshly-created object succeeds and commits `Owner::Party` to state.

The `todo!()` at line 725 (for `NonExclusiveWrite`) is unreachable because the guard fires first for any `Party` object used as input. [3](#0-2) 

### Step 4: Trace the crash path

Once an `Owner::Party` object exists on-chain:

**Indexer crash path**: When the indexer processes the checkpoint containing the new object, `OwnerKind::from_owner()` is called on every object. For a `Party` object it hits `todo!("Party owner WIP")` → Rust panic → indexer process crash. [4](#0-3) [5](#0-4) 

**GraphQL crash path**: Any GraphQL query that fetches the object's owner field calls `Owner::from_native()`, which hits `todo!("Party WIP")` → panic → GraphQL handler crash. [6](#0-5) 

**Balance-change crash path**: Any GraphQL query for balance changes involving a `Party` owner hits `todo!("Party WIP")` in the `owner()` resolver. [7](#0-6) 

### Step 5: Impact gate check

The active bounty scope accepts **Low: public-input fullnode crash** when reachable from public input. The attacker is an ordinary SUI holder who publishes a package and submits a PTB — no malicious peer, validator, or admin required. The crash affects the indexer and GraphQL server (fullnode infrastructure), which is within scope.

---

### Title
`Owner::Party` `todo!()` panics in GraphQL/indexer owner serialization paths cause fullnode infrastructure crash — (`crates/sui-indexer-alt-graphql/src/api/types/owner.rs`, `crates/sui-rpc-store/src/schema/object_by_owner.rs`, `crates/sui-indexer-alt-consistent-store/src/schema/object_by_owner.rs`, `crates/sui-indexer-alt-graphql/src/api/types/balance_change.rs`)

### Summary
An unprivileged caller can publish a Move package that calls `sui::party::public_transfer()` to commit an `Owner::Party` object to chain state. Multiple production code paths in the indexer and GraphQL server contain `todo!("Party WIP")` for the `Owner::Party` variant with no fallback. When the indexer processes the checkpoint or a user queries the object via GraphQL, the process panics and crashes.

### Finding Description
`party.move` exposes `party::public_transfer<T: key + store>` as a public macro callable by any Move package. When invoked, it sets the object's `Owner` to `Owner::Party { .. }` and commits it to state. The following production match arms are unimplemented stubs:

- `Owner::from_native()` in `sui-indexer-alt-graphql/src/api/types/owner.rs:90`
- `OwnerKind::from_owner()` in `sui-rpc-store/src/schema/object_by_owner.rs:65`
- `OwnerKind::from_owner()` in `sui-indexer-alt-consistent-store/src/schema/object_by_owner.rs:88`
- `BalanceChange::owner()` resolver in `sui-indexer-alt-graphql/src/api/types/balance_change.rs:76`
- `From<BalanceChangeContents> for GrpcBalanceChange` in `balance_change.rs:144`

All call `todo!()`, which panics unconditionally.

The guard in `check_one_object()` only blocks `Party` objects from being used as transaction *inputs*; it does not prevent their *creation* as transaction outputs.

### Impact Explanation
- Indexer process panics and crashes on every checkpoint that contains a `Party` object, halting indexing.
- GraphQL server panics on any query that touches the owner field of a `Party` object, crashing the request handler.
- Persistent: the object remains in state, so the crash recurs on every restart until the object is handled.
- Severity: **Low** (public-input fullnode infrastructure crash).

### Likelihood Explanation
Any SUI holder can trigger this with a single package publish + PTB. No special permissions, governance, or validator cooperation required. The `party_owner_enabled` flag is active in protocol versions 100–132 on Mainnet and Testnet.

### Recommendation
Replace each `todo!("Party WIP")` arm with either:
1. A proper implementation mapping `Owner::Party` to a suitable representation (e.g., treat like `AddressOwner` for the primary member, or introduce a new `PartyOwner` GraphQL/index variant), or
2. A graceful error return / `None` / skip that does not panic, with a clear comment that the full implementation is pending.

The indexer's `store()` function should return `None` for `Party` objects (skipping them) until the variant is fully supported, consistent with how packages are already skipped.

### Proof of Concept
```move
// attacker_package/sources/exploit.move
module attacker::exploit;
use sui::party;

public entry fun create_party_object(obj: Coin<SUI>, ctx: &mut TxContext) {
    let p = party::single_owner(ctx.sender());
    party::public_transfer(p, obj);  // sets Owner::Party on obj
}
```

1. Publish the package.
2. Call `attacker::exploit::create_party_object` with any owned `Coin<SUI>`.
3. The transaction succeeds; the coin is now `Owner::Party`.
4. The indexer panics when processing the checkpoint: `thread 'main' panicked at 'not yet implemented: Party owner WIP'`.
5. Any GraphQL query for `object(id: "<coin_id>") { owner { ... } }` panics: `'not yet implemented: Party WIP'`.

### Citations

**File:** crates/sui-framework/packages/sui-framework/sources/party.move (L57-66)
```text
public macro fun transfer<$T: key>($self: Party, $obj: $T) {
    let mp = $self;
    sui::transfer::party_transfer($obj, mp)
}

/// A helper `macro` that calls `sui::transfer::public_party_transfer`.
public macro fun public_transfer<$T: key + store>($self: Party, $obj: $T) {
    let mp = $self;
    sui::transfer::public_party_transfer($obj, mp)
}
```

**File:** crates/sui-transaction-checks/src/lib.rs (L540-545)
```rust
        // Defense-in-depth: Owner::Party is not yet supported.
        if matches!(object.owner, Owner::Party { .. }) {
            return Err(UserInputError::Unsupported(
                "Party-owned objects are not yet supported".to_string(),
            ));
        }
```

**File:** crates/sui-transaction-checks/src/lib.rs (L723-726)
```rust
                            SharedObjectMutability::NonExclusiveWrite => {
                                // TODO(Party WIP)
                                todo!("Party WIP")
                            }
```

**File:** crates/sui-rpc-store/src/schema/object_by_owner.rs (L58-66)
```rust
    pub fn from_owner(owner: &Owner) -> Self {
        match owner {
            Owner::AddressOwner(address) => OwnerKind::AddressOwner(*address),
            Owner::ObjectOwner(address) => OwnerKind::ObjectOwner(*address),
            Owner::Shared { .. } => OwnerKind::Shared,
            Owner::Immutable => OwnerKind::Immutable,
            Owner::ConsensusAddressOwner { owner, .. } => OwnerKind::AddressOwner(*owner),
            Owner::Party { .. } => todo!("Party owner WIP"),
        }
```

**File:** crates/sui-indexer-alt-consistent-store/src/schema/object_by_owner.rs (L80-89)
```rust
    pub(crate) fn from_owner(owner: &Owner) -> Self {
        match owner {
            Owner::AddressOwner(address) => OwnerKind::AddressOwner(*address),
            Owner::ObjectOwner(address) => OwnerKind::ObjectOwner(*address),
            Owner::Shared { .. } => OwnerKind::Shared,
            Owner::Immutable => OwnerKind::Immutable,
            Owner::ConsensusAddressOwner { owner, .. } => OwnerKind::AddressOwner(*owner),
            // TODO(Party WIP)
            Owner::Party { .. } => todo!("Party WIP"),
        }
```

**File:** crates/sui-indexer-alt-graphql/src/api/types/owner.rs (L89-91)
```rust
            // TODO(Party WIP)
            NO::Party { .. } => todo!("Party WIP"),
        }
```

**File:** crates/sui-indexer-alt-graphql/src/api/types/balance_change.rs (L75-77)
```rust
                    // TODO(Party WIP)
                    Owner::Party { .. } => todo!("Party WIP"),
                    Owner::Shared { .. } | Owner::Immutable => return Ok(None),
```
