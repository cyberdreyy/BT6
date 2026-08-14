### Title
`initialize_fee_state` performs no bounds validation on fee-rate parameters, allowing extreme WrappedI80F48 values to be permanently committed to `FeeState` - (File: `programs/marginfi/src/instructions/marginfi_group/init_global_fee_state.rs`)

### Finding Description
`initialize_fee_state` copies `program_fee_fixed`, `program_fee_rate`, `liquidation_max_fee`, and `order_execution_max_fee` directly from caller-supplied `WrappedI80F48` arguments into the newly initialized `FeeState` account with no range/sanity checks whatsoever: [1](#0-0) 

The `InitFeeState` accounts struct requires only a `Signer` payer and uses `init` with a fixed PDA seed (`FEE_STATE_SEED`), with no authority/admin gating on who may call it — any signer can invoke this instruction as long as the account has not yet been created: [2](#0-1) 

By contrast, `edit_fee_state` (which does apply the same fields to an *existing* `FeeState`) is restricted to the `global_fee_admin`/pause_delegate_admin via account constraints, but it likewise performs no numeric bounds validation on the incoming `WrappedI80F48` values before storing them — it only logs the old/new float value and copies the field: [3](#0-2) 

Given the precondition (attacker wins the permissionless init race), the attacker fully controls the bit pattern of `program_fee_rate`, `liquidation_max_fee`, and `order_execution_max_fee` stored on-chain, with no instruction-level clamp to `[0, 1]` or any sane percentage range.

Downstream, `program_fee_rate` is consumed in `lending_pool_borrow`'s fee-accrual path via `checked_mul`, which uses `ok_or_else(math_error!())` to fail on overflow rather than silently corrupting state: [4](#0-3) 

This means a genuinely overflow-inducing bit pattern would cause `checked_mul` to return `None`, and the instruction would abort with a math error rather than silently computing an incorrect fee. However, a value that is merely "out of sane percentage range" but not overflow-inducing (e.g., `program_fee_rate = 5.0` meaning 500%, or a negative fee rate) is *not* rejected by `checked_mul`, and would be silently accepted, producing a program fee amount larger than the entire origination fee, or a negative fee that inverts direction of value flow between program and group fee buckets.

I could not fully verify within the available tool budget how `liquidation_max_fee` and `order_execution_max_fee` are consumed in `liquidate_end.rs` and `order.rs` (I found the fields present in those files via grep but did not read the full bodies), so I cannot conclusively state whether those specific downstream consumers apply their own bounds clamp before use, or whether an extreme value there would corrupt liquidation/order-execution profit calculations. Given the index limitations encountered, a Devin session with full read access to those files would be needed to confirm the exact downstream arithmetic and whether it saturates or is otherwise guarded.

### Impact Explanation
Because `initialize_fee_state` is reachable by any unprivileged signer in a race against the legitimate deployer (a precondition explicitly established by the linked prior question), and it applies no bounds validation, an attacker who wins that race can commit protocol-wide fee parameters (e.g., `program_fee_rate` > 100%, or negative-encoded) that then feed directly into bank-level fee-accrual math in `lending_pool_borrow`. Since these accounting fields (`collected_program_fees_outstanding`, `collected_group_fees_outstanding`) are shared across every bank and every borrow in the protocol, a bad `program_fee_rate` corrupts protocol-wide fee accounting, potentially misallocating or overstating fees taken from every group's collected fees.

### Likelihood Explanation
This finding is directly contingent on the precondition established in the referenced "race-to-init" question — i.e., it only matters if an attacker can actually win the init race in the first place. Assuming that precondition holds, exploiting *this specific* gap (no bounds validation) requires no further privilege: the attacker simply supplies out-of-range `WrappedI80F48` arguments in the same transaction that wins the race. The instruction has no numeric guard rails to prevent it.

### Recommendation
Add explicit bounds checks in `initialize_fee_state` (and equivalently in `edit_fee_state`) before storing `program_fee_rate`, `liquidation_max_fee`, and `order_execution_max_fee`: reject (return a `MarginfiError`) if any of these values, once converted to `I80F48`, fall outside a sane range (e.g., `0 <= rate <= 1` for `program_fee_rate`, and appropriate caps for `liquidation_max_fee`/`order_execution_max_fee`). This should be paired with resolving the underlying race-to-init issue (e.g., pre-deriving/locking the `FeeState` PDA at deploy time or restricting `InitFeeState` to a known upgrade authority) since bounds-checking alone does not prevent an attacker from becoming the permanent `global_fee_admin`.

### Proof of Concept
Rust integration test plan (extends the existing `programs/marginfi/tests` bankrun harness):
1. Build an `InitFeeState` instruction with `program_fee_rate` encoded as `WrappedI80F48::from(I80F48::from_num(5.0))` (500%) and sign/send it as an arbitrary non-privileged keypair before the legitimate setup calls `init_global_fee_state`.
2. Assert the transaction succeeds (demonstrating no bounds check exists) and that `FeeState::program_fee_rate` deserializes to 5.0.
3. Set up a bank and call `lending_pool_borrow` to trigger the origination-fee-splitting logic in `programs/marginfi/src/instructions/marginfi_account/borrow.rs`; assert that `collected_program_fees_outstanding` becomes larger than the entire `origination_fee`, or (if `origination_fee * 5.0` overflows the I80F48 range for large amounts) assert the transaction aborts with a `MathError`, confirming the lack of a graceful bounds-based rejection at the `initialize_fee_state` layer.
4. Repeat with a negative-encoded `program_fee_rate` and assert `collected_program_fees_outstanding` decreases or underflows in an unexpected direction, confirming the invariant violation.

### Citations

**File:** programs/marginfi/src/instructions/marginfi_group/init_global_fee_state.rs (L20-34)
```rust
) -> Result<()> {
    let mut fee_state = ctx.accounts.fee_state.load_init()?;
    fee_state.global_fee_admin = admin_key;
    fee_state.global_fee_wallet = fee_wallet;
    fee_state.key = ctx.accounts.fee_state.key();
    fee_state.bank_init_flat_sol_fee = bank_init_flat_sol_fee;
    fee_state.bump_seed = ctx.bumps.fee_state;
    fee_state.program_fee_fixed = program_fee_fixed;
    fee_state.program_fee_rate = program_fee_rate;
    fee_state.liquidation_max_fee = liquidation_max_fee;
    fee_state.liquidation_flat_sol_fee = liquidation_flat_sol_fee;
    fee_state.order_execution_max_fee = order_execution_max_fee;
    fee_state.order_init_flat_sol_fee = order_init_flat_sol_fee;

    Ok(())
```

**File:** programs/marginfi/src/instructions/marginfi_group/init_global_fee_state.rs (L37-55)
```rust
#[derive(Accounts)]
pub struct InitFeeState<'info> {
    /// Pays the init fee
    #[account(mut)]
    pub payer: Signer<'info>,

    #[account(
        init,
        seeds = [
            FEE_STATE_SEED.as_bytes()
        ],
        bump,
        payer = payer,
        space = 8 + FeeState::LEN,
    )]
    pub fee_state: AccountLoader<'info, FeeState>,

    pub system_program: Program<'info, System>,
}
```

**File:** programs/marginfi/src/instructions/marginfi_group/edit_global_fee.rs (L54-87)
```rust
    if let Some(program_fee_rate) = program_fee_rate {
        let old_f64: f64 = wrapped_i80f48_to_f64(fee_state.program_fee_rate);
        let new_f64: f64 = wrapped_i80f48_to_f64(program_fee_rate);
        msg!("Updating program_fee_rate: {:?} -> {:?}", old_f64, new_f64);
        fee_state.program_fee_rate = program_fee_rate;
    }
    if let Some(liquidation_max_fee) = liquidation_max_fee {
        let old_f64: f64 = wrapped_i80f48_to_f64(fee_state.liquidation_max_fee);
        let new_f64: f64 = wrapped_i80f48_to_f64(liquidation_max_fee);
        msg!(
            "Updating liquidation_max_fee: {:?} -> {:?}",
            old_f64,
            new_f64
        );
        fee_state.liquidation_max_fee = liquidation_max_fee;
    }
    if let Some(liquidation_flat_sol_fee) = liquidation_flat_sol_fee {
        msg!(
            "Updating liquidation_flat_sol_fee: {:?} -> {:?}",
            fee_state.liquidation_flat_sol_fee,
            liquidation_flat_sol_fee
        );
        fee_state.liquidation_flat_sol_fee = liquidation_flat_sol_fee;
    }
    if let Some(order_execution_max_fee) = order_execution_max_fee {
        let old_f64: f64 = wrapped_i80f48_to_f64(fee_state.order_execution_max_fee);
        let new_f64: f64 = wrapped_i80f48_to_f64(order_execution_max_fee);
        msg!(
            "Updating order_execution_max_fee: {:?} -> {:?}",
            old_f64,
            new_f64
        );
        fee_state.order_execution_max_fee = order_execution_max_fee;
    }
```

**File:** programs/marginfi/src/instructions/marginfi_account/borrow.rs (L180-193)
```rust
            if !program_fee_rate.is_zero() {
                // Some portion of the origination fee to goes to program fees
                let program_fee_amount: I80F48 = origination_fee
                    .checked_mul(program_fee_rate)
                    .ok_or_else(math_error!())?;
                // The remainder of the origination fee goes to group fees
                bank_fees_after = bank_fees_after
                    .saturating_add(origination_fee.saturating_sub(program_fee_amount));

                // Update the bank's program fees
                let program_fees_before: I80F48 = bank.collected_program_fees_outstanding.into();
                bank.collected_program_fees_outstanding = program_fees_before
                    .saturating_add(program_fee_amount)
                    .into();
```
