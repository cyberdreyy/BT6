[1](#0-0) [2](#0-1)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L33-35)
```text
/// 4. Admin can call terminate_vesting_contract to terminate the vesting. This would first finish any distribution but
/// will prevent any further rewards or vesting distributions from being created. Once the locked up stake becomes
/// withdrawable, admin can call admin_withdraw to withdraw all funds to the vesting contract's withdrawal address.
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L73-74)
```text
    /// The signer is not the admin of the vesting contract.
    const ENOT_ADMIN: u64 = 7;
```
