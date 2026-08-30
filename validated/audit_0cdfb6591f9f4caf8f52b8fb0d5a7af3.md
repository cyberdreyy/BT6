#No Vulnerability found for this question.

**Rationale:**

The scenario in the question does not correspond to how rewards are computed or accrued in this codebase, and is not reachable by an unprivileged attacker:

1. **No cross-epoch "partial reward" concept exists.** `RewardCalculator::calculate_reward` computes and distributes the full reward for an epoch in a single atomic call at epoch finalization, based solely on `validator_block_chunk_stats` gathered during that epoch [1](#0-0) . There is no mechanism anywhere that "records" a delegator's or validator's partial/uptime-based reward mid-epoch to be paid out or reconciled in a later epoch — reward for validator uptime in epoch T is computed once, at T's finalization, added directly into the carried stake for the next-next epoch's proposals [2](#0-1) . There is no "un-minted difference" held in limbo that could later be dropped.

2. **The current epoch's validator set is immutable mid-epoch.** `validator_block_chunk_stats` is built from `epoch_info.validators_iter()`, i.e., the validator set that was already finalized two epochs prior [3](#0-2) . An account unstaking mid-epoch (a `Stake` action with 0 stake) only affects the *proposal* for future epochs (T+2); it cannot retroactively remove the account from the already-fixed current epoch's validator set or make `num_validators` become 0 for the epoch currently being finalized.

3. **The system explicitly prevents an empty validator set.** There is a "never kick everyone" safety valve that always keeps at least one validator, and if validator selection fails entirely (`NotEnoughValidators`/`ThresholdError`), the epoch manager falls back to cloning the previous `EpochInfo` [4](#0-3) . This makes `num_validators == 0` in `calculate_reward` an unreachable condition under normal operation initiated by a single attacker.

4. **The attack requires validator/consensus-level control, not just a funded account.** Becoming "the sole validator" on a live network is not something a single unprivileged account can unilaterally cause — it requires all other validators to already be absent, which is a network-state precondition outside the attacker's control, and per the rules this falls into rejected "misconfiguration-only" / epoch-manager-internal territory rather than an attacker-triggerable exploit.

Since the premise of an "already-earned but un-minted delegator reward" being silently dropped does not exist in the reward/stake-accrual model, and the `num_validators == 0` path is unreachable via any transaction an unprivileged attacker can submit, there is no valid, concretely exploitable vulnerability here.

### Citations

**File:** chain/epoch-manager/src/reward_calculator.rs (L51-90)
```rust
    pub fn calculate_reward(
        &self,
        validator_block_chunk_stats: HashMap<AccountId, BlockChunkValidatorStats>,
        validator_stake: &HashMap<AccountId, Balance>,
        total_supply: Balance,
        _protocol_version: ProtocolVersion,
        epoch_duration: u64,
        online_thresholds: ValidatorOnlineThresholds,
        max_inflation_rate: Rational32,
    ) -> (HashMap<AccountId, Balance>, Balance) {
        let mut res = HashMap::new();
        let num_validators = validator_block_chunk_stats.len();
        let use_hardcoded_value = self.genesis_protocol_version == PROD_GENESIS_PROTOCOL_VERSION;
        let protocol_reward_rate = if use_hardcoded_value {
            Rational32::new_raw(1, 10)
        } else {
            self.protocol_reward_rate
        };
        let epoch_total_reward = Balance::from_yoctonear(
            (U256::from(*max_inflation_rate.numer() as u64)
                * U256::from(total_supply.as_yoctonear())
                * U256::from(epoch_duration)
                / (U256::from(self.num_seconds_per_year)
                    * U256::from(*max_inflation_rate.denom() as u64)
                    * U256::from(NUM_NS_IN_SECOND)))
            .as_u128(),
        );
        let epoch_protocol_treasury = Balance::from_yoctonear(
            (U256::from(epoch_total_reward.as_yoctonear())
                * U256::from(*protocol_reward_rate.numer() as u64)
                / U256::from(*protocol_reward_rate.denom() as u64))
            .as_u128(),
        );
        res.insert(self.protocol_treasury_account.clone(), epoch_protocol_treasury);
        if num_validators == 0 {
            return (res, Balance::ZERO);
        }
        let epoch_validator_reward =
            epoch_total_reward.checked_sub(epoch_protocol_treasury).unwrap();
        let mut epoch_actual_reward = epoch_protocol_treasury;
```

**File:** chain/epoch-manager/src/lib.rs (L42-50)
```rust
use std::collections::{BTreeMap, HashMap, HashSet};
#[cfg(feature = "test_features")]
use std::marker::PhantomData;
use std::path::Path;
use std::sync::Arc;
pub use validator_selection::proposals_to_epoch_info;
use validator_stats::get_sortable_validator_online_ratio;

mod adapter;
```

**File:** chain/epoch-manager/src/lib.rs (L696-746)
```rust
    fn compute_validators_to_reward_and_kickout(
        config: &EpochConfig,
        epoch_info: &EpochInfo,
        block_validator_tracker: &HashMap<ValidatorId, ValidatorStats>,
        chunk_stats_tracker: &HashMap<ShardId, HashMap<ValidatorId, ChunkStats>>,
        spice_endorsement_tracker: &HashMap<ValidatorId, ValidatorStats>,
        prev_validator_kickout: &HashMap<AccountId, ValidatorKickoutReason>,
    ) -> (HashMap<AccountId, BlockChunkValidatorStats>, HashMap<AccountId, ValidatorKickoutReason>)
    {
        let block_producer_kickout_threshold = config.block_producer_kickout_threshold;
        let chunk_producer_kickout_threshold = config.chunk_producer_kickout_threshold;
        let chunk_validator_only_kickout_threshold = config.chunk_validator_only_kickout_threshold;
        let mut validator_block_chunk_stats = HashMap::new();
        let mut total_stake = Balance::ZERO;
        let mut maximum_block_prod = 0;
        let mut max_validator = None;

        for (i, v) in epoch_info.validators_iter().enumerate() {
            let account_id = v.account_id();
            let block_stats = block_validator_tracker
                .get(&(i as u64))
                .unwrap_or(&ValidatorStats { expected: 0, produced: 0 })
                .clone();
            let mut chunk_stats = ChunkStats::default();
            for (_, tracker) in chunk_stats_tracker {
                if let Some(stat) = tracker.get(&(i as u64)) {
                    *chunk_stats.expected_mut() += stat.expected();
                    *chunk_stats.produced_mut() += stat.produced();
                    chunk_stats.endorsement_stats_mut().produced +=
                        stat.endorsement_stats().produced;
                    chunk_stats.endorsement_stats_mut().expected +=
                        stat.endorsement_stats().expected;
                }
            }
            // On spice epochs endorsements are not embedded per-shard, so the
            // per-shard tracker above is empty; the endorsement stats come from
            // the epoch's last block header instead.
            if let Some(stat) = spice_endorsement_tracker.get(&(i as u64)) {
                chunk_stats.endorsement_stats_mut().produced += stat.produced;
                chunk_stats.endorsement_stats_mut().expected += stat.expected;
            }
            total_stake = total_stake.checked_add(v.stake()).unwrap();
            let is_already_kicked_out = prev_validator_kickout.contains_key(account_id);
            if (max_validator.is_none() || block_stats.produced > maximum_block_prod)
                && !is_already_kicked_out
            {
                maximum_block_prod = block_stats.produced;
                max_validator = Some(account_id.clone());
            }
            validator_block_chunk_stats
                .insert(account_id.clone(), BlockChunkValidatorStats { block_stats, chunk_stats });
```

**File:** chain/epoch-manager/src/lib.rs (L1146-1182)
```rust
        let (validator_reward, minted_amount) = {
            let last_epoch_last_block_hash =
                *self.get_block_info(block_info.epoch_first_block())?.prev_hash();
            let last_block_in_last_epoch = self.get_block_info(&last_epoch_last_block_hash)?;
            assert!(block_info.timestamp_nanosec() > last_block_in_last_epoch.timestamp_nanosec());
            let epoch_duration =
                block_info.timestamp_nanosec() - last_block_in_last_epoch.timestamp_nanosec();
            for (account_id, reason) in &validator_kickout {
                if matches!(
                    reason,
                    ValidatorKickoutReason::NotEnoughBlocks { .. }
                        | ValidatorKickoutReason::NotEnoughChunks { .. }
                        | ValidatorKickoutReason::NotEnoughChunkEndorsements { .. }
                ) {
                    validator_block_chunk_stats.remove(account_id);
                }
            }

            // We use the chunk validator kickout threshold as the cutoff threshold for the
            // endorsement ratio to remap the ratio to 0 or 1.
            let online_thresholds = ValidatorOnlineThresholds {
                online_min_threshold: epoch_config.online_min_threshold,
                online_max_threshold: epoch_config.online_max_threshold,
                endorsement_cutoff_threshold: Some(
                    epoch_config.chunk_validator_only_kickout_threshold,
                ),
            };
            self.reward_calculator.calculate_reward(
                validator_block_chunk_stats,
                &validator_stake,
                *block_info.total_supply(),
                epoch_protocol_version,
                epoch_duration,
                online_thresholds,
                epoch_config.max_inflation_rate,
            )
        };
```
