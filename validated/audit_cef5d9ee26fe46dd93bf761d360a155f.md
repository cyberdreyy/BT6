[1](#0-0)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/account/account.move (L1-28)
```text
module aptos_framework::account {
    use std::bcs;
    use std::error;
    use std::features;
    use std::hash;
    use std::option::{Self, Option};
    use std::signer;
    use std::vector;
    use aptos_framework::chain_id;
    use aptos_framework::create_signer::create_signer;
    use aptos_framework::event::{Self, EventHandle};
    use aptos_framework::guid;
    use aptos_framework::system_addresses;
    use aptos_std::ed25519;
    use aptos_std::from_bcs;
    use aptos_std::multi_ed25519;
    use aptos_std::single_key;
    use aptos_std::multi_key;
    use aptos_std::table::{Self, Table};
    use aptos_std::type_info::{Self, TypeInfo};

    friend aptos_framework::aptos_account;
    friend aptos_framework::coin;
    friend aptos_framework::genesis;
    friend aptos_framework::keyless_account;
    friend aptos_framework::multisig_account;
    friend aptos_framework::resource_account;
    friend aptos_framework::transaction_validation;
```
