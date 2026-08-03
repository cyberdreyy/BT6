[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/stake.move (L1866-1894)
```text
        let fee_pending_inactive = 0;
        let fee_active = 0;
        let fee_limit =
            if (exists<TransactionFeeConfig>(@aptos_framework)) {
                let TransactionFeeConfig::V0 { max_fee_octa_allowed_per_epoch_per_pool } =
                    borrow_global<TransactionFeeConfig>(@aptos_framework);
                *max_fee_octa_allowed_per_epoch_per_pool
            } else {
                MAX_U64 as u64
            };

        if (exists<PendingTransactionFee>(@aptos_framework)) {
            let pending_fee_by_validator =
                &mut borrow_global_mut<PendingTransactionFee>(@aptos_framework).pending_fee_by_validator;
            if (pending_fee_by_validator.contains(&validator_index)) {
                let fee_octa = pending_fee_by_validator.remove(&validator_index).read();
                if (fee_octa > fee_limit) {
                    fee_octa = fee_limit;
                };
                let stake_active = (coin::value(&stake_pool.active) as u128);
                let stake_pending_inactive =
                    (coin::value(&stake_pool.pending_inactive) as u128);
                fee_pending_inactive =
                    (
                        ((fee_octa as u128) * stake_pending_inactive
                            / (stake_active + stake_pending_inactive)) as u64
                    );
                fee_active = fee_octa - fee_pending_inactive;
            }
```

**File:** aptos-move/framework/aptos-framework/sources/stake.move (L1946-1961)
```text
        let rewards_amount = rewards_active + rewards_pending_inactive;
        // Pending active stake can now be active.
        coin::merge(
            &mut stake_pool.active, coin::extract_all(&mut stake_pool.pending_active)
        );

        // Pending inactive stake is only fully unlocked and moved into inactive if the current lockup cycle has expired
        let current_lockup_expiration = stake_pool.locked_until_secs;
        if (get_reconfig_start_time_secs() >= current_lockup_expiration) {
            coin::merge(
                &mut stake_pool.inactive,
                coin::extract_all(&mut stake_pool.pending_inactive)
            );
        };

        event::emit(DistributeRewards { pool_address, rewards_amount });
```

**File:** aptos-move/framework/aptos-framework/sources/stake.move (L1973-1998)
```text
    /// Calculate the rewards amount.
    fun calculate_rewards_amount(
        stake_amount: u64,
        num_successful_proposals: u64,
        num_total_proposals: u64,
        rewards_rate: u64,
        rewards_rate_denominator: u64
    ): u64 {
        spec {
            // The following condition must hold because
            // (1) num_successful_proposals <= num_total_proposals, and
            // (2) `num_total_proposals` cannot be larger than 86400, the maximum number of proposals
            //     in a day (1 proposal per second), and `num_total_proposals` is reset to 0 every epoch.
            assume num_successful_proposals * MAX_REWARDS_RATE <= MAX_U64;
        };
        // The rewards amount is equal to (stake amount * rewards rate * performance multiplier).
        // We do multiplication in u128 before division to avoid the overflow and minimize the rounding error.
        let rewards_numerator =
            (stake_amount as u128) * (rewards_rate as u128)
                * (num_successful_proposals as u128);
        let rewards_denominator =
            (rewards_rate_denominator as u128) * (num_total_proposals as u128);
        if (rewards_denominator > 0) {
            ((rewards_numerator / rewards_denominator) as u64)
        } else { 0 }
    }
```
