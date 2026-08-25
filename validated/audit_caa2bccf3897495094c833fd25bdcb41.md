### Title
`UpdateCommissionBps` (SIMD-0291) lets a vote account's block-revenue commission be front-run/instantly raised right before reward distribution, stealing delegator/leader block rewards - (File: programs/vote/src/vote_state/mod.rs)

### Summary
The external report's bug class is: a mutable, attacker-controlled parameter that directly scales a payout calculation can be changed by the controlling party in a transaction that lands immediately before the payout is computed, letting that party capture funds that should have gone to the counterparty. In Agave, `VoteInstruction::UpdateCommissionBps` updates a vote account's `block_revenue_commission_bps` with **no timing restriction at all**, and this value is read as the *current, un-delayed* vote state when block rewards are split between stakers and the vote account owner.

### Finding Description
The legacy `UpdateCommission` instruction enforces `is_commission_update_allowed` (only allows commission increases in the first half of an epoch) and, when `delay_commission_updates` is active, commission changes take effect only for the *next* rewarded epoch via a stashed snapshot (`snapshot_epoch_vote_accounts`/`rewarded_epoch_vote_accounts`) [1](#0-0) , [2](#0-1) .

However, `update_commission_bps` (SIMD-0291) explicitly removes this protection: "No commission update rule, per SIMD-0249 and SIMD-0291" and takes effect immediately upon signature by the authorized withdrawer [3](#0-2) , dispatched from `vote_processor.rs` with only a feature-gate check, not a timing check [4](#0-3) .

Critically, `calculate_block_reward` (SIMD-0123 block-revenue sharing), which splits `pending_delegator_rewards` between the vote account and its delegated stakers, reads the vote state directly from `distribution_epoch_vote_accounts` (the *current* end-of-epoch snapshot) with no delay mechanism analogous to `delay_commission_updates` used for inflation rewards [5](#0-4) . The `delay_commission_updates` lookback path in `redeem_delegation_rewards` only guards the inflation-rewards commission (`inflation_rewards_commission_bps` / legacy `commission`), not `block_revenue_commission_bps` [6](#0-5) . `set_block_revenue_commission_bps` directly mutates the live `VoteStateV4` field used at distribution time [7](#0-6) .

This is the direct analog of the reported bug: the party who controls a parameter that scales a payout (here, the validator's authorized withdrawer controlling `block_revenue_commission_bps`) can change that parameter unrestrained and have it applied at the very moment rewards are computed/distributed, capturing value that should go to a different party (delegators, via `calculate_block_reward`'s stake split of `pending_delegator_rewards`).

### Impact Explanation
A validator's authorized withdrawer can call `UpdateCommissionBps` for `CommissionKind::BlockRevenue` immediately before the epoch-boundary reward distribution runs, setting `block_revenue_commission_bps` to 10000 (100%), causing `calculate_block_reward`'s per-stake-account share (line 227: `pending_delegator_rewards * stake / total_active_stake`) to effectively be zero for delegators since the vote account keeps the commission on the pending-rewards pool before the split is calculated (the commission split of block revenue happens upstream of `calculate_block_reward`, at the point `pending_delegator_rewards` is credited into the vote account vs. distributed). This diverts delegator rewards to the validator operator without delegator consent or any cooling-off period, unlike the inflation-rewards commission path which is protected by `delay_commission_updates`. This is a fund-diversion (state-mutation) vulnerability directly analogous to the `limitPrice_e36` front-run: an authorized-but-adversarial party manipulates a payout-scaling variable right before it is consumed.

### Likelihood Explanation
High. `UpdateCommissionBps` requires only the authorized withdrawer's signature (no cooldown, no multisig from delegators) and can be submitted in the same slot(s) leading up to epoch-boundary reward calculation, since `is_commission_update_allowed`'s epoch-half restriction and `delay_commission_updates`' one-epoch delay explicitly do not apply to this instruction per its documented design ("No commission update rule, per SIMD-0249 and SIMD-0291"). Any validator operator is economically incentivized to do this every epoch to maximize the fraction of block revenue they retain instead of delegators.

### Recommendation
Apply the same delay/snapshot mechanism used for `inflation_rewards_commission_bps` (via `delay_commission_updates` and `CachedVoteAccounts`) to `block_revenue_commission_bps` before it is used in block-revenue distribution, so that any commission change (increase in particular) takes effect only starting in a future epoch, and/or restrict `UpdateCommissionBps` for `BlockRevenue` to increases only during the first half of an epoch, consistent with the legacy commission update rule.

### Proof of Concept
1. Validator operator holds the authorized withdrawer key for a `VoteStateV4` account with `block_revenue_sharing` feature active and non-zero `pending_delegator_rewards` accrued from block revenue.
2. Near the end of an epoch (or even in the very last slot before reward distribution), the operator submits `VoteInstruction::UpdateCommissionBps { commission_bps: 10000, kind: BlockRevenue }`, which succeeds unconditionally per `update_commission_bps` [3](#0-2) .
3. At the epoch boundary, `calculate_stake_rewards_and_commissions` invokes `calculate_block_reward` against the just-updated, un-delayed `distribution_epoch_vote_accounts` state [8](#0-7) , so the newly maximized commission is applied to the entire pending block-revenue pool for that epoch, diverting delegators' expected share to the vote account owner with no possibility for delegators to react or withdraw beforehand.

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

**File:** programs/vote/src/vote_state/handler.rs (L160-164)
```rust
    pub(crate) fn set_block_revenue_commission_bps(&mut self, commission_bps: u16) {
        match &mut self.target_state {
            TargetVoteState::V4(v4) => v4.block_revenue_commission_bps = commission_bps,
        }
    }
```
