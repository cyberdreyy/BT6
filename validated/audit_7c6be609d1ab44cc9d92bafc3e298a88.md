[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L89-90)
```text
    /// Account is not admin or does not have the required role to take this action.
    const EPERMISSION_DENIED: u64 = 15;
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L105-106)
```text
    /// Roles that can manage certain aspects of the vesting account beyond the main admin.
    const ROLE_BENEFICIARY_RESETTER: vector<u8> = b"ROLE_BENEFICIARY_RESETTER";
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L136-150)
```text
    struct VestingContract has key {
        state: u64,
        admin: address,
        grant_pool: Pool,
        beneficiaries: SimpleMap<address, address>,
        vesting_schedule: VestingSchedule,
        // Withdrawal address where all funds would be released back to if the admin ends the vesting for a specific
        // account or terminates the entire vesting contract.
        withdrawal_address: address,
        staking: StakingInfo,
        // Remaining amount in the grant. For calculating accumulated rewards.
        remaining_grant: u64,
        // Used to control staking.
        signer_cap: SignerCapability,

```
