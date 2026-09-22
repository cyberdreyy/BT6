Confirmed: `store_stake_accounts_in_partition` reads `stakes_cache_accounts` live from `self.stakes_cache.stakes()` at distribution time (each partition block), not from a snapshot frozen at the calculation-time epoch boundary. This live account is then passed into `build_updated_stake_reward`, which computes `new_stake.delegation.stake` from `partitioned_stake_reward.inflation.stake` (calculated earlier) and, when `adjust_delegations_for_rent` is enabled, further reconciles the delegation against `account.lamports()` via `adjust_delegation_for_rent`, using whatever lamport balance the account currently holds at distribution time.

### Title
Front-run pending stake-reward distribution by inflating a stake account's lamport balance to gain unearned delegation stake - (File: runtime/src/bank/partitioned_epoch_rewards/distribution.rs)

### Summary
Partitioned epoch-reward distribution recomputes each stake account's new delegation amount using the *live*, distribution-time lamport balance of the stake account (`stakes_cache_accounts`, fetched fresh in `store_stake_accounts_in_partition`), rather than the balance snapshotted at the epoch-boundary calculation time. Because a lamport transfer to any account (regardless of owning program) requires no signature from the receiver, any unprivileged sender can top up the lamports of a stake account they control in between the calculation block and the block in which that account's reward partition is distributed (a multi-slot window). `build_updated_stake_reward`/`adjust_delegation_for_rent` then folds this newly-added balance into `delegation.stake`, effectively converting a plain SOL transfer into instantly-effective delegated stake without going through the normal `DelegateStake` warm-up path.

### Finding Description
`redeem_delegation_rewards`/`calculate_stake_rewards` correctly bar the classic "flash-stake" attack: rewards for the currently activating epoch are forced to zero (`stake.delegation.activation_epoch == rewarded_epoch` skips points, see `runtime/src/inflation_rewards/mod.rs:243-249`), and point accrual uses `stake_history`-derived `delegation_effective_stake` computed as of the rewarded epoch, not current balances.

However, a second, separate mechanism — the rent-adjustment path added for SIMD-0392 — reconstructs `delegation.stake` at *distribution* time (potentially several slots after the epoch boundary and reward-calculation block) directly from the stake account's current lamport balance: [1](#0-0) 

`store_stake_accounts_in_partition` reads the stake account's *current* state from the live stakes cache for each partition block, not a snapshot taken during the calculation phase: [2](#0-1) 

