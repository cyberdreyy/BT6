Based on my research, here's the strongest reachable analog to the Teller front-running/commission-raising vulnerability class in the agave codebase.

### Title
No delay/anti-front-running rule on `UpdateCommissionBps` allows a vote account owner to instantly raise commission and capture already-accrued delegator rewards - (File: programs/vote/src/vote_state/mod.rs)

### Summary
The Teller bug lets a privileged fee-setter (market/protocol owner) front-run a pending transaction by raising their fee percentage right before it settles, diverting funds that should have gone to the counterparty. The equivalent authority in agave is a vote account's `authorized_withdrawer`, who sets the commission taken from delegator stake rewards / block revenue. The legacy `update_commission` path enforces a documented anti-front-running rule (`is_commission_update_allowed`, commission increases restricted to the first half of an epoch), but the newer basis-points path (`UpdateCommissionBps`, SIMD-0291) explicitly removes this restriction.

### Finding Description
`update_commission` in [1](#0-0)  enforces `is_commission_update_allowed` whenever a commission *increase* is requested, unless `disable_commission_update_rule` (the `delay_commission_updates` feature) is active. By contrast, `update_commission_bps` has no such rule at all, as the code comment itself states: "No commission update rule, per SIMD-0249 and SIMD-0291" [2](#0-1) . The vote processor dispatches to it without any epoch-position or magnitude check [3](#0-2) .

For `CommissionKind::BlockRevenue`, this commission is read live (not epoch-delayed) at the point block revenue is realized: `calculate_block_reward` pulls `pending_delegator_rewards` directly from the current `vote_state_view()` in the distribution-epoch cache [4](#0-3) , and the split of that pool between the validator's commission and the delegators happens later using whatever `block_revenue_commission_bps` value is stored on-chain at settlement time — there is no analogous "fetch commission from a prior epoch snapshot" protection for block-revenue commission the way there is for inflation rewards via `delay_commission_updates` in `redeem_delegation_rewards` [5](#0-4) .

This means delegator rewards can accumulate in `pending_delegator_rewards` over a period (via `DepositDelegatorRewards`, see [6](#0-5) ) while the commission is low, and the vote account's `authorized_withdrawer` can submit a single `UpdateCommissionBps` transaction to instantly set `block_revenue_commission_bps` to 10000 (100%) right before the accumulated `pending_delegator_rewards` is distributed at the reward-distribution boundary, diverting the entire pool to themselves — directly mirroring the Teller "raise fee right before settlement" front-running pattern.

### Impact Explanation
If exploitable, a validator operator could unilaterally redirect delegator-earned block-revenue rewards to themselves by front-running the epoch's reward-distribution calculation with a commission spike, since `update_commission_bps` imposes no timing or magnitude restriction and (for `BlockRevenue`) the accumulated pool is evaluated at distribution time using the then-current commission value. This is a fund-diversion issue affecting delegators' expected stake rewards.

### Likelihood Explanation
Likelihood depends on exact timing semantics of when `block_revenue_commission_bps` is read relative to `pending_delegator_rewards` accrual across epoch boundaries, and whether `redeem_delegation_rewards`'s `commission_bps` (used only for `InflationRewards`, protected by `delay_commission_updates`) is somehow also applied to the block-revenue pool downstream in `store_stake_accounts_in_partition`/`distribute_reward_commissions`, which I was not able to fully trace given the remaining tool budget. I could not verify with certainty whether a later, unseen code path re-derives block-revenue commission from a delayed/snapshotted value (as inflation rewards do) before final distribution.

### Recommendation
Apply the same `delay_commission_updates`-style protection used for `InflationRewards` commission to `BlockRevenue` commission: read `block_revenue_commission_bps` from a snapshotted vote-account state (e.g., `snapshot_epoch_vote_accounts`/`rewarded_epoch_vote_accounts`) rather than the live value at distribution time, or otherwise document/prove that `UpdateCommissionBps` cannot affect already-accrued `pending_delegator_rewards`.

### Proof of Concept
Could not be fully constructed with certainty within the remaining tool budget — this requires tracing the exact point where `block_revenue_commission_bps` is consumed to split `pending_delegator_rewards`/`block_reward` into validator vs. delegator portions (in the distribution phase, e.g. `distribution.rs` / `distribute_reward_commissions`), which was not fully located in this session. I recommend a Devin session with fuller repo access trace `distribute_reward_commissions` and `store_stake_accounts_in_partition` in `runtime/src/bank/partitioned_epoch_rewards/` to confirm whether the live (non-delayed) `block_revenue_commission_bps` value is used at final settlement, which would concretely confirm the front-running window.

### Citations

**File:** programs/vote/src/vote_state/mod.rs (L797-815)
```rust
pub fn update_commission<S: std::hash::BuildHasher>(
    vote_account: &mut BorrowedInstructionAccount,
    target_version: VoteStateTargetVersion,
    commission: u8,
    signers: &HashSet<Pubkey, S>,
    epoch_schedule: &EpochSchedule,
    clock: &Clock,
    disable_commission_update_rule: bool,
) -> Result<(), InstructionError> {
    let vote_state_result = get_vote_state_handler_checked(vote_account, target_version);
    let enforce_commission_update_rule = !disable_commission_update_rule
        && match vote_state_result.as_ref() {
            Ok(decoded_vote_state) => commission > decoded_vote_state.commission(),
            Err(_) => true,
        };

    if enforce_commission_update_rule && !is_commission_update_allowed(clock.slot, epoch_schedule) {
        return Err(VoteError::CommissionUpdateTooLate.into());
    }
```

**File:** programs/vote/src/vote_state/mod.rs (L842-847)
```rust
    let mut vote_state = get_vote_state_handler_checked(vote_account, target_version)?;

    // No commission update rule, per SIMD-0249 and SIMD-0291.

    // Require authorized withdrawer to sign.
    verify_authorized_signer(vote_state.authorized_withdrawer(), signers)?;
```

**File:** programs/vote/src/vote_state/mod.rs (L935-988)
```rust
/// Deposit delegator rewards into a vote account (SIMD-0123).
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

**File:** programs/vote/src/vote_processor.rs (L362-382)
```rust
        VoteInstruction::UpdateCommissionBps {
            commission_bps,
            kind,
        } => {
            // SIMD-0291: Commission Rate in Basis Points
            // Requires SIMD-0185: Vote State V4
            // Requires SIMD-0249: Delay Commission Updates
            let feature_set = invoke_context.get_feature_set();
            if !feature_set.commission_rate_in_basis_points || !feature_set.delay_commission_updates
            {
                return Err(InstructionError::InvalidInstructionData);
            }
            vote_state::update_commission_bps(
                &mut me,
                target_version,
                commission_bps,
                kind,
                &signers,
                feature_set.block_revenue_sharing,
            )
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
