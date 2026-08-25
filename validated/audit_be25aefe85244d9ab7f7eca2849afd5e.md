### Title
Reward permanently burned instead of retried when a staker's stake account is deactivated/closed between epoch-reward calculation and partitioned distribution - ([File: runtime/src/bank/partitioned_epoch_rewards/distribution.rs])

### Summary
`store_stake_accounts_in_partition` looks up each staker's stake account in a `stakes_cache_accounts` snapshot taken at the *distribution* block, not the one used for the original *calculation* several blocks earlier. If the lookup fails (`DistributionError::AccountNotFound`), or the reward can't be applied for any other reason, the code logs an `error!` and simply adds the amount to `stake_reward_lamports_burned`/`block_reward_lamports_burned` — there is no retry, no credit to the staker, and the reward is gone forever, mirroring the VUSD `processWithdrawals` pattern of "log and move on" for a failed payout.

### Finding Description
Epoch rewards are calculated once per epoch in `calculate_stake_rewards_and_commissions` (via `recalculate_stake_rewards` / `redeem_rewards` in `runtime/src/inflation_rewards/mod.rs`) and then handed out over many subsequent blocks, one partition per block, in `distribute_epoch_rewards_in_partition` [1](#0-0) .

For each stake reward in a partition, `store_stake_accounts_in_partition` re-fetches the current stake account from the live `stakes_cache` at the time of that later block and calls `build_updated_stake_reward`: [2](#0-1) 

If the account is not present in the cache — which happens if the account owner deactivates and withdraws (closes) the stake account any time between the calculation block and the arrival of their partition's distribution block, an entirely ordinary, permissionless action — `build_updated_stake_reward` returns `Err(DistributionError::AccountNotFound)`.

The caller handles every error case identically: log and burn, with no path to retry or otherwise compensate the staker: [3](#0-2) 

The same "log-and-burn, no retry" pattern is duplicated for validator commission rewards in `load_and_reward_commission_accounts`, where an `Err` from `commission_account.checked_add_lamports` or `Self::collector_type_checked` (e.g., the collector account transiently fails the rent/ownership check) results in the commission being added to `total_non_incinerator_burned_lamports` instead of being retried or redirected: [4](#0-3) .

This is structurally analogous to the reported VUSD bug: a payout attempt fails, the failure is only logged, and the funds that were supposed to reach a specific account are irreversibly and unilaterally forfeited, with the capitalization accounting simply treating them as "burned" rather than crediting the intended recipient by any alternative means.

### Impact Explanation
Reward distribution spans multiple blocks per epoch (one partition per block) purely so that all stake accounts can eventually be credited; during that window, a staker's own routine, permissionless account lifecycle actions (deactivate + withdraw the stake account) can race with their own not-yet-processed partition. When that race is lost, the staker's already-earned reward for the previous epoch is permanently destroyed rather than deferred, retried, or paid to a fallback destination — the same fund-loss outcome the report describes, just triggered by a race window instead of an outright reverting external call. It does not corrupt consensus (capitalization bookkeeping stays internally consistent), but it does cause an unrecoverable loss of legitimately earned staker/validator funds for ordinary users interacting with the network in a completely normal way.

### Likelihood Explanation
Reachability requires no special privilege: any staker can close/withdraw a stake account they control at any time. Because epoch-reward distribution intentionally spreads payouts over many blocks (`partition_indices.len()` blocks), there is a real, network-determined window during which this race can occur for any given account, making the scenario plausible rather than purely theoretical, though it depends on the account happening to fall into a partition that is processed after the staker withdraws.

### Recommendation
When a stake reward cannot be applied at distribution time (account missing, arithmetic overflow, or state-set failure), do not immediately fold the amount into the "burned" bucket. Instead, either (a) retain/re-queue the reward for a later resolution (e.g., credit any close/withdraw path to first flush pending rewards before allowing closure), or (b) only burn amounts belonging to accounts that are provably permanently gone (e.g., surfaced via an explicit "abandoned rewards" sweep with its own audit trail), rather than silently discarding earned rewards in the same code path used for genuinely unrecoverable states.

### Proof of Concept
1. A staker's `Delegation` earns rewards for epoch `E`; `recalculate_stake_rewards`/`calculate_stake_rewards_and_commissions` computes and hashes the reward into a specific partition index (`hash_rewards_into_partitions`), determining which future block during epoch `E+1` will process it.
2. Before that partition's block height is reached, the staker submits an ordinary deactivate + withdraw transaction, removing their stake account (so it is no longer present in `stakes_cache_accounts`).
3. When `distribute_epoch_rewards_in_partition` reaches the staker's partition, `store_stake_accounts_in_partition` → `build_updated_stake_reward` returns `Err(DistributionError::AccountNotFound)` [2](#0-1) .
4. `store_stake_accounts_in_partition`'s match arm logs the error and adds the reward amount to `stake_reward_lamports_burned` [5](#0-4) , permanently forfeiting the staker's already-earned reward with no retry or alternate payout.

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L175-190)
```rust
    fn distribute_epoch_rewards_in_partition(
        &self,
        partition_rewards: &StartBlockHeightAndPartitionedRewards,
        partition_index: u64,
    ) {
        let pre_capitalization = self.capitalization();
        let (
            DistributionResults {
                stake_reward_lamports_minted,
                stake_reward_lamports_burned,
                block_reward_lamports_distributed,
                block_reward_lamports_burned,
                updated_stake_rewards,
            },
            store_stake_accounts_us,
        ) = measure_us!(self.store_stake_accounts_in_partition(partition_rewards, partition_index));
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L249-252)
```rust
        let stake_account = stakes_cache_accounts
            .get(&partitioned_stake_reward.stake_pubkey)
            .ok_or(DistributionError::AccountNotFound)?
            .clone();
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L384-407)
```rust
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

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L1140-1187)
```rust
                            // always exist.
                            let Some(commission_account) = maybe_commission_account else {
                                debug!(
                                    "commission account {commission_pubkey} missing at \
                                     distribution time"
                                );
                                return None;
                            };
                            commission_account
                        };
                        if *burned_lamports != 0 {
                            total_non_incinerator_burned_lamports
                                .fetch_add(*burned_lamports, Relaxed);
                        }
                        let pre_lamports = commission_account.lamports();
                        if let Err(err) =
                            commission_account.checked_add_lamports(*commission_lamports)
                        {
                            debug!("reward redemption failed for {commission_pubkey}: {err:?}");
                            total_non_incinerator_burned_lamports
                                .fetch_add(*commission_lamports, Relaxed);
                            return None;
                        }
                        if !is_vote_account {
                            match Self::collector_type_checked(
                                commission_pubkey,
                                pre_lamports,
                                &commission_account,
                                reserved_account_keys,
                                rent,
                                relax_post_exec_min_balance_check,
                            ) {
                                Ok(ExternalCollectorType::SystemAccount) => {}
                                Ok(ExternalCollectorType::Incinerator) => {
                                    total_incinerator_lamports
                                        .fetch_add(*commission_lamports, Relaxed);
                                }
                                Err(err) => {
                                    debug!(
                                        "reward redemption failed for {commission_pubkey} due to \
                                         commission account error: {err:?}"
                                    );
                                    total_non_incinerator_burned_lamports
                                        .fetch_add(*commission_lamports, Relaxed);
                                    return None;
                                }
                            }
                        }
```
