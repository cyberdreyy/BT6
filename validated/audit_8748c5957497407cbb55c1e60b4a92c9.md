### Title
Stake accounts merged/closed after epoch-reward calculation cause user rewards to be silently burned instead of distributed - (File: `runtime/src/bank/partitioned_epoch_rewards/distribution.rs`)

### Summary
Partitioned epoch rewards are calculated once, at the epoch boundary, from a stakes-cache snapshot, but are only *distributed* to accounts several blocks later, one partition per block. If a stake account included in that snapshot no longer exists by the time its partition is distributed (e.g. because its owner merged it into another stake account or otherwise closed it via an ordinary, unprivileged transaction), the reward computed for it is not delivered anywhere — it is unconditionally burned.

### Finding Description
Reward calculation snapshots stake delegations at the epoch boundary in `calculate_rewards`/`calculate_stake_rewards_and_commissions` [1](#0-0) , producing a fixed `PartitionedStakeRewards` list that is distributed over many subsequent blocks (`distribution_starting_block_height` / per-partition blocks), not atomically with calculation [2](#0-1) .

During each distribution block, `store_stake_accounts_in_partition` looks up the *live*, current-bank version of each pre-computed stake account by pubkey via `stakes_cache_accounts.get(...)`, inside `build_updated_stake_reward`: [3](#0-2) 

If the account is not found (e.g. it was merged away, its lamports zeroed, and thus purged from the accounts store), the function returns `DistributionError::AccountNotFound`, and the caller treats this as a burn of the previously-calculated reward, rather than any kind of retry, redirect, or refund: [4](#0-3) 

The stake program permits merging two fully-active stake accounts delegated to the same vote account via an ordinary user transaction (`merge-stake`), which zeroes and effectively removes the source stake pubkey from the accounts store [5](#0-4) , and this is exercised even for just-activated delegations in existing tests [6](#0-5) . A staker can perform such a merge in the window between the epoch-boundary reward calculation and the specific block at which their partition is scheduled for distribution — a window of potentially many blocks, since rewards are spread `num_partitions` blocks after `distribution_starting_block_height` [7](#0-6) .

The code's own comment acknowledges that this burn path is not expected to occur under correct operation ("there should never be rewards burned") [8](#0-7) , indicating the `AccountNotFound` fallback is a defensive catch-all rather than a deliberately accepted, economically-sound outcome — yet no mechanism exists to prevent a staker's own ordinary merge/close transaction from triggering it.

### Impact Explanation
This is a direct analog of the reported Olympus bug class: rewards that have already been earned and calculated for a specific account become permanently unclaimable once that account is removed before the reward is actually paid out. Here, an unprivileged staker's own legitimate `MergeStake` transaction (no special privilege required) can cause their own already-earned inflation/block reward to be destroyed (burned) rather than credited to the surviving merged account, which corrupts stake/reward accounting and results in real, permanent economic loss to the staker with no path to reclaim it.

### Likelihood Explanation
Likelihood is limited but plausible: it requires timing a merge transaction into the specific block-height window between the epoch-boundary snapshot and the block where that staker's reward partition is processed. This window can span many blocks depending on `num_partitions`, and merges of fully-active, same-vote-account stake delegations are an entirely normal user operation with no restriction tied to reward-distribution status.

### Recommendation
Before finalizing the burn path in `store_stake_accounts_in_partition`/`build_updated_stake_reward`, check whether the missing stake account's lamports/reward can be attributed to a successor account (e.g. a merge destination) rather than unconditionally burning; alternatively, prevent stake accounts with pending, uncredited partitioned rewards from being merged or otherwise closed until their reward partition has been distributed, mirroring the report's suggested fix of disallowing removal while distributions are outstanding.

### Proof of Concept
1. At an epoch boundary, staker `S` has an active stake account `A` delegated to vote account `V`; reward calculation snapshots `A` and computes a nonzero `PartitionedStakeReward` for it, placed into partition `k` (`calculate_stake_rewards_and_commissions`).
2. Before the block scheduled for partition `k`'s distribution is reached, `S` submits a normal `MergeStake` instruction merging `A` into another fully-active stake account `B` also delegated to `V`. `A`'s lamports move to `B` and `A` is deallocated (zero lamports, no longer resolvable via `get_account`).
3. When partition `k` is processed, `store_stake_accounts_in_partition` calls `build_updated_stake_reward` for pubkey `A`, which fails with `DistributionError::AccountNotFound` (`stakes_cache_accounts.get(&A)` returns `None`).
4. The caller adds `A`'s pre-computed `stake_reward_amount` to `stake_reward_lamports_burned` instead of crediting it anywhere — `S`'s already-earned reward for that epoch is permanently lost even though `S`'s stake (now inside `B`) is fully intact and continues earning future rewards normally.

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L780-791)
```rust
    fn calculate_stake_rewards_and_commissions<'a>(
        &self,
        stake_history: &StakeHistory,
        stake_delegations: Vec<(&'a Pubkey, &'a StakeAccount<Delegation>)>,
        cached_vote_accounts: CachedVoteAccounts<'_>,
        rewarded_epoch: Epoch,
        point_value: PointValue,
        ag_epoch_type: &AlpenglowEpochType,
        thread_pool: &ThreadPool,
        reward_calc_tracer: Option<impl RewardCalcTracer>,
        metrics: &mut RewardsMetrics,
    ) -> (RewardCommissions, StakeRewardCalculation) {
```

**File:** runtime/src/bank.rs (L1862-1872)
```rust
        // Distribute rewards commission to vote accounts and cache stake rewards
        // for partitioned distribution in the upcoming slots.
        let (epoch_rewards, begin_partitioned_rewards_time_us) =
            measure_us!(self.begin_partitioned_rewards(
                parent_epoch,
                parent_slot,
                parent_height,
                &rewards_calculation,
                &mut rewards_metrics,
                thread_pool,
            ));
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L248-252)
```rust
    ) -> Result<StakeReward, DistributionError> {
        let stake_account = stakes_cache_accounts
            .get(&partitioned_stake_reward.stake_pubkey)
            .ok_or(DistributionError::AccountNotFound)?
            .clone();
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L327-335)
```rust
    /// Store stake rewards in partition
    /// Returns DistributionResults containing the sum of all the rewards
    /// stored, the sum of all rewards burned, and the updated StakeRewards.
    /// Because stake accounts are checked in calculation, and further state
    /// mutation prevents by stake-program restrictions, there should never be
    /// rewards burned.
    ///
    /// Note: even if staker's reward is 0, the stake account still needs to be
    /// stored because credits observed has changed
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L336-360)
```rust
    fn store_stake_accounts_in_partition(
        &self,
        partition_rewards: &StartBlockHeightAndPartitionedRewards,
        partition_index: u64,
    ) -> DistributionResults {
        let feature_snapshot = self.feature_set.snapshot();
        // Name intentionally doesn't match -- "adjust delegations for rent" is
        // part of relaxing post-exec min balance checks.
        let adjust_delegations_for_rent = feature_snapshot.relax_post_exec_min_balance_check;
        let use_fixed_point_stake_math = feature_snapshot.upgrade_bpf_stake_program_to_v5_1;

        let mut stake_reward_lamports_minted = 0;
        let mut stake_reward_lamports_burned = 0;
        let mut block_reward_lamports_distributed = 0;
        let mut block_reward_lamports_burned = 0;
        let indices = partition_rewards
            .partition_indices
            .get(partition_index as usize)
            .unwrap_or_else(|| {
                panic!(
                    "partition index out of bound: {partition_index} >= {}",
                    partition_rewards.partition_indices.len()
                )
            });
        let mut updated_stake_rewards = Vec::with_capacity(indices.len());
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L393-407)
```rust
            ) {
                Ok(stake_reward) => {
                    stake_reward_lamports_minted += stake_reward_amount;
                    block_reward_lamports_distributed += block_reward_amount;
                    updated_stake_rewards.push(stake_reward);
                }
                Err(err) => {
                    error!(
                        "bank::distribution::store_stake_accounts_in_partition() failed for \
                         {stake_pubkey}, {stake_reward_amount} lamports burned: {err:?}"
                    );
                    stake_reward_lamports_burned += stake_reward_amount;
                    block_reward_lamports_burned += block_reward_amount;
                }
            }
```

**File:** cli/src/stake.rs (L526-543)
```rust
        .subcommand(
            SubCommand::with_name("merge-stake")
                .about("Merges one stake account into another")
                .arg(pubkey!(
                    Arg::with_name("stake_account_pubkey")
                        .index(1)
                        .value_name("STAKE_ACCOUNT_ADDRESS")
                        .required(true),
                    "Stake account to merge into."
                ))
                .arg(pubkey!(
                    Arg::with_name("source_stake_account_pubkey")
                        .index(2)
                        .value_name("SOURCE_STAKE_ACCOUNT_ADDRESS")
                        .required(true),
                    "Source stake account for the merge. If successful, this stake account will \
                     no longer exist after the merge."
                ))
```

**File:** program-test/tests/warp.rs (L310-321)
```rust
    // sanity-check that it's possible to merge the just-activated stake with the older stake!
    let transaction = Transaction::new_signed_with_payer(
        &stake_instruction::merge(
            &base_stake_address,
            &absorbed_stake_address,
            &user_keypair.pubkey(),
        ),
        Some(&context.payer.pubkey()),
        &vec![&context.payer, &user_keypair],
        context.last_blockhash,
    );
    context
```
