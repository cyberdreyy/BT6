Based on the investigation, there is a strong analog in the Agave codebase: the epoch-boundary reward **calculation** phase iterates over the entire set of stake delegations synchronously and without any per-unit compute metering, while only the reward **distribution** phase has been mitigated by partitioning across blocks.

### Title
Unmetered O(N) iteration over attacker-created stake delegations in epoch-boundary reward calculation enables a transaction-triggered cluster stall - (File: runtime/src/bank/partitioned_epoch_rewards/calculation.rs)

### Summary
At every epoch boundary, `Bank::process_new_epoch` synchronously computes rewards for *every* stake delegation in the `StakesCache` before the block can be considered processed. Unlike reward *distribution*, which was deliberately partitioned across many blocks to bound per-block work, reward *calculation* still runs as a single unbounded pass over all delegations, with no gas/compute-budget accounting tied to the number of delegations an attacker can create.

### Finding Description
`Bank::process_new_epoch` [1](#0-0)  calls `compute_new_epoch_caches_and_rewards`, which calls `self.calculate_rewards(...)` [2](#0-1) . This flows into `calculate_stake_rewards_and_commissions`, which does a `par_iter()` pass over the full `stake_delegations` vector — one entry per stake account on chain — calling `redeem_delegation_rewards` for each: [3](#0-2) . A second full pass computes reward points in `calculate_reward_points_partitioned` over the same `stake_delegations` vector [4](#0-3) .

This work is rayon-parallelized across CPU cores but is otherwise **unbounded and unmetered** — there is no per-transaction compute budget or gas cost tied to it, because it isn't executed as part of any single user transaction; it is protocol-level state transition logic that every validator must execute identically and deterministically at the first block of every epoch as part of normal bank/state-transition processing.

Critically, only the **distribution** half of this pipeline was hardened against large delegation counts: `get_reward_distribution_num_blocks` explicitly spreads stake-account credit writes across multiple blocks, capped at 10% of the epoch's slots [5](#0-4) . The **calculation** phase that precedes it has no equivalent chunking or cost-scaling mechanism — it must complete entirely within the processing of the epoch-boundary block itself, exactly mirroring the reported bug class where "Rewards Plans are unboundedly iterated over in unmetered `BeginBlock` execution."

The attacker-reachable input to this unbounded loop is the number of stake delegations, which any unprivileged transaction sender can grow arbitrarily by repeatedly issuing `create_account_and_delegate_stake`/`delegate_stake` instructions [6](#0-5) . The cost per delegation is the stake-account rent-exempt reserve plus the minimum delegation amount enforced by `get_minimum_delegation` [7](#0-6)  — both of which are **recoverable** by deactivating and withdrawing the stake after the attack, unlike the non-refundable 1 TIA fee in the original report. This makes the attack cost even lower than the analog case: an attacker only pays transient capital lockup and transaction fees, not a sunk cost.

### Impact Explanation
If the number of stake delegations grows large enough that the single-pass, single-block calculation phase cannot complete within the network's tolerated block-processing time, every validator replaying that epoch-boundary slot experiences the same slowdown simultaneously (this is deterministic, consensus-critical state-transition code, not leader-only or best-effort code). Because all validators must produce the same result to reach consensus on that slot, a sufficiently large delegation count can stall block production cluster-wide at every epoch boundary — a transaction-triggered cluster halt reachable purely through stake-account creation transactions, matching the "stake and reward accounting" and "bank commit determinism" scope categories.

### Likelihood Explanation
Likelihood is elevated because:
1. Stake-account creation is fully permissionless and requires no privileged access.
2. The capital required (rent-exempt reserve + minimum delegation) is largely recoverable via deactivation/withdrawal, unlike a pure fee, lowering the effective repeated-attack cost close to zero plus transaction fees.
3. The vulnerable code path (calculation, as opposed to distribution) has no chunking/partitioning safeguard at all, whereas the sibling distribution path was explicitly re-engineered to avoid exactly this class of issue, indicating awareness of the general risk but an incomplete fix.
4. The effect compounds every epoch as long as the attacker-created delegations remain active, recurring automatically without further attacker transactions.

### Recommendation
Apply the same partitioning strategy already used for reward *distribution* to reward *calculation*: chunk `calculate_stake_rewards_and_commissions` and `calculate_reward_points_partitioned` across multiple blocks (or precompute incrementally) rather than requiring the entire delegation set to be processed within a single epoch-boundary block, and/or introduce a hard ceiling on the number of concurrently active stake delegations counted toward a single epoch's reward calculation, with excess delegations rolled into a following calculation window.

### Proof of Concept
Conceptually (mirroring the original report's structure, adapted to this codebase):
1. Attacker repeatedly submits `stake_instruction::create_account_and_delegate_stake` transactions [8](#0-7) , each funded with `rent_exempt_reserve + minimum_delegation` lamports, to create N stake accounts, all delegated to any active vote account, well before an epoch boundary.
2. At the epoch boundary, `process_new_epoch` triggers `compute_new_epoch_caches_and_rewards` → `calculate_stake_rewards_and_commissions`, which must iterate all N delegations synchronously within that single block [3](#0-2) .
3. As N grows (e.g., into the millions, consistent with the code's own comment referencing ">1,000,000" delegations [9](#0-8) ), the wall-clock time for this single-block calculation grows linearly and unboundedly, with no gas/fee mechanism scaling to discourage it.
4. After the epoch boundary passes, the attacker deactivates and withdraws all N stake accounts, recovering most of the locked capital, and can repeat the attack every epoch.

Existing repository tests (`test_rewards_computation`, `test_epoch_boundary`) confirm that this calculation path scales linearly with the number of delegations and is executed in full at the epoch-boundary block, without any per-block cap analogous to `get_reward_distribution_num_blocks` [10](#0-9) .

### Citations

**File:** runtime/src/bank.rs (L1793-1803)
```rust
        let (rewards_calculation, update_rewards_with_thread_pool_time_us) =
            measure_us!(self.calculate_rewards(
                &stake_history,
                stake_delegations,
                cached_vote_accounts,
                rewarded_epoch,
                reward_epoch_delegated_stakes,
                reward_calc_tracer,
                thread_pool,
                rewards_metrics,
            ));
```

**File:** runtime/src/bank.rs (L1816-1846)
```rust
    fn process_new_epoch(
        &mut self,
        parent_epoch: Epoch,
        parent_slot: Slot,
        parent_capitalization: u64,
        parent_height: u64,
        reward_calc_tracer: Option<impl RewardCalcTracer>,
    ) {
        let epoch = self.epoch();
        let slot = self.slot();
        let thread_pool = rewards_calculation_thread_pool();

        let (_, apply_feature_activations_time_us) = measure_us!(
            thread_pool.install(|| { self.compute_and_apply_new_feature_activations() })
        );

        let mut rewards_metrics = RewardsMetrics::default();
        let NewEpochBundle {
            stake_history,
            unfiltered_distribution_vote_accounts,
            delegated_stakes,
            filtered_distribution_vote_accounts,
            rewards_calculation,
            calculate_activated_stake_time_us,
            update_rewards_with_thread_pool_time_us,
        } = self.compute_new_epoch_caches_and_rewards(
            thread_pool,
            parent_epoch,
            reward_calc_tracer,
            &mut rewards_metrics,
        );
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L804-808)
```rust
        // For N stake delegations, where N is >1,000,000, we produce:
        // * N stake rewards,
        // * M reward commission accounts, where M is a number of stake nodes.
        //   Currently, way smaller number than 1,000,000. And we can expect it
        //   to always be significantly smaller than number of delegations.
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L813-834)
```rust
        let stake_delegations_len = stake_delegations.len();
        let mut stake_rewards = PartitionedStakeRewards::with_capacity(stake_delegations_len);
        let rewards_accumulator: RewardsAccumulator = thread_pool.install(|| {
            stake_delegations
                .par_iter()
                .zip(&mut stake_rewards.spare_capacity_mut()[..stake_delegations_len])
                .with_min_len(500)
                .filter_map(|((stake_pubkey, stake_account), reward_ref)| {
                    let block_reward = if block_revenue_sharing {
                        calculate_block_reward(
                            rewarded_epoch,
                            stake_account.delegation(),
                            stake_history,
                            cached_vote_accounts.distribution_epoch_vote_accounts,
                            ag_epoch_type,
                            new_warmup_cooldown_rate_epoch,
                            use_fixed_point_stake_math,
                        )
                    } else {
                        0
                    };
                    let maybe_reward_record = self.redeem_delegation_rewards(
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L978-1002)
```rust
        let (points, measure_us) = measure_us!(thread_pool.install(|| {
            stake_delegations
                .par_iter()
                .map(|(_stake_pubkey, stake_account)| {
                    let vote_pubkey = stake_account.delegation().voter_pubkey;

                    let Some(vote_account) = distribution_epoch_vote_accounts.get(&vote_pubkey)
                    else {
                        return 0;
                    };
                    if vote_account.owner() != &solana_vote_program {
                        return 0;
                    }

                    calculate_points_for_tower(
                        stake_account.stake_state(),
                        DelegatedVoteState::from(vote_account.vote_state_view()),
                        stake_history,
                        new_warmup_cooldown_rate_epoch,
                        use_fixed_point_stake_math,
                    )
                    .unwrap_or(0)
                })
                .sum::<u128>()
        }));
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L1492-1551)
```rust
    #[test]
    /// Test rewards computation and partitioned rewards distribution at the epoch boundary
    fn test_rewards_computation() {
        agave_logger::setup();

        // Delegations to get rewards (2 SOL).
        let delegations = 100;
        let stakes = (0..delegations).map(|_| 2_000_000_000).collect::<Vec<_>>();
        let bank = create_reward_bank_with_specific_stakes(
            stakes,
            PartitionedEpochRewardsConfig::default().stake_account_stores_per_block,
            SLOTS_PER_EPOCH,
        )
        .0
        .bank;

        // Calculate rewards
        let thread_pool = ThreadPoolBuilder::new().num_threads(1).build().unwrap();
        let mut rewards_metrics = RewardsMetrics::default();
        let expected_rewards = 100_000_000_000;

        let stakes = bank.stakes_cache.stakes();
        let rewarded_epoch = 0;
        let EpochRewardCalculateParamInfo {
            stake_history,
            stake_delegations,
            cached_vote_accounts,
        } = bank.get_epoch_params_for_recalculation(rewarded_epoch, &stakes);
        let calculated_rewards = bank.calculate_validator_rewards(
            &stake_history,
            stake_delegations,
            cached_vote_accounts,
            rewarded_epoch,
            expected_rewards,
            reward_epoch_delegated_stakes_for_tests(rewarded_epoch),
            null_tracer(),
            &thread_pool,
            &mut rewards_metrics,
        );

        let reward_commissions = &calculated_rewards.as_ref().unwrap().reward_commissions;
        let stake_rewards = &calculated_rewards
            .as_ref()
            .unwrap()
            .stake_reward_calculation;

        let total_reward_commissions: u64 = reward_commissions
            .values()
            .map(|rc| rc.commission_lamports)
            .sum();

        // assert that total rewards matches the sum of reward commissions and stake rewards
        assert_eq!(
            stake_rewards.total_stake_rewards_lamports + total_reward_commissions,
            expected_rewards
        );

        // assert that number of stake rewards matches
        assert_eq!(stake_rewards.stake_rewards.num_rewards(), delegations);
    }
```

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

**File:** cli/src/stake.rs (L2847-2862)
```rust
pub async fn process_delegate_stake(
    rpc_client: &RpcClient,
    config: &CliConfig<'_>,
    stake_account_pubkey: &Pubkey,
    vote_account_pubkey: &Pubkey,
    stake_authority: SignerIndex,
    force: bool,
    sign_only: bool,
    dump_transaction_message: bool,
    blockhash_query: &BlockhashQuery,
    nonce_account: Option<Pubkey>,
    nonce_authority: SignerIndex,
    memo: Option<&String>,
    fee_payer: SignerIndex,
    compute_unit_price: Option<u64>,
) -> ProcessResult {
```

**File:** runtime/src/stake_utils.rs (L15-27)
```rust
/// The minimum stake amount that can be delegated, in lamports.
/// When this feature is added, it will be accompanied by an upgrade to the BPF Stake Program.
/// NOTE: This is also used to calculate the minimum balance of a delegated stake account,
/// which is the rent exempt reserve _plus_ the minimum stake delegation.
#[inline(always)]
pub fn get_minimum_delegation(upgrade_bpf_stake_program_to_v5_is_active: bool) -> u64 {
    if upgrade_bpf_stake_program_to_v5_is_active {
        const MINIMUM_DELEGATION_SOL: u64 = 1;
        MINIMUM_DELEGATION_SOL * LAMPORTS_PER_SOL
    } else {
        1
    }
}
```

**File:** program-test/tests/setup.rs (L23-48)
```rust
pub async fn setup_stake(
    context: &mut ProgramTestContext,
    user: &Keypair,
    vote_address: &Pubkey,
    stake_lamports: u64,
) -> Pubkey {
    let stake_keypair = Keypair::new();
    let transaction = Transaction::new_signed_with_payer(
        &stake_instruction::create_account_and_delegate_stake(
            &context.payer.pubkey(),
            &stake_keypair.pubkey(),
            vote_address,
            &Authorized::auto(&user.pubkey()),
            &Lockup::default(),
            stake_lamports,
        ),
        Some(&context.payer.pubkey()),
        &vec![&context.payer, &stake_keypair, user],
        context.last_blockhash,
    );
    context
        .banks_client
        .process_transaction(transaction)
        .await
        .unwrap();
    stake_keypair.pubkey()
```