`build_updated_stake_reward` then computes `new_delegation = min(new_delegation_with_rewards, lamports_with_rewards - minimum_lamports)` using `account.lamports()` of this live account (after crediting the calculated `stake_reward`/`block_reward`): [3](#0-2) 

Because SOL transfers via the System Program only require the *sender* to sign and do not check the owner of the destination account, an attacker can submit `SystemInstruction::Transfer` to their own (or anyone's) stake account pubkey at any point between the reward-calculation block and that account's specific distribution-partition block. The added lamports are folded into `lamports_with_rewards`, and thus into the reconstructed `delegation.stake`, at distribution time — this is exactly analogous to the reported eEth pattern, where a deposit executed after the yield-accrual snapshot but before the reward transaction settles captures a disproportionate share of the payout. The existing test `test_delegation_adjustment_at_distribution` demonstrates this exact mechanic: lamports are added to the stake account after calculation and before `distribute_epoch_rewards_in_partition`, and the delegation is bumped accordingly: [4](#0-3) [5](#0-4) 

This adjustment path only activates in specific edge cases (`adjust_delegations_for_rent` gated by the `relax_post_exec_min_balance_check` feature, and only for accounts whose reward would otherwise fall below the rent-exempt minimum, or that need destaking), which significantly limits the blast radius: it is designed as a narrow SIMD-0392 rent-reconciliation fix, not a general delegation top-up mechanism, and `new_delegation` is capped by `lamports_with_rewards - minimum_lamports`, meaning any attacker-added lamports are only counted up to whatever headroom the destaking/rent logic leaves, not an unbounded multiplier of the reward like the eEth PoC (95%+ of rebase). I was not able to fully trace every feature-gate condition (`relax_post_exec_min_balance_check` state at genesis/mainnet) or every downstream consumer of the recomputed `delegation.stake` (e.g., whether it also inflates the account's future point/vote-weight for the *next* epoch's `calculate_activated_stake` snapshot) within the available context, so the full consensus/reward impact could not be conclusively confirmed.

### Impact Explanation
If exploitable, this would let a stake-account owner convert an ordinary, permissionless lamport transfer into stake that is credited immediately at distribution time rather than going through the standard activation warm-up, and/or avoid destaking below the rent-exempt minimum by opportunistically funding the account mid-distribution — a form of unearned delegation/stake corruption reachable from a single unprivileged transaction. This matches the "stake or reward corruption" acceptance criterion. It does not, however, appear to divert other stakers' funds directly (unlike the eEth report, where the whale captured funds that would otherwise go to existing depositors); here the attacker only inflates their own account's delegation using their own lamports, and the effect is bounded by the rent-adjustment cap.

### Likelihood Explanation
Requires the `relax_post_exec_min_balance_check`/rent-adjustment feature to be active and requires timing a transfer within the narrow multi-slot window between epoch-boundary calculation and the specific block distributing that account's partition — a window whose start/end block heights are public (`distribution_starting_block_height`, `partition_indices`), making timing feasible for a single attacker-controlled account, but the benefit is capped by the rent/destaking headroom rather than being large and open-ended.

### Recommendation
Use the lamport balance captured at reward-calculation time (or a value immutable during the calculation→distribution window) for the rent-adjustment computation in `adjust_delegation_for_rent`/`build_updated_stake_reward`, instead of re-reading the live stakes-cache account balance at each distribution block, so that a permissionless transfer performed after calculation cannot influence the resulting `delegation.stake`.

### Proof of Concept
Not independently executed against a live cluster; based on the existing unit test `test_delegation_adjustment_at_distribution` in `runtime/src/bank/partitioned_epoch_rewards/distribution.rs`, which already demonstrates the mechanic: it stores a stake account with a small pending reward, then calls `stake_account.checked_add_lamports(1_000_000_000)` followed by `bank.store_account(...)` to simulate an external lamport transfer "before distribution time," and confirms via `bank.distribute_epoch_rewards_in_partition(...)` that the post-distribution `delegation.stake` includes the injected lamports: [6](#0-5)

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L55-76)
```rust
fn adjust_delegation_for_rent(
    delegation: &mut Delegation,
    rewarded_epoch: Epoch,
    new_delegation_with_rewards: u64,
    lamports_with_rewards: u64,
    minimum_lamports: u64,
) {
    let new_delegation = std::cmp::min(
        new_delegation_with_rewards,
        lamports_with_rewards.saturating_sub(minimum_lamports),
    );

    if new_delegation != delegation.stake {
        delegation.stake = new_delegation;
        // Deactivate stake if needed. This deactivation is immediate,
        // unlike a requested deactivation which happens at the next epoch
        // boundary
        if new_delegation == 0 {
            delegation.deactivation_epoch = rewarded_epoch;
        }
    }
}
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L262-296)
```rust
        account
            .checked_add_lamports(partitioned_stake_reward.inflation.stake_reward)
            .map_err(|_| DistributionError::ArithmeticOverflow)?;
        account
            .checked_add_lamports(partitioned_stake_reward.block_reward)
            .map_err(|_| DistributionError::ArithmeticOverflow)?;

        let mut new_stake = partitioned_stake_reward.inflation.stake;
        if adjust_delegations_for_rent {
            let minimum_balance = rent.minimum_balance(account.data().len());
            // The rewarded epoch is right before the distribution epoch
            let rewarded_epoch = distribution_epoch.saturating_sub(1);
            // The entry in `partitioned_stake_reward` contains the rewards,
            // calculated during the calculation phase
            let delegation_with_rewards = new_stake.delegation.stake;
            adjust_delegation_for_rent(
                &mut new_stake.delegation,
                rewarded_epoch,
                delegation_with_rewards,
                account.lamports(),
                minimum_balance,
            );
        } else {
            let expected_delegation = stake
                .delegation
                .stake
                .saturating_add(partitioned_stake_reward.inflation.stake_reward);
            assert_eq!(
                expected_delegation, new_stake.delegation.stake,
                "stake reward delegation must be consistent with the updated stake account \
                 lamport balance"
            );
        }
        account
            .set_state(&StakeStateV2::Stake(meta, new_stake, flags))
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L361-393)
```rust
        let stakes_cache = self.stakes_cache.stakes();
        let stakes_cache_accounts = stakes_cache.stake_delegations();
        let stake_history = stakes_cache.history();
        let new_warmup_cooldown_rate_epoch = self.new_warmup_cooldown_rate_epoch();
        let rent = &self.rent_collector.rent;
        for index in indices {
            let partitioned_stake_reward = partition_rewards
                .all_stake_rewards
                .get(*index)
                .unwrap_or_else(|| {
                    panic!(
                        "partition reward out of bound: {index} >= {}",
                        partition_rewards.all_stake_rewards.total_len()
                    )
                })
                .as_ref()
                .unwrap_or_else(|| {
                    panic!("partition reward {index} is empty");
                });
            let stake_pubkey = partitioned_stake_reward.stake_pubkey;
            let stake_reward_amount = partitioned_stake_reward.inflation.stake_reward;
            let block_reward_amount = partitioned_stake_reward.block_reward;

            match Self::build_updated_stake_reward(
                self.epoch,
                stake_history,
                new_warmup_cooldown_rate_epoch,
                stakes_cache_accounts,
                partitioned_stake_reward,
                rent,
                adjust_delegations_for_rent,
                use_fixed_point_stake_math,
            ) {
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L1246-1266)
```rust
        // Below new minimum, small reward, should normally be destaked
        let reward_lamports = 1;
        let reward = PartitionedStakeReward::new_with_lamport_amounts(reward_lamports, 0, 1);
        let rewards_to_distribute = reward.inflation.stake_reward;
        let stake_pubkey = reward.stake_pubkey;
        let stake_rewards = [reward];
        populate_starting_stake_accounts_from_stake_rewards(&bank, &lower_rent, &stake_rewards);
        let mut stake_account = bank.get_account(&stake_pubkey).unwrap();

        let expected_num = 1;

        let partitioned_rewards = StartBlockHeightAndPartitionedRewards {
            distribution_starting_block_height: bank.block_height() + REWARD_CALCULATION_NUM_BLOCKS,
            all_stake_rewards: Arc::new(stake_rewards.into_iter().collect()),
            partition_indices: vec![(0..expected_num).collect::<Vec<_>>()],
        };

        // But we transfer in more lamports before distribution time
        stake_account.checked_add_lamports(1_000_000_000).unwrap();
        bank.store_account(&stake_pubkey, &stake_account);

```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L1285-1293)
```rust
        // Check that delegation just gets rewards
        let post_account = bank.get_account(&stake_pubkey).unwrap();
        let post_stake_state: StakeStateV2 = post_account.state().unwrap();
        let pre_stake_state: StakeStateV2 = stake_account.state().unwrap();
        assert_eq!(
            post_stake_state.delegation().unwrap().stake,
            pre_stake_state.delegation().unwrap().stake + reward_lamports
        );
    }
```
