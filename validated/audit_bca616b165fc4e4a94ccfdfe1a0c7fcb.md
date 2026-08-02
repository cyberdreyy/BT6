[1](#0-0) [2](#0-1)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L80-94)
```text
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
    /// Zero items were provided to a *_many function.
    const EVEC_EMPTY_FOR_MANY_FUNCTION: u64 = 16;
    /// The permissioned signer feature has been removed.
    const EPERMISSIONED_SIGNER_REMOVED: u64 = 18;
```
