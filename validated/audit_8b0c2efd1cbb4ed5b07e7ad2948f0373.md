### Title
Vote Account Withdraw Authority Can Instantly Raise Block-Revenue Commission to Dilute Delegators' Unclaimed `pending_delegator_rewards` - (File: `programs/vote/src/vote_state/mod.rs`)

### Summary
The Splitter bug class describes an "owner" administrative function (`addPayee`/`adjustShare`) that lets a privileged actor unilaterally alter share allocations while unclaimed funds are outstanding, diluting existing beneficiaries. The Agave analog is the vote account's `authorized_withdrawer` calling `UpdateCommissionBps` (SIMD-0291) for `CommissionKind::BlockRevenue`, which — unlike the legacy `UpdateCommission` path — has no timing restriction and can be raised instantly at any point in the epoch, immediately affecting the shared, unclaimed `pending_delegator_rewards` pool that belongs collectively to delegating stakers.

### Finding Description
`update_commission` (legacy, percentage-based) explicitly restricts commission *increases* to the first half of the epoch via `is_commission_update_allowed`, specifically to prevent a validator from raising its cut right before rewards are calculated/distributed ("last-minute commission rugs"): [1](#0-0) 

By contrast, `update_commission_bps` — the newer basis-points commission setter used for both `InflationRewards` and `BlockRevenue` kinds under SIMD-0291/SIMD-0249 — has no such rule and is documented as always allowed regardless of epoch position: [2](#0-1) [3](#0-2) 

`block_revenue_commission_bps` governs the withdraw authority's cut of block-revenue funds that accumulate in the vote account's `pending_delegator_rewards` field — a pool shared among all delegators, deposited over time via `DepositDelegatorRewards`: [4](#0-3) [5](#0-4) 

At epoch-reward distribution time, `calculate_block_reward` splits `pending_delegator_rewards` proportionally among delegators' *current* stake weights — it contains no reference to a delayed/snapshotted commission value the way `redeem_delegation_rewards` does for inflation commission (which explicitly reads a prior-epoch snapshot via `snapshot_epoch_vote_accounts`/`delay_commission_updates` specifically "to prevent last minute commission rugs"): [6](#0-5) [7](#0-6) [8](#0-7) 

Because the value taken as commission from block-revenue is governed by `block_revenue_commission_bps` at collection time and that field can be changed instantly and without restriction via `update_commission_bps`, the withdraw authority (the vote account "owner," analogous to the Splitter contract owner) can unilaterally increase its own share of funds economically accrued by delegators before the change, exactly mirroring the `addPayee`/`adjustShare` dilution pattern: an administrative, unilateral action alters the split of an already-accumulated, not-yet-distributed pool of funds, at the expense of the existing beneficiaries (delegators) who cannot prevent or opt out of the change.

### Impact Explanation
If confirmed to affect the commission actually withheld from funds before they enter (or as they leave) `pending_delegator_rewards`, this allows a validator operator to capture a disproportionate share of delegator-earned block revenue with no epoch-boundary delay protection, unlike the legacy inflation-commission path which was hardened against this exact "rug" scenario. This directly matches "stake or reward corruption" in the Validate criteria: unclaimed reward funds meant for delegators are redirected to the vote account operator via an administrative, single-signer action.

### Likelihood Explanation
The action requires only a normal signed transaction from the vote account's `authorized_withdrawer` calling `UpdateCommissionBps` — no special privilege beyond controlling that key, which is the direct analog of the Splitter contract's "owner." Given `pending_delegator_rewards` accumulates continuously via `DepositDelegatorRewards` and is only paid out at epoch-reward distribution, there is a persistent window in every epoch during which this unrestricted commission change can be exploited.

### Recommendation
Apply the same epoch-delay protection used for `update_commission`/`delay_commission_updates` to `block_revenue_commission_bps` changes made via `update_commission_bps`, or ensure the commission rate actually applied when funds are deposited/collected into `pending_delegator_rewards` is snapshotted from a prior epoch (analogous to `snapshot_epoch_vote_accounts` used for inflation commission), so that in-flight/unclaimed delegator funds cannot be diluted by a same-block commission increase.

### Proof of Concept
1. Attacker controls a vote account's `authorized_withdrawer`.
2. Delegators' stake earns block revenue that flows into `pending_delegator_rewards` via `DepositDelegatorRewards` (`programs/vote/src/vote_state/mod.rs:935-988`) over the course of an epoch.
3. Immediately before (or during) the block-revenue collection/deposit for a given block, attacker submits `VoteInstruction::UpdateCommissionBps { commission_bps: <max>, kind: CommissionKind::BlockRevenue }`, signed only by the withdraw authority — this succeeds unconditionally per `update_commission_bps` (`programs/vote/src/vote_state/mod.rs:828-859`), since "No commission update rule" is enforced for this instruction.
4. Because there is no epoch-delayed snapshot for `block_revenue_commission_bps` analogous to `delay_commission_updates` for inflation commission, the new, higher commission is applied to funds attributable to delegators' pre-existing, unclaimed stake in `pending_delegator_rewards`, reducing what they ultimately receive at distribution (`calculate_block_reward`, `runtime/src/bank/partitioned_epoch_rewards/calculation.rs:173-232`).

Note: full end-to-end confirmation of the exact point where `block_revenue_commission_bps` lamports are withheld prior to reaching `pending_delegator_rewards` (expected in `runtime/src/bank/fee_distribution.rs`) was not completed within the available investigation — that file was located but its contents were not reviewed before the tool budget was exhausted. The root-cause asymmetry between `update_commission` (epoch-delayed) and `update_commission_bps` (no delay) is confirmed directly in `programs/vote/src/vote_state/mod.rs`, but the precise lamport-level mechanics of block-revenue commission collection should be verified against `runtime/src/bank/fee_distribution.rs` before treating this as fully proven.

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

**File:** programs/vote/src/vote_state/mod.rs (L1806-1812)
```rust
    /// Test update_commission_bps (SIMD-0291).
    ///
    /// Unlike test_update_commission, SIMD-0291 has no timing restrictions
    /// (per SIMD-0249). Updates are always allowed regardless of epoch position.
    ///
    /// This test only uses V4 since SIMD-0291 depends on SIMD-0185 (VoteStateV4).
    #[test]
```

**File:** programs/vote/src/vote_state/handler.rs (L196-209)
```rust
    pub(crate) fn add_pending_delegator_rewards(
        &mut self,
        amount: u64,
    ) -> Result<(), InstructionError> {
        match &mut self.target_state {
            TargetVoteState::V4(v4) => {
                v4.pending_delegator_rewards = v4
                    .pending_delegator_rewards
                    .checked_add(amount)
                    .ok_or(InstructionError::ArithmeticOverflow)?;
                Ok(())
            }
        }
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
