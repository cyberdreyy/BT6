[1](#0-0)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L61-80)
```text
    const EINSUFFICIENT_ACTIVE_STAKE_TO_WITHDRAW: u64 = 7;
    /// Caller must be either the staker, operator, or beneficiary.
    const ENOT_STAKER_OR_OPERATOR_OR_BENEFICIARY: u64 = 8;
    /// Changing beneficiaries for operators is not supported.
    const EOPERATOR_BENEFICIARY_CHANGE_NOT_SUPPORTED: u64 = 9;

    /// Maximum number of distributions a stake pool can support.
    const MAXIMUM_PENDING_DISTRIBUTIONS: u64 = 20;

    #[resource_group(scope = module_)]
    struct StakingGroupContainer {}

    struct StakingContract has store {
        // Recorded principal after the last commission distribution.
        // This is only used to calculate the commission the operator should be receiving.
        principal: u64,
        pool_address: address,
        // The stake pool's owner capability. This can be used to control funds in the stake pool.
        owner_cap: OwnerCapability,
        commission_percentage: u64,
```
