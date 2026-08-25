### Title
Missing upper-bound validation on vote commission basis points allows commission > 100% - ([File: programs/vote/src/vote_state/handler.rs])

### Summary
The vote program's `UpdateCommissionBps` instruction lets the authorized withdrawer set `inflation_rewards_commission_bps` / `block_revenue_commission_bps` to any `u16` value (0–65535) with no upper bound check against `10_000` (100%). This is the same class of bug as the referenced Sherlock finding (`setOptionFeePerTxnLimitPercent()` accepting unbounded percentage values): a percentage-style setter is missing a sanity/max-value check.

### Finding Description
`VoteStateHandler::set_inflation_rewards_commission_bps` and `set_block_revenue_commission_bps` write the raw `commission_bps` value directly into vote state with no range check: [1](#0-0) 

The caller, `update_commission_bps`, verifies only the authorized-withdrawer signature and (for `BlockRevenue`) the `block_revenue_sharing` feature gate — there is no validation that `commission_bps <= 10_000`: [2](#0-1) 

This is reachable from an ordinary transaction: `VoteInstruction::UpdateCommissionBps { commission_bps, kind }` is processed in the vote program's instruction dispatcher without any bounds sanitization before calling into `vote_state::update_commission_bps`: [3](#0-2) 

Compare with the legacy `UpdateCommission(u8)` path, which is naturally bounded to `[0,255]` (converted to bps via `commission * 100`, capped at 25500 bps = 255%) — still not clamped to 10,000, but at least bounded by the u8 domain and existing production usage: [4](#0-3) 

By contrast, `UpdateCommissionBps` accepts a raw `u16` up to 65,535 (655%) with zero bound enforcement anywhere in the call chain (`vote_processor.rs` → `vote_state::update_commission_bps` → `VoteStateHandler::set_*_commission_bps`). The value is later consumed as `voter_commission_bps: u16` in the inflation-reward redemption path: [5](#0-4) 

### Impact Explanation
`commission_bps` is intended to represent a percentage in basis points, where 10,000 = 100%. Because there is no clamp to `10_000`, an authorized withdrawer can set a commission above 100% (e.g., 65,535 bps ≈ 655%). Downstream reward-splitting logic that computes `voter_rewards = total_rewards * commission_bps / 10_000` (and `staker_rewards = total_rewards - voter_rewards`) would either produce a voter reward larger than the total reward pool (over-allocating funds away from delegators/stakers) or trigger an arithmetic underflow/panic if the staker share is computed via unchecked subtraction. Either outcome is a state-integrity/fund-accounting bug directly reachable by any vote-account withdraw authority, without requiring any privileged access — matching the "unwanted results" impact described in the referenced report (a setter without a max-value check allowing extraction of more value than intended).

### Likelihood Explanation
High reachability: any vote account's authorized withdrawer (a normal, unprivileged key) can submit `VoteInstruction::UpdateCommissionBps` in an ordinary transaction once the `commission_rate_in_basis_points` and `delay_commission_updates` features are active. No other precondition or race is required. The only barrier is that this instruction path is currently gated behind not-yet-fully-activated features (`commission_rate_in_basis_points`, `delay_commission_updates`, and `block_revenue_sharing` for the BlockRevenue kind), so likelihood depends on feature activation status on the target cluster.

### Recommendation
Add explicit validation in `vote_state::update_commission_bps` (or in `VoteStateHandler::set_inflation_rewards_commission_bps` / `set_block_revenue_commission_bps`) to reject `commission_bps > 10_000`, returning `InstructionError::InvalidInstructionData` (mirroring how `MAX_COMPUTE_UNIT_LIMIT`/`MAX_HEAP_FRAME_BYTES` are enforced elsewhere in the codebase, e.g. `compute-budget-instruction/src/compute_budget_instruction_details.rs`).

### Proof of Concept
1. On a cluster with `commission_rate_in_basis_points` and `delay_commission_updates` active, create/own a vote account and its authorized-withdrawer keypair.
2. Submit a transaction containing `VoteInstruction::UpdateCommissionBps { commission_bps: 20000, kind: CommissionKind::InflationRewards }` signed by the authorized withdrawer (see dispatch at [3](#0-2) ).
3. The instruction succeeds and `inflation_rewards_commission_bps` is stored as `20000` (200%) with no error, as shown by the unvalidated setter at [6](#0-5) .
4. At the next reward-distribution epoch, the stored `commission_bps` value (200%) is passed as `voter_commission_bps` into `redeem_rewards`/`redeem_stake_rewards` ( [5](#0-4) ), producing an out-of-range commission split.

**Note:** I could not fully trace the exact arithmetic in `calculate_stake_points_and_credits`/the reward-split function (not indexed in the available snippets) to confirm whether an out-of-range `commission_bps` causes a panic, saturating clamp, or fund over-allocation. A Devin session with full repository access would be needed to inspect `runtime/src/inflation_rewards/points.rs` and the exact split-lamports computation to confirm the precise numerical consequence.

### Citations

**File:** programs/vote/src/vote_state/handler.rs (L142-151)
```rust
    #[allow(clippy::arithmetic_side_effects)]
    #[cfg_attr(feature = "dev-context-only-utils", qualifiers(pub))]
    pub(crate) fn set_commission(&mut self, commission: u8) {
        match &mut self.target_state {
            TargetVoteState::V4(v4) => {
                // Safety: u16::MAX > u8::MAX * 100
                v4.inflation_rewards_commission_bps = (commission as u16) * 100;
            }
        }
    }
```

**File:** programs/vote/src/vote_state/handler.rs (L153-164)
```rust
    #[cfg_attr(feature = "dev-context-only-utils", qualifiers(pub))]
    pub(crate) fn set_inflation_rewards_commission_bps(&mut self, commission_bps: u16) {
        match &mut self.target_state {
            TargetVoteState::V4(v4) => v4.inflation_rewards_commission_bps = commission_bps,
        }
    }

    pub(crate) fn set_block_revenue_commission_bps(&mut self, commission_bps: u16) {
        match &mut self.target_state {
            TargetVoteState::V4(v4) => v4.block_revenue_commission_bps = commission_bps,
        }
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

**File:** runtime/src/inflation_rewards/mod.rs (L35-44)
```rust
pub(crate) fn redeem_rewards<'a>(
    mut stake: Stake,
    voter_commission_bps: u16,
    vote_state: DelegatedVoteState,
    calculation_environment: CalculationEnvironment<'a>,
    inflation_point_calc_tracer: Option<impl Fn(&InflationPointCalculationEvent)>,
    ag_epoch_type: &AlpenglowEpochType,
    current_lamports: u64,
    minimum_lamports: u64,
) -> Result<(u64, u64, Stake), InstructionError> {
```
