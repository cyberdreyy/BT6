Based on my investigation, I found a concrete analog of the "commission rate manipulation" bug class in Agave's vote program's SIMD-0291 basis-points commission update path.

### Title
Instant, unbounded commission increases via `UpdateCommissionBps` bypass anti-rug protections applied to legacy commission updates - (File: `programs/vote/src/vote_state/mod.rs`)

### Summary
The legacy `update_commission` instruction enforces an anti-rug timing rule (commission increases are only allowed in the first half of an epoch) and inflation reward redemption additionally uses a one-epoch-delayed snapshot of the commission rate. The newer basis-points commission instruction, `UpdateCommissionBps` (SIMD-0291), explicitly removes this rule, allowing a validator's authorized withdrawer to change commission (for `BlockRevenue`, and functionally for `InflationRewards` at the instruction level) instantly and by an arbitrary amount, at any point in the epoch — precisely the class of "commission rug" described in the external report.

### Finding Description
`update_commission` (legacy percentage-based) enforces `is_commission_update_allowed(clock.slot, epoch_schedule)` whenever the new commission is an increase, and this rule can only be bypassed once the `delay_commission_updates` feature is fully active: [1](#0-0) 

By contrast, `update_commission_bps`, gated by SIMD-0291 (`commission_rate_in_basis_points`) and requiring `delay_commission_updates` to be active at the instruction level, has no such gate at all — the code comment explicitly states "No commission update rule, per SIMD-0249 and SIMD-0291": [2](#0-1) 

This is invoked directly from the vote processor for both `InflationRewards` and `BlockRevenue` commission kinds: [3](#0-2) 

The test suite documents this explicitly: "Unlike `test_update_commission`, SIMD-0291 has no timing restrictions (per SIMD-0249). Updates are always allowed regardless of epoch position." [4](#0-3) 

For `InflationRewards`, this instruction-level removal of the rate limit is compensated for at the reward-calculation layer: `redeem_delegation_rewards` deliberately reads the commission from a snapshot of vote-account state taken a full epoch prior (`snapshot_epoch_vote_accounts`/`rewarded_epoch_vote_accounts`) rather than the live vote state, specifically "to prevent last minute commission rugs": [5](#0-4) [6](#0-5) 

However, for the `BlockRevenue` commission kind (SIMD-0123 block-revenue sharing), I could not find an equivalent delayed-snapshot mechanism. `calculate_block_reward`, which computes each delegator's share of a vote account's `pending_delegator_rewards`, operates on live, current-epoch vote-account state (`distribution_epoch_vote_accounts`) and does not take a commission parameter at all — implying the commission split for block revenue happens earlier, at fee-deposit time, using whatever `block_revenue_commission_bps` is live at that moment: [7](#0-6) 

### Impact Explanation
If the block-revenue commission split is applied using the live (undelayed) `block_revenue_commission_bps` at fee-deposit time — as the code I could inspect suggests — then a validator can call `UpdateCommissionBps { kind: BlockRevenue, commission_bps: 10000 }` immediately before/during their own leader slot(s), capturing 100% of that slot's transaction-fee revenue that would otherwise be shared with delegators, then revert the commission back down afterward. Unlike the legacy commission or the inflation-rewards commission path, there is no epoch-delay protection at the instruction level for this basis-points path, matching the external report's "small commission, then spike it" attack pattern, but with a much shorter exploitation window (per-slot rather than per-epoch) since block revenue is realized immediately rather than after a 30-day-equivalent stake lockup.

### Likelihood Explanation
Any validator who controls the vote account's authorized withdrawer key can invoke this instruction; no special privileges beyond normal validator operation are required. The main uncertainty is whether the block-revenue-sharing deposit path (not directly located in this investigation — likely under `DepositDelegatorRewards` processing) also applies a delay/snapshot protection analogous to `redeem_delegation_rewards`'s inflation-reward path. I was not able to locate or verify that code within the available search results, so I cannot confirm with certainty that the block-revenue commission split lacks delay protection; this should be verified directly against the `DepositDelegatorRewards` instruction handler and any fee-deposit code that reads `block_revenue_commission_bps`.

### Recommendation
Confirm whether `block_revenue_commission_bps` is read from a delayed/snapshotted vote-account state (analogous to `snapshot_epoch_vote_accounts`/`rewarded_epoch_vote_accounts` used for `inflation_rewards_commission_bps`) at the point fees are split into `pending_delegator_rewards`. If it is not, apply the same one-epoch-delay protection to the `BlockRevenue` commission kind that is already applied to `InflationRewards`, or reinstate an epoch-position-based rate-limit for commission increases in `update_commission_bps`.

### Proof of Concept
Not independently verifiable from the available code excerpts because the exact fee-deposit/commission-split code path for `BlockRevenue` (invoked via `DepositDelegatorRewards`) was not located in this investigation. A concrete PoC would require confirming that path reads live (not epoch-delayed) `block_revenue_commission_bps`, then demonstrating: (1) validator sets `block_revenue_commission_bps` to a low value, (2) delegators stake expecting a low commission split, (3) validator calls `UpdateCommissionBps{kind: BlockRevenue, commission_bps: 10000}` immediately before producing a block, (4) full transaction-fee revenue for that block is retained by the validator instead of split with delegators, (5) validator reduces the commission again for subsequent slots.

### Citations

**File:** programs/vote/src/vote_state/mod.rs (L796-825)
```rust
/// Update the vote account's commission
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

    let mut vote_state = vote_state_result?;

    // current authorized withdrawer must say "yay"
    verify_authorized_signer(vote_state.authorized_withdrawer(), signers)?;

    vote_state.set_commission(commission);

    vote_state.set_vote_account_state(vote_account)
}
```

**File:** programs/vote/src/vote_state/mod.rs (L827-859)
```rust
/// Update the vote account's commission in basis points (SIMD-0291, SIMD-0123).
pub fn update_commission_bps<S: std::hash::BuildHasher>(
    vote_account: &mut BorrowedInstructionAccount,
    target_version: VoteStateTargetVersion,
    commission_bps: u16,
    kind: CommissionKind,
    signers: &HashSet<Pubkey, S>,
    block_revenue_sharing_enabled: bool,
) -> Result<(), InstructionError> {
    // Per SIMD-0291: BlockRevenue returns InvalidInstructionData unless
    // SIMD-0123 (block_revenue_sharing) is enabled.
    if matches!(kind, CommissionKind::BlockRevenue) && !block_revenue_sharing_enabled {
        return Err(InstructionError::InvalidInstructionData);
    }

    let mut vote_state = get_vote_state_handler_checked(vote_account, target_version)?;

    // No commission update rule, per SIMD-0249 and SIMD-0291.

    // Require authorized withdrawer to sign.
    verify_authorized_signer(vote_state.authorized_withdrawer(), signers)?;

    match kind {
        CommissionKind::InflationRewards => {
            vote_state.set_inflation_rewards_commission_bps(commission_bps);
        }
        CommissionKind::BlockRevenue => {
            vote_state.set_block_revenue_commission_bps(commission_bps);
        }
    }

    vote_state.set_vote_account_state(vote_account)
}
```

**File:** programs/vote/src/vote_state/mod.rs (L1806-1820)
```rust
    /// Test update_commission_bps (SIMD-0291).
    ///
    /// Unlike test_update_commission, SIMD-0291 has no timing restrictions
    /// (per SIMD-0249). Updates are always allowed regardless of epoch position.
    ///
    /// This test only uses V4 since SIMD-0291 depends on SIMD-0185 (VoteStateV4).
    #[test]
    fn test_update_commission_bps() {
        let target_version = VoteStateTargetVersion::V4;
        let mut vote_state = vote_state_new_for_test(&solana_pubkey::new_rand(), target_version);
        let withdrawer_pubkey = *vote_state.authorized_withdrawer();
        let node_pubkey = *vote_state.node_pubkey();

        // Set initial commission.
        vote_state.set_commission(10); // 10%
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

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L701-724)
```rust
        let vote_state = vote_account.vote_state_view();

        // Fetch the voter commission from past epochs to attempt to
        // delay the effect of commission updates by at least one
        // full epoch.
        // When `commission_rate_in_basis_points` is true, use the new field
        // `inflation_rewards_commission_bps`; otherwise use the legacy
        // percentage field and convert to basis points by multiplying by 100.
        let commission_bps = if delay_commission_updates {
            let vote_state_for_commission = snapshot_epoch_vote_accounts
                .and_then(|eva| eva.get(&vote_pubkey))
                .or_else(|| rewarded_epoch_vote_accounts.and_then(|eva| eva.get(&vote_pubkey)))
                .map(|vote_account| vote_account.vote_state_view())
                .unwrap_or(vote_state);
            if commission_rate_in_basis_points {
                vote_state_for_commission.inflation_rewards_commission()
            } else {
                vote_state_for_commission.commission() as u16 * 100
            }
        } else if commission_rate_in_basis_points {
            vote_state.inflation_rewards_commission()
        } else {
            vote_state.commission() as u16 * 100
        };
```

**File:** runtime/src/bank/partitioned_epoch_rewards/mod.rs (L305-319)
```rust
pub(super) struct CachedVoteAccounts<'a> {
    /// Snapshot of vote account state from the beginning of the epoch prior to
    /// the rewarded epoch. This snapshot state is saved a full epoch before
    /// being used to prevent last minute commission rugs.
    ///
    /// Developer note: This field is `Option` to handle large bank warps
    pub(super) snapshot_epoch_vote_accounts: Option<&'a VoteAccounts>,
    /// Vote account state from the beginning of the rewarded epoch.
    ///
    /// Developer note: This field is `Option` to handle large bank warps
    pub(super) rewarded_epoch_vote_accounts: Option<&'a VoteAccounts>,
    /// Vote account state from the end of the rewarded epoch / beginning of the
    /// distribution epoch.
    pub(super) distribution_epoch_vote_accounts: &'a VoteAccounts,
}
```
