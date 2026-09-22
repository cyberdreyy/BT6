Based on the research conducted, I could not identify a valid analog vulnerability matching the required criteria. The codebase consistently clamps or bounds the parameters explored:

- Vote commission split (`commission_split`/`commission_split_preserve_lamports` in `runtime/src/inflation_rewards/mod.rs`) clamps `commission_bps` via `.min(MAX_BPS)` before use, so out-of-range values cannot cause fund/reward corruption. [1](#0-0) 
- Vote commission-bps storage (`set_inflation_rewards_commission_bps`/`update_commission_bps` in `programs/vote/src/vote_state/mod.rs`) allows values >10,000 bps to be stored, but capping is explicitly deferred to, and enforced at, reward-calculation time — this is a self-affecting parameter set by the vote account's own authorized withdrawer, not an unprivileged third party, and does not lead to unsanctioned fund movement. [2](#0-1) 
- Compute budget instruction parameters (`sanitize_and_convert_to_compute_budget_limits` in `compute-budget-instruction/src/compute_budget_instruction_details.rs`) are all explicitly clamped to `MAX_COMPUTE_UNIT_LIMIT`, `MAX_HEAP_FRAME_BYTES`, and `MAX_LOADED_ACCOUNTS_DATA_SIZE_BYTES`, with `NonZeroU32` validation rejecting zero.
<invoke name="codebase_search">
<parameter name="query">stake history warmup cooldown rate unbounded activation percentage overflow</parameter>
</invoke>
<invoke name="codebase_search">
<parameter name="query">rent collection lamports subtraction underflow unbounded epoch parameter</parameter>
</invoke>

### Citations

**File:** runtime/src/inflation_rewards/mod.rs (L377-382)
```rust
fn commission_split(commission_bps: u16, on: u64) -> (u64, u64, bool) {
    const MAX_BPS: u16 = 10_000;
    const MAX_BPS_U128: u128 = MAX_BPS as u128;
    match commission_bps.min(MAX_BPS) {
        0 => (0, on, false),
        MAX_BPS => (on, 0, false),
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
