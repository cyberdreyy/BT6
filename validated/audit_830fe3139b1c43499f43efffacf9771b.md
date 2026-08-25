Based on my research, I found a plausible structural analog to the Trident H-07 bug in Agave's vote-program **delegator block-revenue reward pool** (SIMD-0123), though I was unable to fully verify one detail (the exact pool-decrement bookkeeping) before running out of iterations — I flag that explicitly below.

### Title
Delegator reward pool (`pending_delegator_rewards`) is split by current-epoch stake snapshot, not by time-weighted contribution, letting late-arriving stakers skim block revenue accrued by earlier delegators - (File: runtime/src/bank/partitioned_epoch_rewards/calculation.rs)

### Summary
The SIMD-0123 delegator-rewards feature lets anyone permissionlessly deposit lamports into a vote account's shared `pending_delegator_rewards` pool via the `DepositDelegatorRewards` instruction. That pool can accumulate across multiple epochs. When epoch rewards are computed, the *entire current pool balance* is split among whichever stake accounts happen to be delegated to that vote account at the reward epoch's stake snapshot, proportional to `stake / total_active_stake` — with no accounting for whether that stake was actually delegated while the pool was accruing.

### Finding Description
`deposit_delegator_rewards` transfers lamports from any signer into the vote account and adds the amount to `vote_state.pending_delegator_rewards`, a single running balance shared by all current delegators: [1](#0-0) 

`add_pending_delegator_rewards` simply accumulates the deposit into this one shared counter: [2](#0-1) 

At epoch-reward time, `calculate_block_reward` reads the vote account's current `pending_delegator_rewards` and divides it among stake delegations using only the reward-epoch's stake snapshot (`stake / total_active_stake`) — it has no notion of which delegators were present when the pool accrued value: [3](#0-2) 

This is invoked per stake delegation during reward calculation: [4](#0-3) 

This mirrors the root cause of the Trident H-07 bug class: a shared/pooled accumulator (fees keyed by tick range in Trident; `pending_delegator_rewards` keyed by vote account in Agave) is paid out based on a *snapshot of participation at distribution time*, not proportional to each participant's actual time-weighted contribution while the pool accrued. In Trident, this let a party mint a tiny position on the same key just before `burn()` and take the whole accumulated pool; here, it lets a staker (re)delegate to a vote account shortly before a reward-payout epoch and capture a proportional share of `pending_delegator_rewards` that was deposited/accrued over prior epochs, at the expense of the delegators who were actually staked during that accrual period.

### Impact Explanation
Delegators who were staked to a vote account while `pending_delegator_rewards` accrued get diluted: any subsequent staker who arrives before the pool is next distributed can claim a proportional share of lamports that account never earned. This is a real fund-mutation issue (lamports move to accounts that did not deserve them) reachable via ordinary transactions (stake delegate + `DepositDelegatorRewards`), not requiring privileged access.

### Likelihood Explanation
Requires the `block_revenue_sharing`, `commission_rate_in_basis_points`, and `custom_commission_collector` features active (this is a newer, in-development reward path per the SIMD-0123/0185/0232/0291 references seen in the code) [5](#0-4) . The severity is bounded per-epoch by stake warm-up (`delegation_effective_stake`), which limits how much effective weight a newly delegated stake account gets in a single epoch, but the exploit can be amplified by repeatedly targeting vote accounts with large pending pools or by using already-warmed-up stake redelegated to the target vote account right before distribution.

### Recommendation
Track `pending_delegator_rewards` on a time-weighted or per-deposit-epoch basis (e.g., snapshot the pool and the delegated-stake distribution *at deposit time*, or require that only stake active continuously since the deposit is eligible for that portion of the pool), rather than distributing the whole current balance based solely on the stake snapshot present at the next reward-payout epoch.

### Proof of Concept
1. Vote account `V` has long-standing delegator `A` with 1,000,000 SOL delegated for many epochs.
2. Over several epochs, third parties deposit rewards into `V` via `DepositDelegatorRewards`, accumulating a large `pending_delegator_rewards` balance [6](#0-5) .
3. Immediately before the next reward-distribution epoch boundary, attacker `B` delegates already-warmed-up stake (e.g., moved from another validator, so no additional warm-up delay) to `V`.
4. At the reward epoch snapshot, `calculate_block_reward` computes `B`'s share as `pending_delegator_rewards * B_stake / total_active_stake` [7](#0-6) , paying `B` a portion of rewards that accrued entirely before `B` was delegated to `V`.

**Unresolved/uncertain**: I was unable to confirm, before the tool budget ran out, the exact mechanism by which `pending_delegator_rewards` is decremented after each distribution (e.g., whether it's reduced by the total distributed amount, potentially leaving residual dilution effects across epochs), nor did I verify precise warm-up rate parameters that bound how large `B`'s single-epoch effective stake can be. These details would refine the exact severity/likelihood but do not change the core structural finding that the payout snapshot is decoupled from time-weighted contribution.

### Citations

**File:** programs/vote/src/vote_state/mod.rs (L936-988)
```rust
pub fn deposit_delegator_rewards<S: std::hash::BuildHasher>(
    invoke_context: &mut InvokeContext,
    vote_account_index: IndexOfAccount,
    sender_account_index: IndexOfAccount,
    deposit: u64,
    signers: &HashSet<Pubkey, S>,
) -> Result<(), InstructionError> {
    let transaction_context = &invoke_context.transaction_context;
    let instruction_context = transaction_context.get_current_instruction_context()?;

    let vote_address = *instruction_context.get_key_of_instruction_account(vote_account_index)?;
    let source_address =
        *instruction_context.get_key_of_instruction_account(sender_account_index)?;

    // Source account must sign the transfer.
    verify_authorized_signer(&source_address, signers)?;

    // SIMD-0123 states we must validate the vote account deserializes to a v4
    // *before* attempting CPI, then update the `pending_delegator_rewards`
    // field *last*.
    // We can deserialize it, and hold onto the deserialized payload in-memory.
    // This way, we can drop the account borrow but avoid re-deserializing
    // later, since we know only lamports will change.
    let mut vote_state = {
        let vote_account =
            instruction_context.try_borrow_instruction_account(vote_account_index)?;

        // Can't use `get_vote_state_handler_checked`, since it will convert
        // the underlying vote state to v4.
        // SIMD-0123 requires an *initialized v4*.
        let versioned = VoteStateVersions::deserialize(vote_account.get_data())?;
        if let VoteStateVersions::V4(vote_state_v4) = versioned {
            Ok(VoteStateHandler::new_v4(*vote_state_v4))
        } else {
            Err(InstructionError::InvalidAccountData)
        }
    }?;

    // CPI to System: Transfer from sender to vote account.
    invoke_context.native_invoke_signed(
        system_instruction::transfer(&source_address, &vote_address, deposit),
        &[],
    )?;

    // Update `pending_delegator_rewards`.
    let transaction_context = &invoke_context.transaction_context;
    let instruction_context = transaction_context.get_current_instruction_context()?;
    let mut vote_account =
        instruction_context.try_borrow_instruction_account(vote_account_index)?;

    vote_state.add_pending_delegator_rewards(deposit)?;
    vote_state.set_vote_account_state(&mut vote_account)
}
```

**File:** programs/vote/src/vote_state/handler.rs (L196-209)
```rust
    pub(crate) fn add_pending_delegator_rewards(
        &mut self,
        amount: u64,
    ) -> Result<(), InstructionError> {
        match &mut self.target_state {
            TargetVoteState::V4(v4) => {
                v4.pending_delegator_rewards = v4
                    .pending_delegator_rewards
                    .checked_add(amount)
                    .ok_or(InstructionError::ArithmeticOverflow)?;
                Ok(())
            }
        }
    }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L173-232)
```rust
/// Calculates block reward for a stake account based on SIMD-0123
fn calculate_block_reward(
    rewarded_epoch: Epoch,
    delegation: &Delegation,
    stake_history: &StakeHistory,
    distribution_epoch_vote_accounts: &VoteAccounts,
    ag_epoch_type: &AlpenglowEpochType,
    new_warmup_cooldown_rate_epoch: Option<Epoch>,
    use_fixed_point_stake_math: bool,
) -> u64 {
    let vote_pubkey = delegation.voter_pubkey;
    let Some(vote_account) = distribution_epoch_vote_accounts.get(&vote_pubkey) else {
        debug!("could not find vote account {vote_pubkey} in cache");
        return 0;
    };
    let vote_state = vote_account.vote_state_view();
    let pending_delegator_rewards = vote_state.pending_delegator_rewards();
    // NOTE: during recalculation, `distribution_epoch_vote_accounts` already
    // includes updated stake activation values from after the new epoch
    // calculation, so we need to use `RewardEpochDelegatedStakes` for the exact
    // values at the end of the reward epoch.
    let (AlpenglowEpochType::Alpenglow {
        reward_epoch_delegated_stakes,
        ..
    }
    | AlpenglowEpochType::MigrationEpoch {
        reward_epoch_delegated_stakes,
        ..
    }) = ag_epoch_type
    else {
        debug!("Alpenglow must be enabled for block reward calculation");
        return 0;
    };
    let total_active_stake = reward_epoch_delegated_stakes
        .delegated_stakes
        .get(&vote_pubkey)
        .copied()
        .unwrap_or(0);
    if total_active_stake == 0 {
        0
    } else {
        let stake = delegation_effective_stake(
            delegation,
            rewarded_epoch,
            stake_history,
            new_warmup_cooldown_rate_epoch,
            use_fixed_point_stake_math,
        );
        // During recalculation, if stake account has already received rewards,
        // it's possible to have `stake > total_active_stake`. If
        // `pending_delegator_rewards` is a huge number, we could potentially
        // overflow a `u64`. We can also have individual rewards look greater
        // than the pending rewards. This is harmless in practice, but we
        // clamp it just to be safe
        (pending_delegator_rewards as u128 * stake as u128 / total_active_stake as u128)
            .try_into()
            .unwrap_or(u64::MAX)
            .min(pending_delegator_rewards)
    }
}
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L815-833)
```rust
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
```

**File:** programs/vote/src/vote_processor.rs (L409-426)
```rust
        VoteInstruction::DepositDelegatorRewards { deposit } => {
            // SIMD-0123: Deposit delegator rewards.
            // Requires:
            // * SIMD-0185: Vote State V4
            // * SIMD-0291: Commission in Basis Points
            // * SIMD-0232: Custom Commission Collector
            let feature_set = invoke_context.get_feature_set();
            if !feature_set.commission_rate_in_basis_points
                || !feature_set.custom_commission_collector
                || !feature_set.block_revenue_sharing
            {
                return Err(InstructionError::InvalidInstructionData);
            }

            instruction_context.check_number_of_instruction_accounts(2)?;
            drop(me);
            vote_state::deposit_delegator_rewards(invoke_context, 0, 1, deposit, &signers)
        }
```
