### Title
Origination fee rounds down to zero on fragmented small borrows, letting borrowers accumulate debt fee-free - ([File: programs/marginfi/src/instructions/marginfi_account/borrow.rs])

### Summary
`lending_account_borrow()` computes the protocol origination fee as `amount_pre_fee * origination_fee_rate` in `I80F48` fixed-point, then truncates it to a `u64` via `checked_to_num()` with no floor/minimum check. A borrower can split a borrow into many small chunks, each sized so the computed fee truncates to `0`, and repeat the borrow to accumulate the same total liability while paying no origination fee at all — the same rounding-to-zero fee-avoidance pattern described in the external report, just applied to marginfi's borrow-fee accounting instead of a token-sale fee.

### Finding Description
In `lending_account_borrow()`:
```rust
if !origination_fee_rate.is_zero() {
    origination_fee = I80F48::from_num(amount_pre_fee)
        .checked_mul(origination_fee_rate)
        .ok_or_else(math_error!())?;
    origination_fee_u64 = origination_fee.checked_to_num().ok_or_else(math_error!())?;

    // Incurs a borrow that includes the origination fee (but withdraws just the amt)
    share_amount =
        bank_account.borrow(I80F48::from_num(amount_pre_fee) + origination_fee)?;
} else {
    origination_fee_u64 = 0;
    share_amount = bank_account.borrow(I80F48::from_num(amount_pre_fee))?;
}
``` [1](#0-0) 

`origination_fee` (an `I80F48` with 48 bits fractional precision) is later truncated to native `u64` units via `checked_to_num()`. There is no check that `origination_fee_u64 > 0`, nor any minimum-borrow-amount enforcement anywhere in this function. If `amount_pre_fee * origination_fee_rate < 1` native unit (e.g. a low `protocol_origination_fee` like 1 bps and a small enough `amount`), `origination_fee_u64` truncates to `0`, and the borrower is charged **no origination fee at all** while still successfully borrowing `amount_pre_fee`. Note that even the borrowed liability itself (`share_amount = bank_account.borrow(...)`) uses the full `I80F48::from_num(amount_pre_fee) + origination_fee` where `origination_fee` (pre-truncation) may be a nonzero fraction that gets added to internal liability shares, but the actual fee revenue recorded to `collected_group_fees_outstanding` / `collected_program_fees_outstanding` afterward is built from the truncated `origination_fee_u64`-scale `origination_fee` I80F48 value — meaning the group/program only credit fee income proportional to what didn't round away. By repeating many small borrows sized just under the rounding threshold, an attacker accumulates the same aggregate debt as one large borrow while the group/program lose all fee revenue on that debt.

### Impact Explanation
Impact is low per-transaction (an attacker only avoids a proportionally tiny fee each time), but it durably erodes protocol fee revenue that the group/program admin is otherwise entitled to, exactly mirroring the "Low impact / High likelihood" classification in the source report. This is a permissionless, unprivileged-user path (any borrower with margin account and collateral) with no admin/oracle/CPI dependency.

### Likelihood Explanation
Likelihood is high: any user can call `lending_account_borrow()` repeatedly with `amount` values chosen so `amount_pre_fee * origination_fee_rate < 1` native unit. Solana transaction costs are low enough that scripting many small borrows to reconstruct a large position is practical, especially for low-decimal-fee-rate combinations (small `protocol_origination_fee` values on 6-decimal tokens).

### Recommendation
Enforce a floor on the origination fee analogous to the recommended DAO fix: reject (or round up) the borrow if the computed fee truncates to zero while the configured rate is nonzero, e.g.:
```rust
if !origination_fee_rate.is_zero() {
    origination_fee = I80F48::from_num(amount_pre_fee).checked_mul(origination_fee_rate)...;
    origination_fee_u64 = origination_fee.checked_to_num().ok_or_else(math_error!())?;
    check!(origination_fee_u64 > 0 || origination_fee.is_zero(), MarginfiError::AmountInvalid, "borrow amount too small for fee rate");
}
```
Alternatively, always round the fee up (`.ceil()`, as already done in the repay-side origination fee test path) instead of truncating down, so fragmenting into small borrows cannot zero out the fee. [2](#0-1) 

### Proof of Concept
1. Group admin (or any DAO-like bank config) sets `bank.config.interest_rate_config.protocol_origination_fee` to a small nonzero rate, e.g. `0.0001` (1 bps).
2. Attacker opens a margin account with sufficient collateral to borrow.
3. Attacker calls `lending_account_borrow()` with `amount` such that `amount_pre_fee * 0.0001 < 1` native unit (e.g. `amount_pre_fee = 5000` on a 6-decimal mint → fee `= 0.5` → truncates to `0`).
4. Repeat step 3 `N` times to accumulate the desired total debt (e.g. 1,000 borrows of 5000 units = 5,000,000 units total).
5. Compare `collected_group_fees_outstanding` / `collected_program_fees_outstanding` after the loop versus a single equivalent borrow of `5,000,000` units — the fragmented path yields `0` fee collected, while the single borrow would have yielded `500` units of fee, demonstrating fee-revenue loss with no negative consequence to the attacker. [3](#0-2)

### Citations

**File:** programs/marginfi/src/instructions/marginfi_account/borrow.rs (L85-131)
```rust
    let mut origination_fee: I80F48 = I80F48::ZERO;
    let amount_pre_fee;
    {
        let mut bank = bank_loader.load_mut()?;

        validate_asset_tags(&bank, &marginfi_account)?;
        validate_bank_state(&bank, InstructionKind::FailsIfPausedOrReduceState)?;

        let liquidity_vault_authority_bump = bank.liquidity_vault_authority_bump;
        let origination_fee_rate: I80F48 = bank
            .config
            .interest_rate_config
            .protocol_origination_fee
            .into();

        let lending_account = &mut marginfi_account.lending_account;
        let mut bank_account =
            BankAccountWrapper::find_or_create(&bank_loader.key(), &mut bank, lending_account)?;

        // User needs to borrow amount + fee to receive amount
        amount_pre_fee = maybe_bank_mint
            .as_ref()
            .map(|mint| {
                utils::calculate_pre_fee_spl_deposit_amount(
                    mint.to_account_info(),
                    amount,
                    clock.epoch,
                )
            })
            .transpose()?
            .unwrap_or(amount);

        let (origination_fee_u64, share_amount): (u64, I80F48);
        if !origination_fee_rate.is_zero() {
            origination_fee = I80F48::from_num(amount_pre_fee)
                .checked_mul(origination_fee_rate)
                .ok_or_else(math_error!())?;
            origination_fee_u64 = origination_fee.checked_to_num().ok_or_else(math_error!())?;

            // Incurs a borrow that includes the origination fee (but withdraws just the amt)
            share_amount =
                bank_account.borrow(I80F48::from_num(amount_pre_fee) + origination_fee)?;
        } else {
            // Incurs a borrow for the amount without any fee
            origination_fee_u64 = 0;
            share_amount = bank_account.borrow(I80F48::from_num(amount_pre_fee))?;
        }
```

**File:** programs/marginfi/tests/user_actions/repay.rs (L296-301)
```rust
    let origination_fee: I80F48 =
        I80F48::from_num(native!(borrow_amount, debt_bank.mint.mint.decimals, f64))
            .checked_mul(origination_fee_rate)
            .unwrap()
            .ceil(); // Round up when repaying
    let origination_fee_u64: u64 = origination_fee.checked_to_num().expect("out of bounds");
```
