### Title
Vote account BlockRevenue/Inflation commission (in basis points) can be set to 100% instantly by authorized withdrawer with no rate-limit, enabling front-run theft of stake rewards - (File: programs/vote/src/vote_state/mod.rs)

### Summary
The `update_commission_bps` instruction handler lets the vote account's authorized withdrawer set the inflation-rewards or block-revenue commission (in basis points, up to 100%) with no timing restriction whatsoever, unlike the legacy `update_commission` path which enforces an epoch-boundary rate-limiting rule for commission increases.

### Finding Description
`update_commission_bps` only checks that the authorized withdrawer signed, and explicitly documents "No commission update rule, per SIMD-0249 and SIMD-0291": [1](#0-0) 

This directly contrasts with the legacy `update_commission` function, which enforces `is_commission_update_allowed` (an epoch-based cooldown) whenever the new commission is greater than the current one: [2](#0-1) 

The commission value set via `update_commission_bps` directly controls how inflation rewards (and, when block revenue sharing is enabled, block rewards) are split between the vote account (validator) and delegated stakers, via `commission_split`/`commission_split_preserve_lamports`, which allow bps values effectively clamped to [0, 10000] (0%-100%): [3](#0-2) [4](#0-3) 

This is structurally the same bug class as the Fractional royalty issue: the entity that fully controls the "fee percentage" parameter (here, the vote account's authorized withdrawer, analogous to the vault owner/controller) can change it instantaneously and unboundedly (0%→100%) with no timelock, whereas ordinary participants (delegators/stakers, analogous to fTokens holders) have no say and cannot react in time.

### Impact Explanation
An authorized withdrawer who also runs (or colludes with) the validator can set a low, attractive `inflation_rewards_commission_bps`/`block_revenue_commission_bps` (e.g., near 0) to attract delegations, then immediately before an epoch's reward distribution set it to 10,000 bps (100%), diverting the entirety of that epoch's stake rewards from delegators to themselves, then revert it back afterward. Because there is no cooldown for `update_commission_bps` (unlike the older `update_commission` path, whose rate-limit is also asymmetric — it only restricts increases and only if `disable_commission_update_rule` is false), this results in unauthorized redirection of staker funds — a direct funds-loss impact for delegators, mirroring the "arbitrary royalty" theft pattern in the referenced report.

### Likelihood Explanation
This is reachable by any ordinary vote-account authorized withdrawer via a standard, unprivileged `VoteInstruction` transaction — no special validator privilege, leaked keys, or node-level access is required beyond normal control of one's own vote account keys. The commission update is applied at the vote-account level and takes effect for the current/next epoch's reward computation; the exact timing window (whether it must be delayed via `delay_commission_updates` feature) depends on feature-gate configuration, which is not fully confirmed here.

### Recommendation
Apply the same rate-limiting / cooldown rule used by `update_commission` (`is_commission_update_allowed`) to `update_commission_bps` as well, or add an explicit epoch-boundary delay before an increased commission takes effect on `inflation_rewards_commission_bps`/`block_revenue_commission_bps`, so delegators have a guaranteed window to react (undelegate) before a commission hike applies.

### Proof of Concept
Not independently reproduced against a live cluster; based on static analysis of `update_commission_bps` (no update-rule branch) versus `update_commission` (enforces `is_commission_update_allowed`), combined with `commission_split` allowing bps up to 10,000 (100%). Confirming the exact effective-epoch semantics (whether `delay_commission_updates` mitigates this for the bps-based path) would require checking feature-gate activation and reward-distribution timing tests, which were not fully traced in this pass.

### Citations

**File:** programs/vote/src/vote_state/mod.rs (L797-825)
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

**File:** runtime/src/inflation_rewards/mod.rs (L377-406)
```rust
fn commission_split(commission_bps: u16, on: u64) -> (u64, u64, bool) {
    const MAX_BPS: u16 = 10_000;
    const MAX_BPS_U128: u128 = MAX_BPS as u128;
    match commission_bps.min(MAX_BPS) {
        0 => (0, on, false),
        MAX_BPS => (on, 0, false),
        split => {
            let on = u128::from(on);
            // Calculate mine and theirs independently and symmetrically instead of
            // using the remainder of the other to treat them strictly equally.
            // In Tower, this is also to cancel the rewarding if either of the parties
            // should receive only fractional lamports, resulting in not being rewarded at all.
            // Thus, note that we intentionally discard any residual fractional lamports.
            let mine = on
                .checked_mul(u128::from(split))
                .expect("multiplication of a u64 and u16 should not overflow")
                / MAX_BPS_U128;
            let theirs = on
                .checked_mul(u128::from(
                    MAX_BPS
                        .checked_sub(split)
                        .expect("commission cannot be greater than MAX_BPS"),
                ))
                .expect("multiplication of a u64 and u16 should not overflow")
                / MAX_BPS_U128;

            (mine as u64, theirs as u64, true)
        }
    }
}
```

**File:** runtime/src/inflation_rewards/mod.rs (L709-724)
```rust
                ),
            )
        );

        stake.credits_observed = ag_total_stake_multiplier;
        // this one should be able to collect exactly 1 (already observed one)
        assert_eq!(
            Some(CalculatedStakeRewards {
                staker_rewards: stake.delegation.stake,
                voter_rewards: 0,
                new_credits_observed: 2 * ag_total_stake_multiplier,
            }),
            calculate_stake_rewards(
                &stake,
                vote_state.as_ref_v4().inflation_rewards_commission_bps,
                DelegatedVoteState::from(vote_state.as_ref_v4()),
```
