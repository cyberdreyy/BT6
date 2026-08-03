[1](#0-0)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L99-107)
```text
    /// Vesting contract states.
    /// Vesting contract is active and distributions can be made.
    const VESTING_POOL_ACTIVE: u64 = 1;
    /// Vesting contract has been terminated and all funds have been released back to the withdrawal address.
    const VESTING_POOL_TERMINATED: u64 = 2;

    /// Roles that can manage certain aspects of the vesting account beyond the main admin.
    const ROLE_BENEFICIARY_RESETTER: vector<u8> = b"ROLE_BENEFICIARY_RESETTER";

```
