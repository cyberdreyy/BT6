### Title
Vote account withdraw authority can front-run block-revenue reward distribution by changing `BlockRevenue` commission with no delay - ([File: programs/vote/src/vote_state/mod.rs])

### Summary
The Napier issue is that a privileged owner can freely change a fee parameter that is applied at swap time, with no delay, and front-run users to capture more value. The Agave analog is the vote-account commission mechanism: while the legacy percentage-based commission update path enforces a same-epoch increase restriction and the newer `delay_commission_updates` feature defers the effect of inflation-reward commission changes by snapshotting vote state from an earlier epoch, the new basis-points commission update path for `CommissionKind::BlockRevenue` has **no timing restriction whatsoever**, and the block-revenue reward calculation reads the commission live from the current (undelayed) vote account state.

### Finding Description
`update_commission` (legacy percentage commission) enforces `is_commission_update_allowed`, blocking commission *increases* in the second half of an epoch when `delay_commission_updates` is inactive, and when the feature is active, `redeem_delegation_rewards` explicitly reads the commission from a snapshot taken up to a full epoch earlier: [1](#0-0) [2](#0-1) 

In contrast, `update_commission_bps` (SIMD-0291), which is used for both `CommissionKind::InflationRewards` and `CommissionKind::BlockRevenue`, explicitly has *no* commission update rule ("No commission update rule, per SIMD-0249 and SIMD-0291") and can be called by the authorized withdrawer at any slot: [3](#0-2) 
This is confirmed by the accompanying test comment: "Unlike test_update_commission, SIMD-0291 has no timing restrictions... Updates are always allowed regardless of epoch position." [4](#0-3) 

Critically, while the `delay_commission_updates` snapshot protection is applied to the `InflationRewards` commission_bps in `redeem_delegation_rewards`, the `BlockRevenue` commission used to compute block rewards is read directly from `cached_vote_accounts.distribution_epoch_vote_accounts` — the **current**, undelayed vote-account state — via `calculate_block_reward`, invoked unconditionally whenever `block_revenue_sharing` is enabled: [5](#0-4) 

This means the withdraw authority of a vote account can lower `block_revenue_commission_bps` to attract delegated stake, then raise it to the maximum right before block-revenue rewards are calculated/redeemed for stake accounts, and lower it again afterward — exactly the "set low fee to attract users, raise fee right before charge, then lower again" pattern described in the Napier report, but applied to validator commission on block-revenue rewards rather than swap fees.

### Impact Explanation
Delegators to a vote account have no on-chain guarantee about what commission will actually be applied to their block-revenue rewards, because the withdraw authority can change `block_revenue_commission_bps` at will and have it take effect on the very next reward calculation. This allows a malicious or opportunistic validator/withdraw authority to systematically extract more value from delegators than the commission rate they observed when delegating, undermining the fairness guarantees that the `delay_commission_updates`/snapshot mechanism was specifically designed to provide for the equivalent `InflationRewards` path.

### Likelihood Explanation
The withdraw authority signature is the only requirement to call `UpdateCommissionBps` with `CommissionKind::BlockRevenue`, and no epoch-timing or delay check gates it. Any validator operator already controls this authority, so the exploit requires no privilege escalation — only mistimed transaction submission relative to reward calculation, which is entirely within the operator's control since they control their own withdraw-authority signing.

### Recommendation
Extend the `delay_commission_updates` snapshot-based protection (already applied to `InflationRewards` commission_bps in `redeem_delegation_rewards`) to the `BlockRevenue` commission_bps used by `calculate_block_reward`, so that block-revenue commission changes only take effect after the same one-epoch delay as inflation-reward commission changes.

### Proof of Concept
1. Withdraw authority sets `block_revenue_commission_bps` low (e.g., 0) via `VoteInstruction::UpdateCommissionBps { kind: BlockRevenue, .. }` to attract delegated stake — allowed unconditionally per `update_commission_bps`: [6](#0-5) 
2. Immediately before block-revenue reward calculation for a slot/epoch, the authority raises `block_revenue_commission_bps` to the maximum.
3. `calculate_stake_rewards_and_commissions` invokes `calculate_block_reward` using the live, undelayed `distribution_epoch_vote_accounts` state, applying the just-raised commission to all delegators' block rewards: [5](#0-4) 
4. The authority lowers the commission again afterward, repeating the cycle each reward calculation.

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
