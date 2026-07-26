The `todo!()` at line 65 is real, the call chain is traceable, and `Owner::Party` is a live enum variant. This is a valid vulnerability.

---

### Title
`todo!()` panic in `OwnerKind::from_owner` crashes the `sui-rpc-store` indexer on any Party-owned object — (`crates/sui-rpc-store/src/schema/object_by_owner.rs`)

---

### Summary

Any unprivileged user can submit a transaction that calls `sui::transfer::party_transfer` (exposed via `sui::party::transfer` macro in `party.move`), producing a live Move object with `Owner::Party`. When the `sui-rpc-store` sequential indexer processes the resulting checkpoint, it calls `object_by_owner::store()` → `OwnerKind::from_owner()`, which hits an unconditional `todo!("Party owner WIP")` and panics, crashing the indexer process.

---

### Finding Description

**Step 1 — Attacker creates a Party-owned object.**

`party.move` exposes `single_owner` and the `transfer` / `public_transfer` macros as public API: [1](#0-0) 

Any user can call `sui::party::single_owner(addr)` and then `sui::party::transfer(party, obj)`, which calls `sui::transfer::party_transfer` and records the object with `Owner::Party` in the effects.

**Step 2 — The indexer processes the checkpoint.**

`crates/sui-rpc-store/src/indexer/object_by_owner.rs` iterates every input and output object in each checkpoint and calls `object_by_owner::store()` on each one: [2](#0-1) 

**Step 3 — `store()` calls `from_owner()` with no Party guard.**

`schema/object_by_owner.rs::store()` only short-circuits for non-Move objects (packages). A Party-owned Move object has a valid `StructTag`, so `object.type_()` returns `Some(...)` and execution continues to `OwnerKind::from_owner(object.owner())`: [3](#0-2) 

**Step 4 — `todo!()` panics.**

`from_owner` has no implemented arm for `Owner::Party`: [4](#0-3) 

In Rust, `todo!()` expands to `panic!("not yet implemented: Party owner WIP")`. The indexer thread panics and the process crashes.

---

### Impact Explanation

The `sui-rpc-store` indexer is the RPC-layer store for fullnodes. A crash here causes the fullnode's RPC service to become unavailable. Because the panic is triggered by a committed checkpoint entry (the Party-owned object persists in chain state), every restart of the indexer will re-process the same checkpoint and panic again, making the crash **permanent** until the software is patched. This satisfies the bounty's "fullnode crash reachable from public input" criterion (Low) and potentially "network instability" (Medium) if the indexer is part of the critical RPC path on unmodified software.

---

### Likelihood Explanation

Exploitability is trivial. Any SUI holder can publish a package, create an object, call `sui::party::single_owner` + `sui::party::transfer`, and submit the transaction. No special privileges, leaked keys, or malicious peers are required. The crash is deterministic and repeatable.

---

### Recommendation

Replace the `todo!()` with a concrete mapping. The most conservative fix is to treat `Owner::Party` like `Owner::AddressOwner` (collapsing to the single-owner address when the party is a single-owner party) or to return `None` from `store()` early when the owner is `Owner::Party`, skipping Party-owned objects from this index until the design is finalised:

```rust
Owner::Party { .. } => return None,  // in store(), before calling from_owner
```

Alternatively, add a `Party(SuiAddress)` variant to `OwnerKind` and implement it fully.

---

### Proof of Concept

1. Publish a package with a `key`-only struct `Obj`.
2. In a PTB: create an `Obj`, call `sui::party::single_owner(attacker_addr)` to get a `Party`, call `sui::party::transfer(party, obj)`.
3. Submit the transaction. It commits and the object appears in the checkpoint with `Owner::Party`.
4. The `sui-rpc-store` indexer processes the checkpoint, calls `object_by_owner::store()` on the output object, reaches `OwnerKind::from_owner`, hits `todo!("Party owner WIP")`, and panics.
5. The indexer process exits. Every subsequent restart re-processes the same checkpoint and crashes again.

### Citations

**File:** crates/sui-framework/packages/sui-framework/sources/party.move (L50-66)
```text
public fun single_owner(owner: address): Party {
    let mut mp = empty();
    mp.set_permissions(owner, Permissions(ALL_PERMISSIONS));
    mp
}

/// A helper `macro` that calls `sui::transfer::party_transfer`.
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

**File:** crates/sui-rpc-store/src/indexer/object_by_owner.rs (L50-57)
```rust
        for (_, (input, _)) in checkpoint_input_objects(checkpoint)? {
            if let Some((key, _)) = object_by_owner::store(input) {
                rows.push(Row::Delete(key));
            }
        }
        for (_, (output, _)) in checkpoint_output_objects(checkpoint)? {
            if let Some((key, version)) = object_by_owner::store(output) {
                rows.push(Row::Put(key, version));
```

**File:** crates/sui-rpc-store/src/schema/object_by_owner.rs (L58-67)
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
    }
```

**File:** crates/sui-rpc-store/src/schema/object_by_owner.rs (L217-228)
```rust
pub fn store(object: &Object) -> Option<(Key, U64Varint)> {
    let type_: StructTag = object.type_()?.clone().into();
    Some((
        Key {
            kind: OwnerKind::from_owner(object.owner()),
            type_,
            inverted_balance: object.as_coin_maybe().map(|coin| !coin.balance.value()),
            object_id: object.id(),
        },
        U64Varint(object.version().value()),
    ))
}
```
