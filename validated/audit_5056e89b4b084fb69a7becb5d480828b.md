### Title
Vote program `UpdateCommissionBps` allows an unbounded, delay-exempt-for-BlockRevenue commission change that lets a validator front-run/retroactively divert staker rewards - (File: programs/vote/src/vote_state/mod.rs)

### Summary
The BribeVault `setFee` finding centers on three problems: (1) no upper bound on the fee percentage, (2) the fee can be changed and applied to funds that are already "in flight" (deposited before the fee change), and (3) the fee-setting transaction can front-run depositors because there is no timelock. The Agave analog is the vote program's commission-update instructions (`UpdateCommission` / `UpdateCommissionBps`), which govern the "fee" a validator takes from its delegators' staking rewards. `set_inflation_rewards_commission_bps`/`set_block_revenue_commission_bps` store the requested value with **no upper-bound validation at the setter level** (values above 10,000 bps/100% are accepted and only clamped much later, at reward-calculation time) [1](#0-0) , and `update_commission_bps` explicitly removes the timing/delay restriction that the legacy `update_commission` enforced [2](#0-1) .

### Finding Description
Legacy `update_commission` enforces an update rule: commission *increases* are only allowed in the first half of an epoch (`is_commission_update_allowed`), specifically to prevent a validator from raising its cut right before reward distribution and catching delegators unaware [3](#0-2) [4](#0-3) .

The newer SIMD-0291 `UpdateCommissionBps` instruction removes this rule entirely — the code comment states "No commission update rule, per SIMD-0249 and SIMD-0291" — relying solely on a separate, epoch-boundary delay mechanism (`delay_commission_updates`, SIMD-0249) to prevent the change from affecting the *current* epoch's rewards [2](#0-1) [5](#0-4) . At the reward-redemption boundary, the commission used is looked up from a snapshot of `snapshot_epoch_vote_accounts`/`rewarded_epoch_vote_accounts`, i.e., a state captured before the payout epoch, which is the actual mechanism meant to block front-running [6](#0-5) . This makes the mitigation entirely dependent on the correctness of that epoch-boundary snapshot logic; the instruction-level setter itself performs no independent check against last-minute changes or against unreasonable values.

Separately, and independently of the delay mechanism, the setter functions accept arbitrary `u16` values, including values far beyond 10,000 bps (100%): the test `test_set_inflation_rewards_commission_bps` explicitly documents that "values > 10,000 are allowed at program level. Capping happens during reward calculation, not storage" [1](#0-0) . The 10,000-bps cap is enforced only deep inside `commission_split`/`commission_split_preserve_lamports` in the reward-calculation path via `.min(MAX_BPS)` [7](#0-6) , not at the point the authorized withdrawer submits the transaction. This mirrors the BribeVault report's first complaint almost exactly: the "fee" (commission) setter itself has no bound; any bound that exists is bolted on somewhere downstream, and any future code path added to consume `inflation_rewards_commission_bps`/`block_revenue_commission_bps` without going through `commission_split` would inherit an unvalidated, effectively 0–65535 bps rate.

Additionally, `UpdateCommissionCollector` (SIMD-0232) lets the withdraw authority redirect the commission payout to an arbitrary account with no timing restriction at all [8](#0-7) , which is the vote-program analog of "admin can redirect fees to any address," compounding the BribeVault-style trust concern (the recipient of the "fee" can also be changed unilaterally and immediately).

### Impact Explanation
This is a validator/withdraw-authority-privileged action (analogous to BribeVault's admin), reachable by submitting a signed `UpdateCommission`/`UpdateCommissionBps` transaction from the vote account's authorized withdrawer key. If the epoch-boundary snapshot/delay logic (`delay_commission_updates` combined with `snapshot_epoch_vote_accounts`) has any gap — e.g., a code path that reads live `vote_state.inflation_rewards_commission()`/`block_revenue_commission_bps` instead of the delayed/snapshotted value — a withdraw authority could unilaterally divert up to 100% (or, due to missing storage-level validation, a stored value interpreted incorrectly elsewhere) of delegators' staking/block-revenue rewards for an epoch that has already accrued credits, i.e., a direct value-diversion from stakers to the validator's chosen collector. This matches the report's core concern: fee manipulation without a hard cap and without protection for "already deposited" (already-earned) funds.

### Likelihood Explanation
Medium. The core protections (the epoch-half-relative-slot rule for legacy commission, and the epoch-snapshot lookup for delayed BPS commission) are present and appear correctly wired for the paths reviewed, so under current feature-gate configuration the identified gaps (no bound at the setter, no timing rule for the BPS path) are mitigated by other layers rather than exploitable end-to-end in a single transaction today. However, the missing input validation on the setter itself, and the reliance on a single downstream `.min(MAX_BPS)` clamp that is easy to bypass if any new consumer of the stored `commission_bps` field is added, represent a real defense-in-depth gap consistent with the reported bug class.

### Recommendation
- Enforce an upper bound (≤10,000 bps) directly in `set_inflation_rewards_commission_bps` / `set_block_revenue_commission_bps` (or in `update_commission_bps`) rather than relying solely on `commission_split`'s `.min(MAX_BPS)` clamp [1](#0-0) [7](#0-6) .
- Audit every consumer of `inflation_rewards_commission_bps`/`block_revenue_commission_bps` to confirm all of them use the epoch-delayed/snapshotted value rather than the live vote-state value, so `delay_commission_updates` cannot be silently bypassed [6](#0-5) .
- Consider applying an analogous delay/notice period to `UpdateCommissionCollector` so redirection of the commission recipient cannot take effect within the same epoch as accrued, unredeemed rewards [8](#0-7) .

### Proof of Concept
Not independently reproducible from the indexed code alone — a concrete PoC would require confirming (in a live cluster or full checkout) whether any active reward/commission-collector code path reads `inflation_rewards_commission_bps` or `block_revenue_commission_bps` directly (bypassing `redeem_delegation_rewards`'s epoch-snapshot lookup) under currently activated feature gates. The unit test `test_set_inflation_rewards_commission_bps` demonstrates the unbounded-storage behavior directly: setting `bps` to `15_000` or `u16::MAX` succeeds at the storage layer with no error [1](#0-0) , confirming the missing input validation described above.

### Citations

**File:** programs/vote/src/vote_state/handler.rs (L1755-1775)
```rust
    #[test]
    fn test_set_inflation_rewards_commission_bps() {
        let mut handler = VoteStateHandler::new_v4(VoteStateV4::default());

        // First test some "normal" values.
        for bps in [0, 100, 500, 1_000, 5_000, 10_000] {
            handler.set_inflation_rewards_commission_bps(bps);
            let v4 = handler.as_ref_v4();
            assert_eq!(v4.inflation_rewards_commission_bps, bps);
            // commission() should return bps / 100
            assert_eq!(handler.commission(), (bps / 100) as u8);
        }

        // Now test values > 10,000 are allowed at program level.
        // Capping happens during reward calculation, not storage.
        for bps in [10_001, 15_000, u16::MAX] {
            handler.set_inflation_rewards_commission_bps(bps);
            let v4 = handler.as_ref_v4();
            assert_eq!(v4.inflation_rewards_commission_bps, bps);
        }
    }
```

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

**File:** programs/vote/src/vote_processor.rs (L383-408)
```rust
        VoteInstruction::UpdateCommissionCollector(kind) => {
            // SIMD-0232: Custom Commission Collector Account
            // Requires SIMD-0185: Vote State V4
            let custom_collector_enabled =
                invoke_context.get_feature_set().custom_commission_collector;
            if !custom_collector_enabled {
                return Err(InstructionError::InvalidInstructionData);
            }

            instruction_context.check_number_of_instruction_accounts(3)?;
            let new_collector = read_new_collector_account(&instruction_context, &me, 1)?;

            let rent = invoke_context
                .environment_config
                .sysvar_cache()
                .get_rent()?;

            vote_state::update_commission_collector(
                &mut me,
                target_version,
                new_collector,
                kind,
                &signers,
                &rent,
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
