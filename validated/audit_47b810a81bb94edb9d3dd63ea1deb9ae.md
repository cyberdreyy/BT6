### Title
No timelock/notice period for validator commission-rate changes via `UpdateCommissionBps` - (File: `programs/vote/src/vote_state/mod.rs`)

### Summary
The `setPlatformFee()` timelock finding maps to an analogous unrestricted, instantly-effective fee (commission) update path in the vote program: `VoteInstruction::UpdateCommissionBps`, handled by `update_commission_bps()`. Unlike the legacy `update_commission()` path, which enforces an epoch-position restriction via `is_commission_update_allowed()`, the new basis-points commission update explicitly removes any timing restriction, letting the authorized withdrawer raise the commission (the "fee" charged to delegators) from 0 to 100% instantly and with zero notice.

### Finding Description
The legacy commission setter restricts commission *increases* to the first half of an epoch so that delegators have advance notice/opportunity to react before the new rate takes effect: [1](#0-0) 

`is_commission_update_allowed()` implements that half-epoch cutoff: [2](#0-1) 

However, the newer basis-points commission mechanism introduced by SIMD-0291/SIMD-0249 (`update_commission_bps`) explicitly drops this restriction, as the code comment states directly: "No commission update rule, per SIMD-0249 and SIMD-0291": [3](#0-2) 

This is dispatched unconditionally from `vote_processor.rs` whenever the relevant feature gates are active, requiring only the authorized withdrawer's signature — no cluster-level delay, cool-down, or epoch-boundary gate is enforced at the instruction level: [4](#0-3) 

The unit test explicitly documents that this path has "no timing restrictions... regardless of epoch position", in contrast with the old percentage-based commission that does enforce one: [5](#0-4) 

Partial mitigation exists only for the *inflation-rewards* accounting side: `delay_commission_updates` causes the reward-calculation code to look back at a previous epoch's snapshotted commission when computing `InflationRewards` payouts: [6](#0-5) 

But this delayed-lookback protection is specific to the inflation-rewards commission calculation path; the on-chain vote-account state itself (and the `BlockRevenue` commission kind) is mutated immediately and without restriction the moment the withdrawer submits the transaction.

### Impact Explanation
Delegators choose a validator partly based on its advertised commission rate. Because `update_commission_bps` allows the withdrawer to raise commission from 0 to 10000 bps (100%) instantly with no epoch-boundary or timelock restriction (unlike the legacy percentage-based commission setter), delegators have no guaranteed window to react (e.g., undelegate) before a validator starts extracting maximal commission on/near their stake rewards or block-revenue share. This directly matches the reported bug class of a critical, economically-impactful parameter (`setPlatformFee`-equivalent) being changeable without a timelock, and it is reachable purely through an ordinary signed transaction from the vote account's authorized withdrawer — no privileged node access required.

### Likelihood Explanation
High. Any authorized withdrawer of a `VoteStateV4` account can submit `UpdateCommissionBps` at any time once the `commission_rate_in_basis_points` and `delay_commission_updates` feature gates are active; no special conditions, race, or privileged environment are needed.

### Recommendation
Apply the same epoch-position/timelock restriction used by `update_commission()` (via `is_commission_update_allowed`) to `update_commission_bps()` as well, or otherwise enforce a minimum notice period (e.g., only allow commission increases to take effect at the start of the next epoch cluster-wide, consistently for both the on-chain state and reward accounting) so delegators have a guaranteed window to react to fee increases.

### Proof of Concept
1. Authorized withdrawer of a `VoteStateV4` account submits a transaction with `VoteInstruction::UpdateCommissionBps { commission_bps: 10000, kind: CommissionKind::BlockRevenue }` (or `InflationRewards`).
2. `vote_processor.rs` dispatches directly to `update_commission_bps()` with no epoch-position check [7](#0-6) .
3. The commission is updated immediately in vote-account state; any block produced or rewards computed after this slot use the new maximum commission, with delegators having had no advance warning, unlike the half-epoch-cutoff behavior of the legacy `update_commission` path.

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

**File:** programs/vote/src/vote_state/mod.rs (L1806-1815)
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
