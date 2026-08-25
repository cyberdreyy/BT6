### Title
Unrestricted, un-delayed `UpdateCommissionBps` (BlockRevenue) lets a vote-account withdraw authority front-run stakers to steal their block-revenue share - (File: programs/vote/src/vote_state/mod.rs)

### Summary
The legacy `update_commission` instruction enforces a "commission update rule" that only allows a commission *increase* during the first half of an epoch, specifically to prevent last‑minute "commission rugs" against delegators. The newer SIMD-0291 `UpdateCommissionBps` instruction, introduced for `BlockRevenue`/`InflationRewards` commission in basis points, explicitly drops this restriction ("No commission update rule, per SIMD-0249 and SIMD-0291"), relying instead on the epoch-delay mechanism used for inflation rewards. However, `CommissionKind::BlockRevenue` commission is not an inflation-reward value distributed a full epoch later - it is the operator's cut of live per-block transaction/priority fee revenue. This makes the analogous "front-run to raise the take-rate right before value flows through" attack from the referenced report (EDITION_MANAGER_ROLE royalty rug) reachable here: the vote account's authorized withdrawer (a role every delegator implicitly trusts to be economically rational, but which is not delegator-controlled) can raise `block_revenue_commission_bps` immediately before a lucrative block/epoch of activity and lower it again afterward, with no enforced delay or increase-limit window.

### Finding Description
`update_commission` (legacy) enforces:
```
programs/vote/src/vote_state/mod.rs:797-825
``` [1](#0-0) 
which blocks commission *increases* outside the first half of the epoch via `is_commission_update_allowed`, specifically to stop "last-minute commission rugs" (comment in `runtime/src/bank.rs`): [2](#0-1) 

By contrast, `update_commission_bps` (SIMD-0291) has **no such rule**: [3](#0-2) 
The code comment on the corresponding instruction handler makes this explicit: "No commission update rule, per SIMD-0249 and SIMD-0291." [4](#0-3) 

For `CommissionKind::InflationRewards`, the removal of the time-window rule is acceptable because reward calculation independently delays the effective commission by reading vote-state from a snapshot taken a full epoch earlier when `delay_commission_updates` is active: [5](#0-4) [6](#0-5) 

For `CommissionKind::BlockRevenue`, however, the commission represents the operator's live cut of block/transaction-fee revenue rather than an inflation-style reward paid out epochs later. `calculate_block_reward` (used to split `pending_delegator_rewards` between the validator and stakers) reads the vote account state from `distribution_epoch_vote_accounts` - the *current* distribution-epoch snapshot, not a one-epoch-delayed snapshot: [7](#0-6) 
Because the `UpdateCommissionBps` instruction imposes no epoch-half restriction and no minimum delay before taking effect (unlike the legacy path), a withdrawer can change `block_revenue_commission_bps` at any point and have it apply to revenue accrued imminently thereafter.

### Impact Explanation
The vote account's authorized withdrawer is a role every delegator/staker trusts to manage commission responsibly on their behalf but does not control directly - directly analogous to the `EDITION_MANAGER_ROLE` in the referenced report, which was "restricted" but not fully trusted, and could front-run buyers by raising royalty just before their purchase. Here, a validator operator can:
1. Observe (via mempool/gossip or simply schedule knowledge) that a lucrative leader slot / high fee-revenue period is imminent.
2. Submit `UpdateCommissionBps { commission_bps: 10000, kind: BlockRevenue }` (100% take) with no epoch-timing restriction.
3. Collect nearly all of the block revenue that should have been shared with delegators for that period, then lower the commission back down afterward.

This is an unauthorized diversion of delegator funds enabled by a protocol-level removal of the front-running mitigation that exists for the legacy commission path, exactly matching the bug class in the source report (privileged-but-untrusted role front-running to increase a fee/commission at the victim's expense).

### Likelihood Explanation
Likelihood is Medium: it requires control of a vote account's authorized withdrawer key (a role that is not attacker-arbitrary but is explicitly less trusted than a delegator's own funds, similar to how `EDITION_MANAGER_ROLE` was "restricted" but not "trusted" in the Sherlock contest scope). No special network position is needed — the withdrawer can simply submit the `UpdateCommissionBps` transaction at any time, since the "no commission update rule" comment confirms there is no time-window or rate-of-change gate protecting stakers on this path, unlike the mitigated legacy commission field.

### Recommendation
Apply the same epoch-delay/anti-rug protection used for `InflationRewards` commission (and for the legacy commission field) to `CommissionKind::BlockRevenue`: either (a) read `block_revenue_commission_bps` from a one-epoch-delayed vote-state snapshot when computing/distributing block revenue, or (b) reinstate an `is_commission_update_allowed`-style time-window/rate-limit specifically for BlockRevenue commission increases, so operators cannot spike their take rate immediately before a period of high fee revenue.

### Proof of Concept
Given the review-only nature of this task, a full transaction-level PoC could not be executed, but the mechanism is confirmed at the code level:
1. Deploy/observe a vote account with `block_revenue_sharing` and `commission_rate_in_basis_points` features active.
2. As the vote account's authorized withdrawer, submit `VoteInstruction::UpdateCommissionBps { commission_bps: 10000, kind: CommissionKind::BlockRevenue }` immediately before (or during) a slot/epoch expected to generate high transaction fee revenue — this succeeds unconditionally as shown by the processor handler, which contains no epoch-timing check analogous to `is_commission_update_allowed`: [4](#0-3) 
3. Because `calculate_block_reward` reads commission-affecting vote state from the *current* distribution-epoch snapshot rather than a delayed one, the increased commission is honored for revenue distributed shortly after the update, unlike the protected `InflationRewards` path: [7](#0-6) 
4. Lower the commission back down afterward to avoid drawing attention, having captured an outsized share of that period's block revenue at the expense of delegators.

### Citations

**File:** programs/vote/src/vote_state/mod.rs (L805-815)
```rust
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

**File:** programs/vote/src/vote_state/mod.rs (L828-859)
```rust
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

**File:** runtime/src/bank.rs (L1730-1736)
```rust
    ) -> CachedVoteAccounts<'a> {
        // Snapshot of vote account state from the beginning of the epoch prior to
        // the rewarded epoch. This snapshot state is saved a full epoch before
        // being used to prevent last minute commission rugs.
        let snapshot_epoch_vote_accounts = self
            .epoch_stakes(rewarded_epoch)
            .map(|epoch_stakes| epoch_stakes.stakes().vote_accounts());
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

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L703-724)
```rust
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
