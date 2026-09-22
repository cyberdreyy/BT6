## Title
Withdraw authority can instantly set vote account commission to 100% with no timelock, silently stealing all staker rewards - (File: `programs/vote/src/vote_state/mod.rs`)

### Summary
The Sherlock report describes a market coordinator that can change critical parameters (margin/maintenance ratios) with no upper limit and no timelock, letting it instantly harm all market participants. The Agave analog is `update_commission_bps`, the SIMD‑0291 commission-update path for `VoteStateV4`, which explicitly removes both the timing restriction and any parameter bound that the legacy `update_commission` path enforced, allowing the vote account's withdraw authority to instantaneously reset commission to effectively 100% and capture an entire epoch's stake rewards from delegators who have no time to react.

### Finding Description
The legacy commission-update instruction, `update_commission`, restricts commission *increases* to the first half of an epoch via `is_commission_update_allowed`, giving stakers a window to react (e.g., to redelegate) before a hostile rate hike takes effect: [1](#0-0) [2](#0-1) 

By contrast, `update_commission_bps` (SIMD‑0291) deliberately removes this rule ("No commission update rule, per SIMD-0249 and SIMD-0291") and only requires the withdraw authority's signature — there is no epoch-timing gate and no upper bound check on the submitted `commission_bps` value at the instruction level: [3](#0-2) 

This is confirmed by the accompanying unit test, which documents the removed restriction and shows values above 100% (10,000 bps), e.g. 150% and 500%, are accepted at the program level: [4](#0-3) 

The withdraw authority here plays the role of the "coordinator" in the analog report: a single, privileged (but otherwise unprivileged-transaction-reachable via one signed instruction) party controls a parameter that directly determines how much of the block/stake reward pool goes to itself versus to third-party stakers, with no limit and no delay before the change takes effect.

At reward-distribution time the resulting bps value is only clamped (not rejected) to 10,000 (100%) in `commission_split`/`commission_split_preserve_lamports`: [5](#0-4) [6](#0-5) 

meaning any commission_bps ≥ 10,000 (including the allowed >100% values) results in the voter (vote account/commission collector) receiving 100% of the epoch's stake reward and the staker receiving 0%, an outcome achievable in a single transaction the moment before rewards are calculated for the current epoch.

### Impact Explanation
Because there is no timing restriction on `update_commission_bps`, and because whether the effect is delayed by an epoch depends on a separate `delay_commission_updates` feature flag consulted in the epoch-rewards calculation path: [7](#0-6) 

a malicious or compromised withdraw authority can, at any slot, submit a single `UpdateCommissionBps` instruction setting commission to 10,000+ bps just before the reward-distribution point for the current epoch (when `delay_commission_updates` is not active for the relevant code path, or by acting during the window it doesn't cover), diverting the entire epoch's stake reward for every delegator on that vote account to itself. Delegators cannot practically defend themselves because stake deactivation requires at least one epoch of cooldown, so they cannot exit before the theft occurs — directly mirroring the report's "coordinator sets extreme parameters, then extracts value from users who cannot react in time" pattern. This is a concrete unsigned/undisclosed fund diversion of stake rewards away from their rightful delegators.

### Likelihood Explanation
Requires the vote account's authorized withdrawer to act maliciously (a privileged but realistically obtainable role — many withdraw authorities are held by third-party staking-as-a-service providers or automated hot wallets, distinct from the identity actually running the validator node or the delegators trusting it). No other preconditions (no special network state, no race with other validators) are needed; it is a single signed transaction from an already-existing authority key, executed via the ordinary vote program instruction path. Whether the theft is fully realized (versus delayed by one epoch) depends on the `delay_commission_updates` feature/flag being active for that reward accounting path; where it is not yet active or does not cover the instantaneous commission read, the exploit is immediately effective.

### Recommendation
Apply the same design already used for legacy commission: enforce an epoch-timing restriction (or a mandatory one-epoch delay via `delay_commission_updates`, made unconditional rather than feature-gated) for `update_commission_bps` as well, and reject `commission_bps` values above 10,000 at the instruction level (`InstructionError::InvalidInstructionData`) instead of silently clamping to 100% at reward-calculation time. This restores the "sanity upside limit + advance notice" mitigations recommended in the analog report.

### Proof of Concept
1. Withdraw authority signs and submits `VoteInstruction::UpdateCommissionBps(10_000+, CommissionKind::InflationRewards)` for its vote account, exercised in `update_commission_bps`: [3](#0-2) .
2. No epoch-timing check blocks this, unlike `update_commission`'s `is_commission_update_allowed` gate: [2](#0-1) .
3. At the next reward-distribution calculation, `commission_split`/`commission_split_preserve_lamports` clamp the bps value to `MAX_BPS = 10_000`, sending 100% of the stake reward to the voter and 0% to the staker: [5](#0-4) .
4. Existing unit tests already confirm bps values above 10,000 are accepted by the program with no error (`commission_bps_roundtrip(15_000)`, `commission_bps_roundtrip(50_000)`): [8](#0-7) .

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

**File:** programs/vote/src/vote_state/mod.rs (L1806-1935)
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

        let serialized = vote_state.serialize();
        let serialized_len = serialized.len();
        let rent = Rent::default();
        let lamports = rent.minimum_balance(serialized_len);
        let mut vote_account = AccountSharedData::new(lamports, serialized_len, &id());
        vote_account.set_data_from_slice(&serialized);

        let processor_account = AccountSharedData::new(0, 0, &solana_sdk_ids::native_loader::id());
        let mut transaction_context = TransactionContext::new(
            vec![(id(), processor_account), (node_pubkey, vote_account)],
            rent,
            0,
            0,
            1,
        );
        transaction_context
            .configure_top_level_instruction_for_tests(
                0,
                vec![InstructionAccount::new(1, false, true)],
                vec![],
            )
            .unwrap();
        let instruction_context = transaction_context.get_next_instruction_context().unwrap();
        let mut borrowed_account = instruction_context
            .try_borrow_instruction_account(0)
            .unwrap();

        let signers: HashSet<Pubkey> = vec![withdrawer_pubkey].into_iter().collect();
        let non_signers: HashSet<Pubkey> = HashSet::new();

        // `CommissionKind::BlockRevenue` returns `InvalidInstructionData` when
        // block_revenue_sharing is disabled.
        assert_eq!(
            update_commission_bps(
                &mut borrowed_account,
                target_version,
                500,
                CommissionKind::BlockRevenue,
                &signers,
                false, // block_revenue_sharing disabled
            ),
            Err(InstructionError::InvalidInstructionData)
        );

        // Missing signature returns `MissingRequiredSignature`.
        assert_eq!(
            update_commission_bps(
                &mut borrowed_account,
                target_version,
                500,
                CommissionKind::InflationRewards,
                &non_signers,
                false,
            ),
            Err(InstructionError::MissingRequiredSignature)
        );

        // Incorrect signature for withdraw authority returns `MissingRequiredSignature`.
        let wrong_signers: HashSet<Pubkey> = vec![Pubkey::new_unique()].into_iter().collect();
        assert_eq!(
            update_commission_bps(
                &mut borrowed_account,
                target_version,
                500,
                CommissionKind::InflationRewards,
                &wrong_signers,
                false,
            ),
            Err(InstructionError::MissingRequiredSignature)
        );

        let mut commission_bps_roundtrip = |new_commission_bps: u16| {
            update_commission_bps(
                &mut borrowed_account,
                target_version,
                new_commission_bps,
                CommissionKind::InflationRewards,
                &signers,
                false,
            )
            .unwrap();
            update_commission_bps(
                &mut borrowed_account,
                target_version,
                new_commission_bps,
                CommissionKind::BlockRevenue,
                &signers,
                true,
            )
            .unwrap();
            let handler =
                get_vote_state_handler_checked(&borrowed_account, target_version).unwrap();
            assert_eq!(
                handler.as_ref_v4().inflation_rewards_commission_bps,
                new_commission_bps
            );
            assert_eq!(
                handler.as_ref_v4().block_revenue_commission_bps,
                new_commission_bps
            );
        };

        // There's no timing check for SIMD-0291, so just go back and forth
        // with new values.

        commission_bps_roundtrip(1_100); // Increase to 11%
        commission_bps_roundtrip(5_000); // Increase to 50%
        commission_bps_roundtrip(4_400); // Decrease to 44%
        commission_bps_roundtrip(4_600); // Increase to 46%

        // Values > 10,000 bps are allowed at program level.
        commission_bps_roundtrip(15_000); // 150%
        commission_bps_roundtrip(50_000); // 500%
    }
```

**File:** runtime/src/inflation_rewards/mod.rs (L376-406)
```rust
#[cfg_attr(any(test, feature = "dev-context-only-utils"), qualifiers(pub(crate)))]
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

**File:** runtime/src/inflation_rewards/mod.rs (L412-435)
```rust
#[cfg_attr(any(test, feature = "dev-context-only-utils"), qualifiers(pub(crate)))]
fn commission_split_preserve_lamports(commission_bps: u16, on: u64) -> (u64, u64, bool) {
    const MAX_BPS: u16 = 10_000;
    const MAX_BPS_U128: u128 = MAX_BPS as u128;
    match commission_bps.min(MAX_BPS) {
        0 => (0, on, false),
        MAX_BPS => (on, 0, false),
        split => {
            let staker_bps = MAX_BPS
                .checked_sub(split)
                .expect("commission cannot be greater than MAX_BPS");
            let staker_rewards = u128::from(on)
                .checked_mul(u128::from(staker_bps))
                .expect("multiplication of a u64 and u16 should not overflow")
                / MAX_BPS_U128;
            let staker_rewards = staker_rewards as u64;
            let voter_rewards = on
                .checked_sub(staker_rewards)
                .expect("staker rewards cannot exceed total rewards");

            (voter_rewards, staker_rewards, true)
        }
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
