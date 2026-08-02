[1](#0-0) [2](#0-1)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L451-474)
```text
    public fun total_accumulated_rewards(vesting_contract_address: address): u64 acquires VestingContract {
        assert_active_vesting_contract(vesting_contract_address);

        let vesting_contract = borrow_global<VestingContract>(vesting_contract_address);
        let (total_active_stake, _, commission_amount) =
            staking_contract::staking_contract_amounts(vesting_contract_address, vesting_contract.staking.operator);
        total_active_stake - vesting_contract.remaining_grant - commission_amount
    }

    #[view]
    /// Return the accumulated rewards that have not been distributed to the provided shareholder. Caller can also pass
    /// the beneficiary address instead of shareholder address.
    ///
    /// This errors out if the vesting contract with the provided address doesn't exist.
    public fun accumulated_rewards(
        vesting_contract_address: address, shareholder_or_beneficiary: address): u64 acquires VestingContract {
        assert_active_vesting_contract(vesting_contract_address);

        let total_accumulated_rewards = total_accumulated_rewards(vesting_contract_address);
        let shareholder = shareholder(vesting_contract_address, shareholder_or_beneficiary);
        let vesting_contract = borrow_global<VestingContract>(vesting_contract_address);
        let shares = vesting_contract.grant_pool.shares(shareholder);
        vesting_contract.grant_pool.shares_to_amount_with_total_coins(shares, total_accumulated_rewards)
    }
```
