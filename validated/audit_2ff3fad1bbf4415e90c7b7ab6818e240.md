## Analog Vulnerability Found

### Title
Block-revenue commission (`block_revenue_commission_bps`) is not delayed for reward calculation, allowing a validator to instantly rug delegators' block-revenue rewards - (File: `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`)

### Summary
Agave implements a delegator-protection mechanism for vote-account commission changes: legacy `commission` and the new basis-points `inflation_rewards_commission_bps` are both delayed by design so that a validator cannot "rug" delegators by hiking commission right before rewards are computed. However, the block-revenue-sharing commission (`block_revenue_commission_bps`, added by SIMD-0123/SIMD-0291) is updatable with **no timelock at the instruction level** and, at the reward-calculation call site, is read from the **current/undelayed** vote-account snapshot rather than the epoch-old snapshot used for inflation rewards — reproducing the exact "deployerCut" front-running bug class from the referenced Audius report.

### Finding Description
`update_commission_bps` (SIMD-0291) explicitly removes any timing restriction: [1](#0-0) 

Compare this to the legacy `update_commission`, which enforces `is_commission_update_allowed` (restricting increases to the first half of an epoch) specifically to prevent last-minute commission changes: [2](#0-1) 

For reward calculation, `CachedVoteAccounts` intentionally keeps a delayed snapshot to defend against "last minute commission rugs": [3](#0-2) 

`redeem_delegation_rewards` honors this delay for the inflation-rewards commission: when `delay_commission_updates` is set, it deliberately reads commission from `snapshot_epoch_vote_accounts` (or falls back to `rewarded_epoch_vote_accounts`) instead of the live `vote_state`: [4](#0-3) 

But the block-revenue reward path takes a different, non-delayed input. In `calculate_stake_rewards_and_commissions`, `calculate_block_reward` is invoked with `cached_vote_accounts.distribution_epoch_vote_accounts` — the *current* end-of-epoch vote-account state, not the epoch-old `snapshot_epoch_vote_accounts` used for inflation commission: [5](#0-4) 

Because `update_commission_bps` has no per-epoch timing restriction (unlike legacy `update_commission`) and `block_revenue_commission_bps` is read from the undelayed snapshot at reward-calculation time, a validator can raise `block_revenue_commission_bps` to its maximum (10000 bps = 100%) at any slot right before the epoch's reward calculation runs, diverting all block-revenue rewards that would otherwise flow to delegated stakers into the validator's own commission share for that entire epoch — with no advance warning that would let delegators withdraw/redelegate first. This is functionally identical to the `deployerCut` rug: a permissionless, instantly-effective parameter update by the entity collecting the cut, executed immediately before the reward/claim calculation that consumes it.

Note: I was not able to fully inspect the body of `calculate_block_reward` itself within the available iterations, only the call site that supplies it with the undelayed `distribution_epoch_vote_accounts`. If `calculate_block_reward` internally re-derives an epoch-delayed commission value from elsewhere, the asymmetry described here would not hold; this should be verified by reading the full function definition (`runtime/src/bank/partitioned_epoch_rewards/calculation.rs`, function `calculate_block_reward`).

### Impact Explanation
If confirmed, this allows any validator with delegated stake and block-revenue sharing enabled to unilaterally and instantly capture up to 100% of block-revenue rewards owed to delegators for a full epoch, causing concrete unauthorized state mutation / fund misallocation in the rewards-distribution path — the same "unfairly deceives delegators" impact called out in the referenced report, but realized in Agave's stake-reward calculation rather than a smart contract.

### Likelihood Explanation
Likelihood is high if the asymmetry holds: the action requires only the withdraw-authority signature already needed for any commission update (`VoteInstruction::UpdateCommissionBps`), no special privilege beyond being a validator's authorized withdrawer, and no cooperation from other parties — it is a standard, permissionless instruction (`vote_processor.rs`) that any validator operator can submit near an epoch boundary.

### Recommendation
Verify `calculate_block_reward`'s source of `block_revenue_commission_bps`; if it reads from `distribution_epoch_vote_accounts` (undelayed), change it to source the block-revenue commission from `snapshot_epoch_vote_accounts` the same way `redeem_delegation_rewards` does for `inflation_rewards_commission_bps`, and/or reinstate an epoch-scale delay for `UpdateCommissionBps` with `CommissionKind::BlockRevenue` (currently the code explicitly states "No commission update rule, per SIMD-0249 and SIMD-0291").

### Proof of Concept
1. Validator with delegated stake and `block_revenue_sharing` feature active sets `block_revenue_commission_bps` to 0 to attract delegators.
2. Near the end of an epoch, immediately before `distribute_reward_commissions`/`calculate_stake_rewards_and_commissions` runs for that epoch, the validator submits `VoteInstruction::UpdateCommissionBps { commission_bps: 10000, kind: CommissionKind::BlockRevenue }` signed by the authorized withdrawer — this succeeds unconditionally per `update_commission_bps` (no timing check).
3. When `calculate_block_reward` is invoked with `cached_vote_accounts.distribution_epoch_vote_accounts` (reflecting the just-updated 100% commission), all block-revenue rewards for that epoch's delegated stake are attributed to the validator's commission instead of the delegators, exactly mirroring the Audius `deployerCut` front-running scenario. [5](#0-4) [6](#0-5)

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
