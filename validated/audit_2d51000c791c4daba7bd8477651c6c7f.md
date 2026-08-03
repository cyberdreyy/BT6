[1](#0-0)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L73-90)
```text
    /// The signer is not the admin of the vesting contract.
    const ENOT_ADMIN: u64 = 7;
    /// Vesting contract needs to be in active state.
    const EVESTING_CONTRACT_NOT_ACTIVE: u64 = 8;
    /// Admin can only withdraw from an inactive (paused or terminated) vesting contract.
    const EVESTING_CONTRACT_STILL_ACTIVE: u64 = 9;
    /// No vesting contract found at provided address.
    const EVESTING_CONTRACT_NOT_FOUND: u64 = 10;
    /// Cannot terminate the vesting contract with pending active stake. Need to wait until next epoch.
    const EPENDING_STAKE_FOUND: u64 = 11;
    /// Grant amount cannot be 0.
    const EZERO_GRANT: u64 = 12;
    /// Vesting account has no other management roles beside admin.
    const EVESTING_ACCOUNT_HAS_NO_ROLES: u64 = 13;
    /// The vesting account has no such management role.
    const EROLE_NOT_FOUND: u64 = 14;
    /// Account is not admin or does not have the required role to take this action.
    const EPERMISSION_DENIED: u64 = 15;
```
