No vulnerability found for this question.

**Reasoning:**

The premise assumes an attacker can obtain another owner's `OwnerCapability` via a "leaked/duplicate reference to... performed against the wrong signer," but the actual code in `stake.move` does not permit this:

- `extract_owner_cap` only removes the `OwnerCapability` resource stored **at the calling signer's own address** — `move_from<OwnerCapability>(owner_address)` where `owner_address = signer::address_of(owner)` [1](#0-0) . There is no function taking an arbitrary target address to extract a capability belonging to someone else; the resource can only be extracted by the account it is stored under.
- `deposit_owner_cap` only lets a signer place an `OwnerCapability` value **they already hold** into their own account, and aborts if the destination already has one [2](#0-1) . It does not fetch or transfer a capability from any other account — the caller must already possess the resource value as a matter of Move's type/resource system, which only happens if the true owner explicitly moved it to them (e.g., handed off the returned `OwnerCapability` value from their own `extract_owner_cap` call).
- `reactivate_stake` itself requires `assert_owner_cap_exists(owner_address)` and then reads `borrow_global<OwnerCapability>(owner_address).pool_address` to determine which pool to act on [3](#0-2) . The capability is intrinsically bound to a specific `pool_address` set at `initialize_owner`/`move_to` time [4](#0-3) , so even an attacker's own legitimately-held capability can only reactivate stake for the pool it references, never a victim's pool.

The formal spec confirms this design intent: `extract_owner_cap` aborts if the capability doesn't exist at the caller's own address and guarantees it no longer exists there after, while `deposit_owner_cap` aborts if the destination already holds one [5](#0-4) .

There is no code path by which an unprivileged, non-owner signer can acquire another account's `OwnerCapability` "against the wrong signer" — Move's resource-ownership model (the capability is a `key, store` resource that can only be moved by/to the address holding it, with no cross-account extraction API) already blocks the described transfer. The question's own proof idea ("verifying ... cannot be invoked ... without already possessing the OwnerCapability resource") describes exactly the behavior that already exists in the code; it is not a vulnerability to be discovered, it's the existing (correct) invariant.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/stake.move (L806-806)
```text
        move_to(owner, OwnerCapability { pool_address: owner_address });
```

**File:** aptos-move/framework/aptos-framework/sources/stake.move (L809-814)
```text
    /// Extract and return owner capability from the signing account.
    public fun extract_owner_cap(owner: &signer): OwnerCapability acquires OwnerCapability {
        let owner_address = signer::address_of(owner);
        assert_owner_cap_exists(owner_address);
        move_from<OwnerCapability>(owner_address)
    }
```

**File:** aptos-move/framework/aptos-framework/sources/stake.move (L816-826)
```text
    /// Deposit `owner_cap` into `account`. This requires `account` to not already have ownership of another
    /// staking pool.
    public fun deposit_owner_cap(
        owner: &signer, owner_cap: OwnerCapability
    ) {
        assert!(
            !exists<OwnerCapability>(signer::address_of(owner)),
            error::not_found(EOWNER_CAP_ALREADY_EXISTS)
        );
        move_to(owner, owner_cap);
    }
```

**File:** aptos-move/framework/aptos-framework/sources/stake.move (L929-936)
```text
    /// Move `amount` of coins from pending_inactive to active.
    public entry fun reactivate_stake(owner: &signer, amount: u64) acquires OwnerCapability, StakePool {
        assert_reconfig_not_in_progress();
        let owner_address = signer::address_of(owner);
        assert_owner_cap_exists(owner_address);
        let ownership_cap = borrow_global<OwnerCapability>(owner_address);
        reactivate_stake_with_cap(ownership_cap, amount);
    }
```

**File:** aptos-move/framework/aptos-framework/sources/stake.spec.move (L368-381)
```text
    spec extract_owner_cap(owner: &signer): OwnerCapability {
        // TODO: set because of timeout (property proved)
        pragma verify_duration_estimate = 300;
        let owner_address = signer::address_of(owner);
        aborts_if !exists<OwnerCapability>(owner_address);
        ensures !exists<OwnerCapability>(owner_address);
    }

    spec deposit_owner_cap(owner: &signer, owner_cap: OwnerCapability) {
        let owner_address = signer::address_of(owner);
        aborts_if exists<OwnerCapability>(owner_address);
        ensures exists<OwnerCapability>(owner_address);
        ensures global<OwnerCapability>(owner_address) == owner_cap;
    }
```
