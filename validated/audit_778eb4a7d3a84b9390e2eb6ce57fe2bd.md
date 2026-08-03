[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/aptos_account.move (L181-185)
```text
        assert!(
            coin::is_account_registered<AptosCoin>(addr),
            error::not_found(EACCOUNT_NOT_REGISTERED_FOR_APT)
        );
    }
```

**File:** aptos-move/framework/aptos-framework/sources/aptos_account.move (L233-235)
```text
    public(friend) fun register_apt(account_signer: &signer) {
        ensure_primary_fungible_store_exists(signer::address_of(account_signer));
    }
```

**File:** aptos-move/framework/aptos-framework/sources/aptos_account.move (L280-290)
```text
    /// Mint into APT Primary FungibleStore for gas refund
    public(friend) fun mint_to_fungible_store_for_gas(
        ref: &MintRef, account: address, amount: u64
    ) {
        // Skip minting if amount is zero. This shouldn't error out as it's called as part of gas refund.
        if (amount != 0) {
            let store_addr = ensure_primary_fungible_store_exists(account);
            let fa = fungible_asset::mint(ref, amount);
            fungible_asset::unchecked_deposit_with_no_events(store_addr, fa);
        };
    }
```

**File:** aptos-move/framework/aptos-framework/sources/aptos_account.move (L292-302)
```text
    /// Ensure that APT Primary FungibleStore exists (and create if it doesn't)
    inline fun ensure_primary_fungible_store_exists(owner: address): address {
        let store_addr = primary_fungible_store_address(owner);
        if (fungible_asset::store_exists(store_addr)) {
            store_addr
        } else {
            primary_fungible_store::create_primary_store(
                    owner, object::address_to_object<Metadata>(@aptos_fungible_asset)
                ).object_address()
        }
    }
```
