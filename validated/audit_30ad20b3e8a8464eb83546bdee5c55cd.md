No vulnerability found for this question.

**Reasoning:** `calculate_points_for_tower` in [1](#0-0)  already correctly returns `Err(InstructionError::InvalidAccountData)` for any non-`Stake` `StakeStateV2` variant, and its sole caller safely absorbs this via `.unwrap_or(0)` rather than panicking or misattributing rewards: [2](#0-1) . Treating a non-`Stake` account as contributing `0` points is the semantically correct outcome, since such an account isn't a delegation and shouldn't earn rewards — this isn't a "silently coerced success attributed to the wrong account" but a no-op for an account that has no stake to reward.

Critically, this function is not reachable from any RPC entrypoint. It is invoked only during the validator's internal epoch-boundary reward computation (`calculate_reward_points_partitioned` → `calculate_validator_rewards`), triggered by normal bank/epoch processing, not by a client-issued query [3](#0-2) . The actual RPC method `getInflationReward` (`RpcSolPimpl::get_inflation_reward` / `JsonRpcRequestProcessor::get_inflation_reward`) does not call `calculate_points_for_tower` at all — it instead reads already-computed, previously-persisted `Reward` records from a historical block via `get_block`/`get_blocks_with_limit` and filters them by address [4](#0-3) [5](#0-4) .

Additionally, the `stake_delegations` vector fed into `calculate_reward_points_partitioned` is sourced from the bank's `Stakes` cache, which by construction only tracks accounts already known to be in the `Stake` state (delegations); an `Uninitialized`/`RewardsPool` account would not appear there as a delegation in the first place, further precluding the described attacker scenario of injecting a non-Stake account into this path via a single RPC call.

Since there is no reachable single-RPC-call path from an unprivileged client into `calculate_points_for_tower`, and the existing error handling in both the function and its caller is already correct and panic-free, this does not meet the finding criteria.

### Citations

**File:** runtime/src/inflation_rewards/points.rs (L103-122)
```rust
pub(crate) fn calculate_points_for_tower(
    stake_state: &StakeStateV2,
    vote_state: DelegatedVoteState,
    stake_history: &StakeHistory,
    new_rate_activation_epoch: Option<Epoch>,
    use_fixed_point_stake_math: bool,
) -> Result<u128, InstructionError> {
    if let StakeStateV2::Stake(_meta, stake, _stake_flags) = stake_state {
        Ok(calculate_stake_points_for_tower(
            stake,
            vote_state,
            stake_history,
            null_tracer(),
            new_rate_activation_epoch,
            use_fixed_point_stake_math,
        ))
    } else {
        Err(InstructionError::InvalidAccountData)
    }
}
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L940-951)
```rust
    /// Calculates epoch reward points from stake/vote accounts.
    /// Returns reward lamports and points for the epoch or none if points == 0.
    fn calculate_reward_points_partitioned<'a>(
        &self,
        stake_history: &StakeHistory,
        stake_delegations: &Vec<(&'a Pubkey, &'a StakeAccount<Delegation>)>,
        cached_vote_accounts: &CachedVoteAccounts<'_>,
        epoch_inflation_rewards: u64,
        ag_epoch_type: &AlpenglowEpochType,
        thread_pool: &ThreadPool,
        metrics: &RewardsMetrics,
    ) -> Option<PointValue> {
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L992-999)
```rust
                    calculate_points_for_tower(
                        stake_account.stake_state(),
                        DelegatedVoteState::from(vote_account.vote_state_view()),
                        stake_history,
                        new_warmup_cooldown_rate_epoch,
                        use_fixed_point_stake_math,
                    )
                    .unwrap_or(0)
```

**File:** rpc/src/rpc.rs (L700-765)
```rust
    pub async fn get_inflation_reward(
        &self,
        addresses: Vec<Pubkey>,
        config: Option<RpcEpochConfig>,
    ) -> Result<Vec<Option<RpcInflationReward>>> {
        let config = config.unwrap_or_default();
        let epoch_schedule = self.get_epoch_schedule();
        let first_available_block = self.get_first_available_block().await;
        let context_config = RpcContextConfig {
            commitment: config.commitment,
            min_context_slot: config.min_context_slot,
        };
        let epoch = match config.epoch {
            Some(epoch) => epoch,
            None => epoch_schedule
                .get_epoch(self.get_slot(context_config)?)
                .saturating_sub(1),
        };

        // Rewards for this epoch are found in the first confirmed block of the next epoch
        let first_slot_in_epoch = epoch_schedule.get_first_slot_in_epoch(epoch.saturating_add(1));
        if first_slot_in_epoch < first_available_block {
            if self.bigtable_ledger_storage.is_some() {
                return Err(RpcCustomError::LongTermStorageSlotSkipped {
                    slot: first_slot_in_epoch,
                }
                .into());
            } else {
                return Err(RpcCustomError::BlockCleanedUp {
                    slot: first_slot_in_epoch,
                    first_available_block,
                }
                .into());
            }
        }

        let first_confirmed_block_in_epoch = *self
            .get_blocks_with_limit(first_slot_in_epoch, 1, Some(context_config))
            .await?
            .first()
            .ok_or(RpcCustomError::BlockNotAvailable {
                slot: first_slot_in_epoch,
            })?;

        // Determine if partitioned epoch rewards were enabled for the desired
        // epoch
        let bank = self.get_bank_with_config(context_config)?;

        // Get first block in the epoch
        let Ok(Some(epoch_boundary_block)) = self
            .get_block(
                first_confirmed_block_in_epoch,
                Some(RpcBlockConfig::rewards_with_commitment(config.commitment).into()),
            )
            .await
        else {
            return Err(RpcCustomError::BlockNotAvailable {
                slot: first_confirmed_block_in_epoch,
            }
            .into());
        };

        // If there is a gap in blockstore or long-term historical storage that
        // includes the epoch boundary, the `get_blocks_with_limit()` call above
        // will return the slot of the block at the end of that gap, not a
        // legitimate epoch-boundary block. Therefore, verify that the parent of
```

**File:** rpc/src/rpc.rs (L880-898)
```rust
        let rewards = addresses
            .iter()
            .map(|address| {
                if let Some((reward, slot)) = reward_map.get(&address.to_string()) {
                    return Some(RpcInflationReward {
                        epoch,
                        effective_slot: *slot,
                        amount: reward.lamports.unsigned_abs(),
                        post_balance: reward.post_balance,
                        commission: reward.commission,
                        commission_bps: reward.commission_bps,
                    });
                }
                None
            })
            .collect();

        Ok(rewards)
    }
```
