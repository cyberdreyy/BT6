### Title
Calculated epoch stake rewards are silently burned instead of paid when the stake account is withdrawn/removed between reward calculation and reward distribution blocks - (File: runtime/src/bank/partitioned_epoch_rewards/distribution.rs)

### Summary
Partitioned epoch rewards are calculated once at the epoch boundary and then paid out over several subsequent blocks. If the underlying stake delegation is withdrawn (or otherwise removed from the `Stakes` cache) after the reward was calculated for it but before it is actually distributed, the already-computed reward for that account is not paid to anyone — it is unconditionally counted as "burned" and lost. This mirrors the Union Finance pattern: a resource is de-registered (there, `supportedMarkets[token] = false` via `removeToken()`; here, the stake account's delegation entry is removed from `stakes_cache_accounts` via a legitimate `Withdraw`/`stake_program` mutation), and the code path responsible for delivering the already-owed funds (`if (isMarketSupported(token))` there, `stakes_cache_accounts.get(...)` here) is skipped, so funds that rightfully belong to a user become permanently inaccessible/lost instead of delivered.

### Finding Description
Reward computation happens once, at the epoch boundary, and is cached as a list of `PartitionedStakeReward` entries (`all_stake_rewards`), keyed by `stake_pubkey`. Actual crediting of stake accounts, however, is deferred and spread across many subsequent blocks (`distribute_partitioned_epoch_rewards` / `distribute_epoch_rewards_in_partition`) — for large validator sets this window can span up to 10% of an epoch's slots (`get_reward_distribution_num_blocks`, capped by `MAX_FACTOR_OF_REWARD_BLOCKS_IN_EPOCH`): [1](#0-0) 

At the actual distribution block, `store_stake_accounts_in_partition` looks up the *current* state of the stake account from the live `stakes_cache_accounts` snapshot, not from the state used for calculation: [2](#0-1) 

If the pubkey is not found (`DistributionError::AccountNotFound`), the code does not defer, retry, redirect the funds, or refund the bank's capitalization to a neutral state — it simply logs an error and adds the reward amount to `stake_reward_lamports_burned` / `block_reward_lamports_burned`, permanently discarding the lamports that were already promised to that account: [3](#0-2) 

The developer comment explicitly acknowledges the code assumes this cannot happen ("Because stake accounts are checked in calculation, and further state mutation prevents by stake-program restrictions, there should never be rewards burned"), but this assumption is not actually enforced by any consensus rule: [4](#0-3) 

The stake account's entry is removed from the cache the moment its lamports reach zero (a stake withdrawal that fully drains the account) or when its data no longer deserializes into a delegated `StakeStateV2::Stake` (e.g., the stake program set the account to `Uninitialized`), via `StakesCache::check_and_store` → `remove_stake_delegation`: [5](#0-4) [6](#0-5) 

An ordinary user with the stake/withdraw authority for their own stake account can trigger this removal through the ordinary, permissionless `Deactivate` + `Withdraw` stake-program instructions (exactly the flow exercised in the CLI's own withdraw-stake tests), fully draining the account and thereby causing the reward that was already calculated for that stake to be discarded at distribution time instead of paid out: [7](#0-6) 

The existing regression test even hard-codes and validates this "burn" outcome as expected behavior when the account is simply absent from the cache: [8](#0-7) 

### Impact Explanation
This is a real, unprivileged loss-of-funds path: a legitimate stake reward that was already computed and "owed" to a staker/vote-account commission chain is dropped on the floor rather than paid to the staker, refunded to the bank, or credited elsewhere. Because `capitalization` is only incremented by `stake_reward_lamports_minted` (excluding the burned amount), the reward effectively vanishes from total supply — a quiet, protocol-level fund loss rather than a crash, but it directly parallels the "can not withdraw"/fund-lock class: an already-earned entitlement becomes permanently unobtainable once the account's supported/delegated status is removed mid-flight. It affects any staker whose stake account happens to become fully undelegated/withdrawn during the (potentially many-block) gap between calculation and distribution within an epoch boundary — a normal, expected user operation, not a contrived edge case.

### Likelihood Explanation
Likelihood is real but not high-frequency: it requires a stake account to receive a calculated reward at the epoch boundary and then be fully withdrawn/deactivated-and-drained (or otherwise removed from the delegation cache) before its specific partition block is processed. Because distribution can be spread over up to 10% of an epoch's slots, and any staker (or an automated bot managing many stake accounts) may withdraw at any time for entirely legitimate reasons (e.g., moving funds, closing out a stake), this window is realistically reachable without any special privilege — just ordinary transaction submission timing.

### Recommendation
Do not silently discard rewards when the destination stake account is missing/mutated at distribution time. Options include: (a) snapshotting/locking the specific stake account state referenced by a pending reward so it cannot be withdrawn until its reward is paid, (b) redirecting undeliverable rewards to a well-defined destination (e.g., re-crediting the original owner/authority account, or explicitly not counting them toward "burned" capitalization removal so the discrepancy is auditable/reversible), or (c) at minimum, treating this as a hard invariant violation (panic/refuse to advance) rather than an expected, tested "burn" path, so any genuine occurrence is investigated rather than silently accepted as protocol-correct fund destruction.

### Proof of Concept
1. At the epoch boundary, a stake account `S` delegated with an accruing reward is included in `all_stake_rewards` during `calculate_rewards_for_partitioning` (reward computed against the calculation-time `Stakes` snapshot).
2. Before `S`'s partition block is reached in `distribute_partitioned_epoch_rewards` (which can be many blocks later within the same epoch boundary window), the stake authority submits ordinary `Deactivate` + `Withdraw` (or `Withdraw` of the full inactive balance) instructions against `S`, driving its lamports to zero.
3. `Bank::update_stakes_cache` → `StakesCache::check_and_store` observes the zero-lamport stake-program-owned account and calls `remove_stake_delegation`, deleting `S` from `stakes_cache_accounts`.
4. When `S`'s partition block arrives, `store_stake_accounts_in_partition` → `build_updated_stake_reward` fails to find `S` in `stakes_cache_accounts`, returns `DistributionError::AccountNotFound`, and the reward amount is added to `stake_reward_lamports_burned` instead of being minted/paid — confirmed by the existing unit test `test_build_updated_stake_reward`, which asserts `AccountNotFound` is returned for a stake pubkey absent from the cache.

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/mod.rs (L408-428)
```rust
    /// Calculate the number of blocks required to distribute rewards to all stake accounts.
    pub(super) fn get_reward_distribution_num_blocks(
        &self,
        rewards: &PartitionedStakeRewards,
    ) -> u64 {
        let total_stake_accounts = rewards.num_rewards();
        if self.epoch_schedule.warmup && self.epoch < self.first_normal_epoch() {
            1
        } else {
            const MAX_FACTOR_OF_REWARD_BLOCKS_IN_EPOCH: u64 = 10;
            let num_chunks = total_stake_accounts
                .div_ceil(self.partitioned_rewards_stake_account_stores_per_block() as usize)
                as u64;

            // Limit the reward credit interval to 10% of the total number of slots in a epoch
            num_chunks.clamp(
                1,
                (self.epoch_schedule.slots_per_epoch / MAX_FACTOR_OF_REWARD_BLOCKS_IN_EPOCH).max(1),
            )
        }
    }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L249-252)
```rust
        let stake_account = stakes_cache_accounts
            .get(&partitioned_stake_reward.stake_pubkey)
            .ok_or(DistributionError::AccountNotFound)?
            .clone();
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L327-336)
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
    fn store_stake_accounts_in_partition(
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

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L807-833)
```rust
        let nonexistent_account = Pubkey::new_unique();
        let partitioned_stake_reward = PartitionedStakeReward {
            stake_pubkey: nonexistent_account,
            inflation: InflationReward {
                stake: new_stake,
                stake_reward,
                commission_bps: Some(commission_bps),
            },
            block_reward,
        };
        let stakes_cache = bank.stakes_cache.stakes();
        let stakes_cache_accounts = stakes_cache.stake_delegations();
        assert_eq!(
            Bank::build_updated_stake_reward(
                distribution_epoch,
                &stake_history,
                new_warmup_cooldown_rate_epoch,
                stakes_cache_accounts,
                &partitioned_stake_reward,
                &rent,
                adjust_delegations_for_rent,
                true,
            )
            .unwrap_err(),
            DistributionError::AccountNotFound
        );
        drop(stakes_cache);
```

**File:** runtime/src/stakes.rs (L99-116)
```rust
        // Zero lamport accounts are not stored in accounts-db
        // and so should be removed from cache as well.
        if account.lamports() == 0 {
            if solana_vote_program::check_id(owner) {
                let _old_vote_account = {
                    let mut stakes = self.0.write().unwrap();
                    stakes.remove_vote_account(pubkey)
                };
            } else if stake_program::check_id(owner) {
                let mut stakes = self.0.write().unwrap();
                stakes.remove_stake_delegation(
                    pubkey,
                    new_rate_activation_epoch,
                    use_fixed_point_stake_math,
                );
            }
            return;
        }
```

**File:** runtime/src/stakes.rs (L582-601)
```rust
    fn remove_stake_delegation(
        &mut self,
        stake_pubkey: &Pubkey,
        new_rate_activation_epoch: Option<Epoch>,
        use_fixed_point_stake_math: bool,
    ) {
        if let Some(stake_account) = self.stake_delegations.remove(stake_pubkey) {
            let removed_delegation = stake_account.delegation();
            let removed_stake = delegation_effective_stake(
                removed_delegation,
                self.epoch,
                &self.stake_history,
                new_rate_activation_epoch,
                use_fixed_point_stake_math,
            );
            self.sub_delegated_stake(&removed_delegation.voter_pubkey, removed_stake);
            self.vote_accounts
                .sub_stake(&removed_delegation.voter_pubkey, removed_stake);
        }
    }
```

**File:** cli/tests/stake.rs (L440-475)
```rust
    // Deactivate stake
    config_validator.command = CliCommand::DeactivateStake {
        stake_account_pubkey: stake_keypair.pubkey(),
        stake_authority: 0,
        sign_only: false,
        deactivate_delinquent: false,
        dump_transaction_message: false,
        blockhash_query: BlockhashQuery::default(),
        nonce_account: None,
        nonce_authority: 0,
        memo: None,
        seed: None,
        fee_payer: 0,
        compute_unit_price: None,
    };
    process_command(&config_validator).await.unwrap();

    // Withdraw available stake
    config_validator.signers = vec![&validator_keypair];
    config_validator.command = CliCommand::WithdrawStake {
        stake_account_pubkey: stake_keypair.pubkey(),
        destination_account_pubkey: recipient_pubkey,
        amount: SpendAmount::Available,
        withdraw_authority: 0,
        custodian: None,
        sign_only: false,
        dump_transaction_message: false,
        blockhash_query: BlockhashQuery::Rpc(Source::Cluster),
        nonce_authority: 0,
        nonce_account: None,
        memo: None,
        seed: None,
        fee_payer: 0,
        compute_unit_price: None,
    };
    process_command(&config_validator).await.unwrap();
```
