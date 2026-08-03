[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/resource_account.move (L14-17)
```text
///  4. In the liquidity pool module's `init_module` function, call `retrieve_resource_account_cap`
///     which will retrieve the `signer_cap` and rotate the resource account's authentication key to
///     `0x0`, effectively locking it off.
///  5. When adding a new coin, the liquidity pool will load the capability and hence the `signer` to
```

**File:** aptos-move/framework/aptos-framework/sources/resource_account.move (L79-81)
```text
    struct Container has key {
        store: SimpleMap<address, account::SignerCapability>
    }
```

**File:** aptos-move/framework/aptos-framework/sources/resource_account.move (L176-180)
```text
        let resource_addr = signer::address_of(resource);
        let (resource_signer_cap, empty_container) = {
            let container = borrow_global_mut<Container>(source_addr);
            assert!(
                container.store.contains_key(&resource_addr),
```
