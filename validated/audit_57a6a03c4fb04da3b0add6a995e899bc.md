### Title
Block-revenue commission (`CommissionKind::BlockRevenue`) can be changed without any delay and is applied to an entire epoch's already-accrued block reward at distribution time - ([File: runtime/src/bank/partitioned_epoch_rewards/calculation.rs])

### Summary
Agave delays the effect of *inflation-rewards* commission changes by reading commission from a vote-account snapshot taken a full epoch earlier, explicitly to prevent "last minute commission rugs." However, the newer `update_commission_bps` instruction added for SIMD-0291/SIMD-0123 (block revenue sharing) has **no such delay** and the block-reward calculation path reads the commission from the vote account's *current, undelayed* state at distribution time rather than from the rate that was in effect while the revenue was accruing. This mirrors the Wise Lending `FeeManager.setPoolFee` bug: a rate is changed without first "syncing" (settling) the amount that already accrued under the old rate, so the new rate gets applied retroactively to the whole unsettled period.

### Finding Description
For inflation rewards, commission is intentionally delayed by caching vote-account state from prior epochs: [1](#0-0) 

and used in `redeem_delegation_rewards` via `snapshot_epoch_vote_accounts`/`rewarded_epoch_vote_accounts` when `delay_commission_updates` is active: [2](#0-1) 

This is the exact anti-pattern that the Wise Lending fix implements (settle before changing rate / use a lagged rate). In contrast, block revenue is computed with `calculate_block_reward` using `cached_vote_accounts.distribution_epoch_vote_accounts`: [3](#0-2) 

`distribution_epoch_vote_accounts` is documented as the vote-account state "at the end of the rewarded epoch / beginning of the distribution epoch" - i.e. the *current* commission at the moment rewards are computed, not the commission that was in effect during the epoch that generated the block revenue: [4](#0-3) 

Compounding this, `update_commission_bps` (which sets both `InflationRewards` and `BlockRevenue` commission in basis points) explicitly has no timing restriction, per the code comment "No commission update rule, per SIMD-0249 and SIMD-0291": [5](#0-4) 

This is confirmed by the unit test explaining that, unlike the legacy `update_commission`, SIMD-0291 updates have no epoch-position restriction: [6](#0-5) 

The legacy `update_commission` at least enforces an epoch-half cutoff via `is_commission_update_allowed` to bound the window in which a change can affect the *next* epoch's un-delayed rewards: [7](#0-6) 

but `update_commission_bps` bypasses this entirely, and the `BlockRevenue` commission consumed at reward-distribution time is read from the un-delayed, "current" vote account state rather than a state snapshot fixed before the revenue-accrual epoch began.

### Impact Explanation
A validator's authorized withdrawer can set the `BlockRevenue` commission_bps low (or to 0) throughout an entire epoch to attract/maintain delegated stake, and then, immediately before the epoch-boundary reward-distribution calculation runs, raise `BlockRevenue` commission_bps to a much higher value. Because `calculate_block_reward` reads the *current* (`distribution_epoch_vote_accounts`) commission rather than a rate fixed at the start of the accrual period, the entire epoch's already-earned block revenue gets split using the new, higher commission - retroactively transferring value from delegated stakers to the validator (or vice versa if lowered right before distribution, shorting the validator's own commission collector but that direction is less likely to be exploited). This is a concrete, unsigned reallocation of already-accrued reward lamports between stakers and the commission collector, achieved purely through a permissionless vote-program instruction (`UpdateCommission`/`CommissionKind::BlockRevenue`) - directly analogous to the reported Wise Lending issue where fee changes were applied to already-accrued-but-unsynced interest.

### Likelihood Explanation
Low-to-moderate. It requires the vote account's authorized withdrawer to intentionally time an `UpdateCommission` (bps, `BlockRevenue` kind) transaction just before the epoch boundary/reward-distribution slot, which is publicly known (deterministic epoch schedule) and requires no special access beyond normal transaction submission signed by the withdrawal authority - the same "honest mistake or deliberate manipulation, low likelihood but real" characterization given in the original C4 finding.

### Recommendation
Apply the same delay mechanism used for inflation-rewards commission to block-revenue commission: read the `BlockRevenue` commission_bps used in `calculate_block_reward` from a vote-account snapshot fixed prior to the start of the epoch whose revenue is being distributed (e.g. `snapshot_epoch_vote_accounts`/`rewarded_epoch_vote_accounts`), rather than from `distribution_epoch_vote_accounts`, or otherwise re-impose an `is_commission_update_allowed`-style timing restriction on `update_commission_bps` for the `BlockRevenue` kind so a rate change cannot retroactively apply to already-accrued revenue.

### Proof of Concept
1. Validator withdrawer holds a vote account with `BlockRevenue` commission_bps = 0 at the start of epoch N.
2. Throughout epoch N, the validator earns block revenue that is tracked/accrued against the vote account (0% commission implied).
3. Near the very end of epoch N (any slot, since `update_commission_bps` has no timing gate per `programs/vote/src/vote_state/mod.rs:844`), the withdrawer submits `UpdateCommission`/`CommissionKind::BlockRevenue` setting commission_bps to 10000 (100%).
4. At the epoch-N→N+1 boundary, `calculate_stake_rewards_and_commissions`/`calculate_block_reward` reads the vote account's *current* state (`distribution_epoch_vote_accounts`) and computes commission using the just-set 100% rate, per `runtime/src/bank/partitioned_epoch_rewards/calculation.rs:820-833`.
5. Result: 100% of epoch N's entire block revenue reward is redirected to the commission collector even though the 0% rate was advertised/in effect for essentially the whole epoch - a retroactive, unsigned reallocation of stakers' expected rewards.

### Citations

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

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L820-833)
```rust
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

**File:** programs/vote/src/vote_state/mod.rs (L990-1004)
```rust
/// Given the current slot and epoch schedule, determine if a commission change
/// is allowed
pub fn is_commission_update_allowed(slot: Slot, epoch_schedule: &EpochSchedule) -> bool {
    // always allowed during warmup epochs
    if let Some(relative_slot) = slot
        .saturating_sub(epoch_schedule.first_normal_slot)
        .checked_rem(epoch_schedule.slots_per_epoch)
    {
        // allowed up to the midpoint of the epoch
        relative_slot.saturating_mul(2) <= epoch_schedule.slots_per_epoch
    } else {
        // no slots per epoch, just allow it, even though this should never happen
        true
    }
}
```

**File:** programs/vote/src/vote_state/mod.rs (L1806-1813)
```rust
    /// Test update_commission_bps (SIMD-0291).
    ///
    /// Unlike test_update_commission, SIMD-0291 has no timing restrictions
    /// (per SIMD-0249). Updates are always allowed regardless of epoch position.
    ///
    /// This test only uses V4 since SIMD-0291 depends on SIMD-0185 (VoteStateV4).
    #[test]
    fn test_update_commission_bps() {
```
