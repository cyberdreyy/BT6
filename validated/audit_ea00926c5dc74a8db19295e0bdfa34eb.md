[1](#0-0) [2](#0-1)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L85-90)
```text
    /// Vesting account has no other management roles beside admin.
    const EVESTING_ACCOUNT_HAS_NO_ROLES: u64 = 13;
    /// The vesting account has no such management role.
    const EROLE_NOT_FOUND: u64 = 14;
    /// Account is not admin or does not have the required role to take this action.
    const EPERMISSION_DENIED: u64 = 15;
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L105-106)
```text
    /// Roles that can manage certain aspects of the vesting account beyond the main admin.
    const ROLE_BENEFICIARY_RESETTER: vector<u8> = b"ROLE_BENEFICIARY_RESETTER";
```
