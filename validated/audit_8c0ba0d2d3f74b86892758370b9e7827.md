### Title
Validator-controlled `BlockRevenue` commission changes via `update_commission_bps` bypass all timelock protections that legacy commission updates enforce - ([File: programs/vote/src/vote_state/mod.rs])

### Summary
The vote program provides two paths to change a vote account's commission: the legacy `update_commission` (percentage-based) and the newer `update_commission_bps` (basis-points, introduced by SIMD-0291/SIMD-0123). The legacy path enforces a timing rule — commission *increases* are only allowed in the first half of an epoch — specifically to give delegators/stakers advance notice before a commission hike affects their rewards. The new `update_commission_bps` path explicitly removes this rule ("No commission update rule, per SIMD-0249 and SIMD-0291") for both `InflationRewards` and `BlockRevenue` commission kinds, allowing the vote account's authorized withdrawer to raise commission to the maximum (10,000 bps = 100%) at any slot, with no advance-notice window at all.

### Finding Description
`update_commission` enforces `is_commission_update_allowed`, which only permits commission increases in the first half of the epoch: [1](#0-0) 

`is_commission_update_allowed` implements this half-epoch cutoff: [2](#0-1) 

In contrast, `update_commission_bps` — the mechanism for both `InflationRewards` and `BlockRevenue` commission — has no such gate; the code comment explicitly states there is no timing restriction: [3](#0-2) 

For `InflationRewards` commission specifically, the epoch-rewards calculation path contains a separate mitigation: it can fetch the commission value from a prior-epoch snapshot to "attempt to delay the effect of commission updates by at least one full epoch": [4](#0-3) 

However, `BlockRevenue` commission (SIMD-0123, priority-fee/MEV revenue sharing) is a distinct distribution path from the inflation-rewards epoch calculation shown above, and no equivalent delay/snapshot mechanism for `block_revenue_commission_bps` was found in the reachable code. The result is that `CommissionKind::BlockRevenue` updates via `update_commission_bps` have neither an in-epoch timing gate (like legacy commission) nor a cross-epoch delay (like inflation rewards commission) — they are immediate and unrestricted.

### Impact Explanation
An authorized withdrawer can raise `block_revenue_commission_bps` to 10,000 (100%) in the same slot or immediately before block-revenue distribution, diverting the entire share of priority fees/MEV revenue that would otherwise flow to delegators/stakers sharing in block revenue, with zero warning window. This mirrors the DODO finding's core issue — an owner-controlled fee parameter that unilaterally and instantly changes economic terms affecting third parties (stakers) who have no opportunity to react (e.g., by undelegating) before the change takes effect. This is a state-mutation/economic-fairness issue rather than a memory-safety or consensus-divergence bug, but it directly reproduces the "lack of timelock for fee change" bug class from the report.

### Likelihood Explanation
Any vote account's authorized withdrawer can trigger this by submitting an ordinary `VoteInstruction` transaction; no special privilege beyond normal account authority is required, and there is no cooldown, feature gate, or governance delay blocking the action for `BlockRevenue` commission.

### Recommendation
Apply the same half-epoch/delay protections used for legacy `update_commission` and for `InflationRewards` commission (via `delay_commission_updates`) to `BlockRevenue` commission increases in `update_commission_bps`, or otherwise ensure block-revenue distribution reads a commission snapshot from a prior epoch/slot boundary so stakers have advance notice before an increase takes effect.

### Proof of Concept
1. Authorized withdrawer of a vote account submits a `VoteInstruction::UpdateCommissionBps` instruction (per `programs/vote/src/vote_state/mod.rs` `update_commission_bps`) with `kind = CommissionKind::BlockRevenue` and `commission_bps = 10000`.
2. The instruction succeeds unconditionally (no epoch-position check, no signer other than authorized withdrawer required) as shown in [5](#0-4) .
3. Block-revenue distribution for subsequent blocks in the same epoch immediately uses the new 100% commission, since no delay/snapshot mechanism analogous to `delay_commission_updates` (seen only in the inflation-rewards path at `runtime/src/bank/partitioned_epoch_rewards/calculation.rs:703-724`) protects this value.

Note: I was unable to locate the block-revenue distribution/payout code path itself within the indexed portion of the codebase to confirm there is no independent delay mechanism specific to block revenue; if such a mechanism exists elsewhere, it would need to be checked directly (a Devin session with full repository access could confirm this).

### Citations

**File:** programs/vote/src/vote_state/mod.rs (L806-815)
```rust
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
