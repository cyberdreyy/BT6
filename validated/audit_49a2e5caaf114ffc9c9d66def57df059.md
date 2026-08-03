[1](#0-0) [2](#0-1)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L31-33)
```text
/// coins) of that shareholder to the beneficiary account. By defalt, if a beneficiary is not set, the distributions are
/// send directly to the shareholder account.
/// 4. Admin can call terminate_vesting_contract to terminate the vesting. This would first finish any distribution but
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L47-55)
```text
    use aptos_framework::account::{Self, SignerCapability, new_event_handle};
    use aptos_framework::aptos_account::{Self, assert_account_is_registered_for_apt};
    use aptos_framework::aptos_coin::AptosCoin;
    use aptos_framework::coin::{Self, Coin};
    use aptos_framework::event::{EventHandle, emit};
    use aptos_framework::stake;
    use aptos_framework::staking_contract;
    use aptos_framework::system_addresses;
    use aptos_framework::timestamp;
```
