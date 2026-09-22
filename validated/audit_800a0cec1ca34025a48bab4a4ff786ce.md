### Title
Vote-account authorized withdrawer can rug-pull inflation commission via unrestricted `UpdateCommissionBps` (SIMD-0291), diluting stake-reward payouts to delegators - (File: programs/vote/src/vote_state/mod.rs)

### Summary
The external report describes a vault where the admin's `setFeeConfig()` has no rate-limit or timelock, letting the admin push the fee to 100% at any moment (including right before a user's transaction) and drain value that should have gone to depositors. The closest reachable analog in this Agave repo is the vote program's `UpdateCommissionBps` instruction (SIMD-0291), which lets the vote account's authorized withdrawer change the inflation/block-revenue commission to any value, at any slot, with no epoch-timing restriction — unlike the legacy `UpdateCommission` path.

### Finding Description
The legacy commission-update path enforces a "no rug" rule: an *increase* in commission is only allowed in the first half of an epoch, via `is_commission_update_allowed`, and this check is only bypassed once the `delay_commission_updates` feature makes updates take effect a full epoch later: [1](#0-0) [2](#0-1) 

However, `update_commission_bps` (introduced for SIMD-0291) explicitly removes any timing restriction: [3](#0-2) 

This is invoked from `vote_processor.rs`'s handling of `VoteInstruction::UpdateCommissionBps`, gated only on two feature flags (`commission_rate_in_basis_points` and `delay_commission_updates`), with no per-epoch or per-slot restriction on how often or how drastically the commission can change: [4](#0-3) 

The unit test explicitly documents this behavior and shows commission can be set arbitrarily high (values up to 500%, i.e. 50,000 bps, are accepted at the program level): [5](#0-4) 

For the inflation-reward-commission side, mitigation exists at the reward-*calculation* layer: `redeem_delegation_rewards` reads the commission from a snapshot vote-account state (`snapshot_epoch_vote_accounts`/`rewarded_epoch_vote_accounts`) taken a full epoch earlier, when `delay_commission_updates` is enabled, rather than the live value: [6](#0-5) 

So a live change to `inflation_rewards_commission_bps` cannot retroactively affect rewards already computed for the current/previous epoch cycle — this delay is the direct analog to the report's recommended fix (rate-limit/timelock the fee change). This is functionally sound for the inflation-commission path.

### Impact Explanation
For the `BlockRevenue` commission kind (`CommissionKind::BlockRevenue`, guarded by the `block_revenue_sharing` feature), the same `update_commission_bps` call is equally unrestricted in timing: [7](#0-6) 
Whether block-revenue commission payouts are similarly delayed by a snapshot mechanism (as the inflation-commission path is) could not be fully confirmed within available search results — `calculate_block_reward` and its live/cached commission source were referenced (`runtime/src/bank/partitioned_epoch_rewards/calculation.rs:821-833`) but the internals of `calculate_block_reward` were not retrieved in this session, so it is uncertain whether block-revenue commission is protected by the same epoch-delay snapshot or read live at block-commit time. If it is read live, a validator operator could raise `block_revenue_commission_bps` to a very high value immediately before blocks that earn substantial revenue-sharing rewards and lower it back afterward, siphoning delegator/staker share of block revenue with no signature from affected parties and no minimum notice period — a direct analog of the reported vault fee-frontrunning issue, but confined to that specific validator's own commission/reward accounting (not a cross-validator consensus-breaking bug, since all validators would compute the same values deterministically from the same on-chain commission field and slot).

### Likelihood Explanation
Any vote account's `authorized_withdrawer` (a normal transaction signer, not a network operator/privileged role) can invoke `UpdateCommissionBps` freely once the relevant features (`commission_rate_in_basis_points`, `delay_commission_updates`, and for BlockRevenue, `block_revenue_sharing`) are active, requiring only their own signature — no special network privilege is needed, matching the "reachable from a single submitted transaction" requirement. However, the actual economic impact is limited to that validator's own delegators/stakers, not a cluster-wide or protocol-wide fund-drain, consensus divergence, or unsigned fund movement — this reduces it to essentially the same category of self-inflicted economic risk the original report describes for vault depositors (who must trust the fee-setting authority), and Agave already mitigates the inflation-reward case via epoch-delayed snapshotting.

### Recommendation
For completeness and defense-in-depth, verify (and if missing, implement) that `block_revenue_commission_bps` is read from the same epoch-delayed vote-account snapshot used for `inflation_rewards_commission_bps` in `redeem_delegation_rewards`/`calculate_block_reward`, rather than the live vote-account state, so that a withdrawer cannot instantaneously raise the block-revenue commission immediately before a high-value block and revert it afterward. If block-revenue commission is already delay-protected, no change is needed beyond documenting the delay explicitly for auditors, since `update_commission_bps`'s "no timing restriction" comment could otherwise be mistaken for a missing protection.

### Proof of Concept
Not applicable as concrete unsigned fund-movement/consensus-divergence PoC — the analysis above is a code-level review of `update_commission_bps` and its downstream reward-calculation consumers; a full PoC would require confirming the block-revenue commission read path (`calculate_block_reward`), which was not retrievable in the available search results.

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
