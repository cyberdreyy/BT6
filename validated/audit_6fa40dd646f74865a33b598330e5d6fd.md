### Title
Zero-lamport stake reward silently dropped from `getInflationReward` while block record still exists - ([File: runtime/src/bank/partitioned_epoch_rewards/mod.rs])

### Finding Description
`Bank::update_reward_history_in_partition` filters out any `StakeReward` whose `get_stake_reward() > 0` before pushing it into `self.rewards`, meaning a legitimately-earned zero-lamport reward (e.g. a fully deactivated stake account) never appears in `bank.rewards` for that partition slot: `rewards.iter().filter(|x| x.get_stake_reward() > 0)` [1](#0-0) . This exact behavior is asserted by the existing test `test_update_reward_history_in_partition`, which explicitly zeroes one entry's `lamports` and confirms it is `remove`d/ignored from the resulting `bank.rewards` [2](#0-1) .

Later, `Bank::get_rewards_and_num_partitions` clones `self.rewards` into `KeyedRewardsAndNumPartitions.keyed_rewards`, and `should_record()` returns true if `!keyed_rewards.is_empty() || num_partitions.is_some()` [3](#0-2) . For a partition slot (not the epoch-boundary block), `num_partitions` is always `None` [4](#0-3)  — it is only `Some` at the epoch-boundary slot. Consequently, if a partition slot's only staking reward entrant had a zero reward and got filtered out, `keyed_rewards` is empty and `num_partitions` is `None`, so `should_record()` is false for that slot, meaning nothing is recorded in the block/blockstore for that address at that slot at all.

On the RPC read path, `get_inflation_reward` in `rpc/src/rpc.rs` locates the specific partition block via `EpochRewardsHasher::hash_address_to_partition`, fetches that block's `rewards`, and filters for the requested address via `filter_map_rewards` [5](#0-4) . Since the zero-reward entry was never stored (dropped at `update_reward_history_in_partition`), the address is absent from `reward_map`, and the final response for that address in `get_inflation_reward` (the outer code not fully shown but reachable from the returned `reward_map`) resolves to `None`, i.e., "no reward data" rather than "zero reward recorded." Because the fully-deactivated stake account legitimately earns `0` lamports, the client cannot distinguish this state from "reward for this slot/partition not yet processed" (e.g. epoch reward distribution period still active or block not yet available), both of which also produce `None`/errors from this same code path.

### Impact Explanation
This is a wrong-data-returned-for-a-valid-request issue: the RPC contract for `getInflationReward` implies "this address, this epoch, this commitment" resolves either to a concrete reward or a well-defined absence/error; instead a legitimate epoch with a zero reward is indistinguishable from an unresolved epoch. This matches the "wrong-slot/account data returned" bounty category — a single, unprivileged client querying its own known stake address get an ambiguous, technically incorrect answer purely from normal protocol behavior (a stake account that is fully deactivated and earns nothing in an epoch). It does not cause a crash, DoS, or consensus mutation, so the impact is limited to correctness/ambiguity of RPC responses, which can lead to a client mis-timing settlement decisions (e.g., retrying or considering the query "not yet resolved").

### Likelihood Explanation
This is fully deterministic and requires no attacker capability beyond controlling a stake account that becomes fully deactivated (a routine action any staker can perform) and then issuing a single `getInflationReward` call for the affected epoch — well within the single-call rate constraint. The `zero_reward` branch of `test_update_reward_history_in_partition` already demonstrates that this filtering happens on every partitioned-rewards epoch for every account whose net reward this epoch is exactly zero, so the condition is not a corner case but part of normal reward-distribution semantics for deactivated stake.

### Recommendation
Do not silently drop zero-lamport reward entries from `bank.rewards`/blockstore storage when the entry corresponds to a real reward-eligible stake account for that epoch; instead, retain a zero-value `RewardInfo` (as already done for `RewardType::DeactivatedStake` semantics elsewhere) so that `keyed_rewards` is non-empty and the reward is queryable, or otherwise expose a distinguishable "processed with zero reward" signal to `getInflationReward` consumers (e.g., have the RPC method return `Some(RpcInflationReward{ amount: 0, .. })` rather than `None` whenever the queried address is confirmed to have been part of the calculated (not just distributed) partition for that epoch).

### Proof of Concept
Add/extend a test alongside `test_update_reward_history_in_partition` in `runtime/src/bank/partitioned_epoch_rewards/distribution.rs`:
1. Construct a bank and a `StakeReward` with `stake_reward_info.lamports == 0` for a specific known `stake_pubkey` (simulating a fully deactivated stake account).
2. Call `bank.update_reward_history_in_partition(&[stake_reward])` and assert the returned count is `0` and `bank.rewards.read().unwrap()` contains no entry for `stake_pubkey` (already implicitly proven by existing `test_update_reward_history_in_partition`).
3. Call `bank.get_rewards_and_num_partitions()` on that partition-slot bank and assert `should_record()` is `false` (when this is the only reward in the partition) — showing the block-level record is skipped.
4. At the RPC layer, add an integration test (in `rpc/src/rpc.rs` test module) that builds an epoch with partitioned rewards where one address's stake is fully deactivated (net reward 0) for the epoch, calls `JsonRpcRequestProcessor::get_inflation_reward(vec![zero_reward_address], Some(RpcEpochConfig{epoch: Some(target_epoch), ..}))`, and assert the result is `Some(RpcInflationReward{ amount: 0, .. })` — expected to currently fail, returning `None` instead, confirming the ambiguity described.

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L228-237)
```rust
    fn update_reward_history_in_partition(&self, stake_rewards: &[StakeReward]) -> usize {
        let mut rewards = self.rewards.write().unwrap();
        rewards.reserve(stake_rewards.len());
        let initial_len = rewards.len();
        stake_rewards
            .iter()
            .filter(|x| x.get_stake_reward() > 0)
            .for_each(|x| rewards.push((x.stake_pubkey, x.stake_reward_info.into())));
        rewards.len().saturating_sub(initial_len)
    }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L722-765)
```rust
    fn test_update_reward_history_in_partition() {
        for zero_reward in [false, true] {
            let (genesis_config, _mint_keypair) =
                create_genesis_config(1_000_000 * LAMPORTS_PER_SOL);
            let bank = Bank::new_for_tests(&genesis_config);

            let mut expected_num = 100;

            let mut stake_rewards = (0..expected_num)
                .map(|_| StakeReward::new_random(&bank.rent_collector.rent))
                .collect::<Vec<_>>();

            let mut rng = rand::rng();
            let i_zero = rng.random_range(0..expected_num);
            if zero_reward {
                // pick one entry to have zero rewards so it gets ignored
                stake_rewards[i_zero].stake_reward_info.lamports = 0;
            }

            let num_in_history = bank.update_reward_history_in_partition(&stake_rewards);

            if zero_reward {
                stake_rewards.remove(i_zero);
                // -1 because one of them had zero rewards and was ignored
                expected_num -= 1;
            }

            bank.rewards
                .read()
                .unwrap()
                .iter()
                .zip(stake_rewards.iter())
                .for_each(|((k, reward_info), expected_stake_reward)| {
                    assert_eq!(
                        (
                            &expected_stake_reward.stake_pubkey,
                            &RewardInfo::from(expected_stake_reward.stake_reward_info),
                        ),
                        (k, reward_info)
                    );
                });

            assert_eq!(num_in_history, expected_num);
        }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/mod.rs (L346-374)
```rust
#[derive(Debug, PartialEq)]
pub struct KeyedRewardsAndNumPartitions {
    pub keyed_rewards: Vec<(Pubkey, RewardInfo)>,
    pub num_partitions: Option<u64>,
}

impl KeyedRewardsAndNumPartitions {
    pub fn should_record(&self) -> bool {
        !self.keyed_rewards.is_empty() || self.num_partitions.is_some()
    }
}

impl Bank {
    pub fn get_rewards_and_num_partitions(&self) -> KeyedRewardsAndNumPartitions {
        let keyed_rewards = self.rewards.read().unwrap().clone();
        let epoch_rewards_sysvar = self.get_epoch_rewards_sysvar();
        // If partitioned epoch rewards are active and this Bank is the
        // epoch-boundary block, populate num_partitions
        let epoch_schedule = self.epoch_schedule();
        let parent_epoch = epoch_schedule.get_epoch(self.parent_slot());
        let is_first_block_in_epoch = self.epoch() > parent_epoch;

        let num_partitions = (epoch_rewards_sysvar.active && is_first_block_in_epoch)
            .then_some(epoch_rewards_sysvar.num_partitions);
        KeyedRewardsAndNumPartitions {
            keyed_rewards,
            num_partitions,
        }
    }
```

**File:** rpc/src/rpc.rs (L797-877)
```rust
        // Append stake account rewards from partitions if partitions epoch
        // rewards is enabled
        if epoch_has_partitioned_rewards {
            let num_partitions = epoch_boundary_block.num_reward_partitions.expect(
                "epoch-boundary block should have num_reward_partitions for epochs with \
                 partitioned rewards enabled",
            );

            let num_partitions = usize::try_from(num_partitions)
                .expect("num_partitions should never exceed usize::MAX");
            let hasher = EpochRewardsHasher::new(
                num_partitions,
                &Hash::from_str(&epoch_boundary_block.previous_blockhash)
                    .expect("UiConfirmedBlock::previous_blockhash should be properly formed"),
            );
            let mut partition_index_addresses: HashMap<usize, Vec<String>> = HashMap::new();
            for address in addresses.iter() {
                let address_string = address.to_string();
                // Skip this address if (Voting) rewards were already found in
                // the first block of the epoch
                if !reward_map.contains_key(&address_string) {
                    let partition_index = hasher.clone().hash_address_to_partition(address);
                    partition_index_addresses
                        .entry(partition_index)
                        .and_modify(|list| list.push(address_string.clone()))
                        .or_insert(vec![address_string]);
                }
            }

            let block_list = self
                .get_blocks_with_limit(
                    first_confirmed_block_in_epoch + 1,
                    num_partitions,
                    Some(context_config),
                )
                .await?;

            for (partition_index, addresses) in partition_index_addresses.iter() {
                let slot = *block_list.get(*partition_index).ok_or_else(|| {
                    // If block_list.len() too short to contain
                    // partition_index, the epoch rewards period must be
                    // currently active.
                    let rewards_complete_block_height = epoch_boundary_block
                        .block_height
                        .map(|block_height| {
                            block_height
                                .saturating_add(num_partitions as u64)
                                .saturating_add(1)
                        })
                        .expect(
                            "every block after partitioned_epoch_reward_enabled should have a \
                             populated block_height",
                        );
                    RpcCustomError::EpochRewardsPeriodActive {
                        slot: bank.slot(),
                        current_block_height: bank.block_height(),
                        rewards_complete_block_height,
                    }
                })?;

                let Ok(Some(block)) = self
                    .get_block(
                        slot,
                        Some(RpcBlockConfig::rewards_with_commitment(config.commitment).into()),
                    )
                    .await
                else {
                    return Err(RpcCustomError::BlockNotAvailable { slot }.into());
                };

                let index_reward_map = Self::filter_map_rewards(
                    block.rewards,
                    slot,
                    addresses,
                    &|reward_type| -> bool {
                        reward_type == RewardType::Staking
                            || reward_type == RewardType::DeactivatedStake
                    },
                );
                reward_map.extend(index_reward_map);
            }
```
