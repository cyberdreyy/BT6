[1](#0-0) [2](#0-1)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L56-65)
```text
    /// Staking contracts can't be merged.
    const ECANT_MERGE_STAKING_CONTRACTS: u64 = 5;
    /// The staking contract already exists and cannot be re-created.
    const ESTAKING_CONTRACT_ALREADY_EXISTS: u64 = 6;
    /// Not enough active stake to withdraw. Some stake might still pending and will be active in the next epoch.
    const EINSUFFICIENT_ACTIVE_STAKE_TO_WITHDRAW: u64 = 7;
    /// Caller must be either the staker, operator, or beneficiary.
    const ENOT_STAKER_OR_OPERATOR_OR_BENEFICIARY: u64 = 8;
    /// Changing beneficiaries for operators is not supported.
    const EOPERATOR_BENEFICIARY_CHANGE_NOT_SUPPORTED: u64 = 9;
```
