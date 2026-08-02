## Title
`get_add_stake_fee` computes the anti-frontrunning fee using only `staking_config` reward-rate, ignoring the transaction-fee redistribution (`fee_active`) that is also uniformly applied to the `active_shares` pool, letting new depositors capture a windfall from already-accrued fee value that belongs to existing delegators - (`aptos-move/framework/aptos-framework/sources/delegation_pool.move`)

### Summary
`delegation_pool::get_add_stake_fee` derives the anti-frontrunning fee purely from `staking_config::get_reward_rate` and the operator commission percentage. [1](#0-0) 
That fee is meant to make an `add_stake` depositor economically indifferent to being pending_active for one epoch: `(amount - fee) * (1 + effective_rate) == amount`, so the depositor gets back exactly their principal with no share of the epoch's active-stake appreciation.

However, `stake::update_stake_pool` also mints an independent, unbounded `fee_active` amount from `PendingTransactionFee`/`TransactionFeeConfig` (transaction-fee redistribution, gated by `is_distribute_transaction_fee_enabled`), and merges it into `stake_pool.active` in the very same reconfiguration that merges `pending_active` into `active`. [2](#0-1) 
Because the delegation pool models "active" appreciation as a single uniform per-share ratio applied to `pool.active_shares` in `synchronize_delegation_pool`, any `fee_active` windfall inflates the *actual* appreciation ratio for that epoch beyond what `get_add_stake_fee`'s reward-rate-only estimate assumed. [3](#0-2) 

### Finding Description
`add_stake` charges `get_add_stake_fee(pool_address, amount)`, buys the depositor `amount - fee` shares immediately, and parks the `fee` itself as shares owned by `NULL_SHAREHOLDER` to be released back into the pool at the next sync. [4](#0-3) 

At the next `synchronize_delegation_pool` call (triggered automatically on the next reconfiguration-aware pool interaction), `calculate_stake_pool_drift` compares the real `active` balance from `stake::get_stake` (which by then already includes: old active + `rewards_active` + `fee_active` + the merged-in former `pending_active` principal) against `pool.active_shares.total_coins()` from before the sync. The `NULL_SHAREHOLDER`'s fee shares are redeemed *before* the uniform appreciation (`update_total_coins`) is applied, so the fee's value is meant to offset exactly the extra appreciation the new depositor's shares would otherwise pick up for free. [5](#0-4) 

This offset is only calibrated for the `staking_config` reward rate. `stake::update_stake_pool` computes `fee_active`/`fee_pending_inactive` from `PendingTransactionFee` (populated over the epoch by `record_fee`, invoked by the VM, presumably driven by `block_epilogue`'s `FeeDistribution`) completely independently of `rewards_rate`, and merges it into `stake_pool.active` in the same reconfiguration. [6](#0-5) [7](#0-6) [8](#0-7) 

Because both `rewards_active` and `fee_active` get folded into the *same* single `active` balance before the uniform per-share ratio is applied, and `get_add_stake_fee` only neutralizes the `rewards_active`-sized portion, whenever `fee_active > 0` for the epoch, the new depositor's net-of-fee principal appreciates by more than `1 + effective_rewards_rate`. The delta comes directly out of the pool value that should accrue only to shareholders who were actually active during the fee-generating epoch (existing delegators and, proportionally, the operator's commission calculation, which is itself computed off the same inflated `active - pool_active` delta and is unaffected by this specific skew but the delegator-vs-delegator skew is real).

### Impact Explanation
This falls under "Operator commission, beneficiary payout, or share-accounting corruption that credits the wrong account" — specifically delegator-vs-delegator share-accounting corruption: a new, unprivileged depositor calling `add_stake` captures part of the `fee_active` value that was earned by, and rightfully belongs to, delegators who were active in the pool for the whole epoch. The magnitude scales with the size of transaction-fee redistribution to that validator's pool for the epoch, which is attacker-observable (public mempool/gas activity) and can be arbitrarily large relative to the fixed staking `rewards_rate`, since `fee_active` is capped only by `TransactionFeeConfig.max_fee_octa_allowed_per_epoch_per_pool` (default `MAX_U64`). [9](#0-8) 

### Likelihood Explanation
Exploitation requires no privileged role — any address can call `delegation_pool::add_stake` on a public pool. It does require (a) the `is_distribute_transaction_fee_enabled` feature to be active and (b) a meaningfully large `fee_active` accrued for the target pool during the current epoch, both of which are external/on-chain-observable conditions rather than attacker-controlled secrets, so likelihood depends on how significant transaction-fee redistribution is in practice on the deployed network.

### Recommendation
`get_add_stake_fee` should account for the pool's currently accrued (but not yet distributed) `PendingTransactionFee` amount for that validator index, in addition to `staking_config`'s reward rate, when estimating the expected epoch-end appreciation ratio; alternatively, `fee_active` distribution should be excluded from uniform share-price appreciation for principal added in the same epoch (e.g., by tracking `fee_active` separately from `rewards_active` and using it to top up `NULL_SHAREHOLDER`'s effective fee requirement dynamically at sync time rather than only at `add_stake` time).

### Proof of Concept
1. Enable `is_distribute_transaction_fee_enabled` and register `PendingTransactionFee` (as in `stake.move`'s `test_transaction_fee_limit`). [10](#0-9) 
2. Have the VM record a large `fee_octa` for `pool_address` via `record_fee` during the current epoch (simulating heavy `block_epilogue` `FeeDistribution`). [7](#0-6) 
3. Immediately, in the same epoch, call `delegation_pool::add_stake(depositor, pool_address, amount)`. Note `get_add_stake_fee` computed before the transfer uses only `staking_config::get_reward_rate`, unaware of the pending fee. [1](#0-0) 
4. End the epoch to trigger `on_new_epoch` → `update_stake_pool`, which mints `fee_active` into `active` and merges `pending_active` into `active` in the same call. [11](#0-10) 
5. Call `synchronize_delegation_pool(pool_address)` and read `active_shares.balance(depositor)` via `get_stake`: it will exceed `amount` by more than the reward-rate-based excess the fee was designed to neutralize (as seen in the reference test `test_add_stake_single`, where without any `fee_active`, the fee exactly returns the depositor to `amount`). [12](#0-11)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L695-709)
```text
    public fun get_add_stake_fee(
        pool_address: address,
        amount: u64
    ): u64 acquires DelegationPool, NextCommissionPercentage {
        if (stake::is_current_epoch_validator(pool_address)) {
            let (rewards_rate, rewards_rate_denominator) = staking_config::get_reward_rate(&staking_config::get());
            if (rewards_rate_denominator > 0) {
                assert_delegation_pool_exists(pool_address);

                rewards_rate *= (MAX_FEE - operator_commission_percentage(pool_address));
                rewards_rate_denominator *= MAX_FEE;
                ((((amount as u128) * (rewards_rate as u128)) / ((rewards_rate as u128) + (rewards_rate_denominator as u128))) as u64)
            } else { 0 }
        } else { 0 }
    }
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1494-1521)
```text
        // fee to be charged for adding `amount` stake on this delegation pool at this epoch
        let add_stake_fee = get_add_stake_fee(pool_address, amount);

        let pool = borrow_global_mut<DelegationPool>(pool_address);

        // stake the entire amount to the stake pool
        aptos_account::transfer(delegator, pool_address, amount);
        stake::add_stake(&retrieve_stake_pool_owner(pool), amount);

        // but buy shares for delegator just for the remaining amount after fee
        buy_in_active_shares(pool, delegator_address, amount - add_stake_fee);
        assert_min_active_balance(pool, delegator_address);

        // grant temporary ownership over `add_stake` fees to a separate shareholder in order to:
        // - not mistake them for rewards to pay the operator from
        // - distribute them together with the `active` rewards when this epoch ends
        // in order to appreciate all shares on the active pool atomically
        buy_in_active_shares(pool, NULL_SHAREHOLDER, add_stake_fee);

        event::emit(
            AddStake {
                pool_address,
                delegator_address,
                amount_added: amount,
                add_stake_fee,
            },
        );
    }
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1928-1950)
```text
        ) = calculate_stake_pool_drift(pool);

        // zero `pending_active` stake indicates that either there are no `add_stake` fees or
        // previous epoch has ended and should release the shares owning the existing fees
        let (_, _, pending_active, _) = stake::get_stake(pool_address);
        if (pending_active == 0) {
            // renounce ownership over the `add_stake` fees by redeeming all shares of
            // the special shareholder, implicitly their equivalent coins, out of the active shares pool
            redeem_active_shares(pool, NULL_SHAREHOLDER, MAX_U64);
        };

        // distribute rewards remaining after commission, to delegators (to already existing shares)
        // before buying shares for the operator for its entire commission fee
        // otherwise, operator's new shares would additionally appreciate from rewards it does not own

        // update total coins accumulated by `active` + `pending_active` shares
        // redeemed `add_stake` fees are restored and distributed to the rest of the pool as rewards
        pool.active_shares.update_total_coins(active - commission_active);
        // update total coins accumulated by `pending_inactive` shares at current observed lockup cycle
        pending_inactive_shares_pool_mut(pool).update_total_coins(pending_inactive - commission_pending_inactive);

        // reward operator its commission out of uncommitted active rewards (`add_stake` fees already excluded)
        buy_in_active_shares(pool, beneficiary_for_operator(stake::get_operator(pool_address)), commission_active);
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L2601-2627)
```text
        // add 250 coins being pending_active until next epoch
        stake::mint(validator, 250 * ONE_APT);
        add_stake(validator, pool_address, 250 * ONE_APT);

        let fee1 = get_add_stake_fee(pool_address, 250 * ONE_APT);
        assert_delegation(validator_address, pool_address, 1500 * ONE_APT - fee1, 0, 0);
        // check `add_stake` fee has been transferred to the null shareholder
        assert_delegation(NULL_SHAREHOLDER, pool_address, fee1, 0, 0);
        stake::assert_stake_pool(pool_address, 1250 * ONE_APT, 0, 250 * ONE_APT, 0);

        // add 100 additional coins being pending_active until next epoch
        stake::mint(validator, 100 * ONE_APT);
        add_stake(validator, pool_address, 100 * ONE_APT);

        let fee2 = get_add_stake_fee(pool_address, 100 * ONE_APT);
        assert_delegation(validator_address, pool_address, 1600 * ONE_APT - fee1 - fee2, 0, 0);
        // check `add_stake` fee has been transferred to the null shareholder
        assert_delegation(NULL_SHAREHOLDER, pool_address, fee1 + fee2, 0, 0);
        stake::assert_stake_pool(pool_address, 1250 * ONE_APT, 0, 350 * ONE_APT, 0);

        end_aptos_epoch();
        // delegator got its `add_stake` fees back + 1250 * 1% * (100% - 0%) active rewards
        assert_delegation(validator_address, pool_address, 161250000000, 0, 0);
        stake::assert_stake_pool(pool_address, 161250000000, 0, 0, 0);

        // check that shares of null shareholder have been released
        assert_delegation(NULL_SHAREHOLDER, pool_address, 0, 0, 0);
```

**File:** aptos-move/framework/aptos-framework/sources/stake.move (L671-694)
```text
    public(friend) fun record_fee(
        vm: &signer,
        fee_distribution_validator_indices: vector<u64>,
        fee_amounts_octa: vector<u64>
    ) acquires PendingTransactionFee {
        // Operational constraint: can only be invoked by the VM.
        system_addresses::assert_vm(vm);

        assert!(
            fee_distribution_validator_indices.length() == fee_amounts_octa.length()
        );

        let num_validators_to_distribute = fee_distribution_validator_indices.length();
        let pending_fee = borrow_global_mut<PendingTransactionFee>(@aptos_framework);
        let i = 0;
        while (i < num_validators_to_distribute) {
            let validator_index = fee_distribution_validator_indices[i];
            let fee_octa = fee_amounts_octa[i];
            pending_fee.pending_fee_by_validator.borrow_mut(&validator_index).add(
                fee_octa
            );
            i += 1;
        }
    }
```

**File:** aptos-move/framework/aptos-framework/sources/stake.move (L1866-1950)
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
        };

        spec {
            // The following addition should not overflow because `num_total_proposals` cannot be larger than 86400,
            // the maximum number of proposals in a day (1 proposal per second).
            assume cur_validator_perf.successful_proposals
                + cur_validator_perf.failed_proposals <= MAX_U64;
        };
        let num_total_proposals =
            cur_validator_perf.successful_proposals
                + cur_validator_perf.failed_proposals;
        let (rewards_rate, rewards_rate_denominator) =
            staking_config::get_reward_rate(staking_config);
        let rewards_active =
            distribute_rewards(
                &mut stake_pool.active,
                num_successful_proposals,
                num_total_proposals,
                rewards_rate,
                rewards_rate_denominator
            );
        let rewards_pending_inactive =
            distribute_rewards(
                &mut stake_pool.pending_inactive,
                num_successful_proposals,
                num_total_proposals,
                rewards_rate,
                rewards_rate_denominator
            );
        spec {
            assume rewards_active + rewards_pending_inactive <= MAX_U64;
        };

        if (std::features::is_distribute_transaction_fee_enabled()) {
            let mint_cap =
                &borrow_global<AptosCoinCapabilities>(@aptos_framework).mint_cap;
            if (fee_active > 0) {
                coin::merge(&mut stake_pool.active, coin::mint(fee_active, mint_cap));
            };
            if (fee_pending_inactive > 0) {
                coin::merge(
                    &mut stake_pool.pending_inactive,
                    coin::mint(fee_pending_inactive, mint_cap)
                );
            };
            let fee_amount = fee_active + fee_pending_inactive;
            if (fee_amount > 0) {
                event::emit(DistributeTransactionFee { pool_address, fee_amount });
            };
        };

        let rewards_amount = rewards_active + rewards_pending_inactive;
        // Pending active stake can now be active.
        coin::merge(
            &mut stake_pool.active, coin::extract_all(&mut stake_pool.pending_active)
        );
```

**File:** aptos-move/framework/aptos-framework/sources/stake.move (L3885-3928)
```text
    #[test(
        vm = @0x0, aptos_framework = @0x1, validator_0 = @0x123, validator_1 = @0x234
    )]
    public entry fun test_transaction_fee_limit(
        vm: &signer,
        aptos_framework: &signer,
        validator_0: &signer,
        validator_1: &signer
    ) acquires AllowedValidators, AptosCoinCapabilities, OwnerCapability, PendingTransactionFee, PrecomputedValidatorSet, StakePool, TransactionFeeConfig, ValidatorConfig, ValidatorPerformance, ValidatorSet {
        initialize_for_test(aptos_framework);
        initialize_pending_transaction_fee(aptos_framework);
        features::change_feature_flags_for_testing(
            aptos_framework,
            vector[features::get_distribute_transaction_fee_feature()],
            vector[]
        );
        let address_0 = signer::address_of(validator_0);
        let address_1 = signer::address_of(validator_1);
        let (_sk_0, pk_0, pop_0) = generate_identity();
        let (_sk_1, pk_1, pop_1) = generate_identity();
        initialize_test_validator(&pk_0, &pop_0, validator_0, 100, true, false);
        initialize_test_validator(&pk_1, &pop_1, validator_1, 100, true, true);
        assert!(
            borrow_global<ValidatorSet>(@aptos_framework).active_validators.length()
                == 2,
            0
        );

        record_fee(vm, vector[], vector[]);
        record_fee(
            vm,
            vector[get_validator_index(address_0)],
            vector[1]
        );
        record_fee(
            vm,
            vector[get_validator_index(address_1)],
            vector[2]
        );
        record_fee(
            vm,
            vector[get_validator_index(address_0), get_validator_index(address_1)],
            vector[10, 220]
        );
```

**File:** types/src/transaction/block_epilogue.rs (L54-67)
```rust
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[cfg_attr(any(test, feature = "fuzzing"), derive(Arbitrary))]
pub enum FeeDistribution {
    V0 {
        // Validator index -> Octa
        amount: BTreeMap<u64, u64>,
    },
}

impl FeeDistribution {
    pub fn new(amount: BTreeMap<u64, u64>) -> Self {
        Self::V0 { amount }
    }
}
```
