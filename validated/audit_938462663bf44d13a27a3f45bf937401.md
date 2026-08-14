## Finding: Panic-on-Overflow in `calc_value()`'s Weight Multiplication (unwrap() instead of checked math)

### Title
Unchecked `.unwrap()` on weighted-amount multiplication in `calc_value()` can permanently DoS health-check-gated user operations - (File: `programs/marginfi/src/state/marginfi_account.rs`)

### Summary
The reported bug class is a checked-math overflow that reverts a legitimate operation (`getVestedAmount`) because an unbounded input multiplied against protocol constants isn't defensively bounded, causing a durable per-user DoS. Marginfi-v2 mirrors this exact math shape in `calc_value()`, which multiplies a balance's token `amount` by a `weight` before applying `price`. Unlike virtually every other checked multiplication in the codebase (which uses `.checked_mul(..).ok_or_else(math_error!())?`), this specific multiplication uses a bare `.unwrap()`.

### Finding Description
`calc_value()` computes a balance's USD value for health/risk purposes: [1](#0-0) 

Note line 463: `amount.checked_mul(weight).unwrap()` — every other multiply/divide in the same function (and essentially everywhere else in the interest-rate, price, and integration adapter code, e.g. [2](#0-1) , [3](#0-2) ) consistently propagates overflow as `MarginfiError` via `math_error!()`. This one line panics instead.

`calc_value()` is invoked from the core risk-engine value calculators used on every deposit/withdraw/borrow/repay/liquidate/health-pulse instruction, including the weighted-asset path: [4](#0-3) 

and is also reused by every integration's withdraw computation (Drift, Kamino, Solend, JupLend) per the `calc_value(` call sites found in `instructions/drift/withdraw.rs`, `instructions/kamino/withdraw.rs`, `instructions/solend/withdraw.rs`, `instructions/juplend/withdraw.rs`, and `instructions/marginfi_account/withdraw.rs`.

`amount` is derived from `bank.get_asset_amount(balance.asset_shares.into())` — i.e., `shares * asset_share_value`, which grows with deposits and accrued interest/emissions over time, and `weight` is the bank's configured `asset_weight_init`/`asset_weight_maint` (an admin-set but not tightly-bounded I80F48). Since `I80F48` has finite range (80 integer bits), a sufficiently large `amount` (e.g., a bank with many decimals, high `deposit_limit`, and/or shares inflated via long-running interest/emissions accrual) multiplied by `weight` can exceed I80F48::MAX.

### Impact Explanation
Because `calc_value()` is exercised on the read/write path of health checks, an overflow here does not just fail one instruction — it can affect any instruction that requires a health check for a marginfi account holding that balance, or any liquidator trying to evaluate/liquidate that position. If the value only overflows for large-enough shares/amounts (rather than a corrupted single value), this becomes a durable freeze: the affected balance can no longer be deposited into, withdrawn from, borrowed against, repaid, or liquidated, since every such instruction re-runs the risk engine which panics before completing. Unlike a normal `MarginfiError`-based revert (which just fails the current transaction, same as any input-validation failure), a raw Rust `.unwrap()` panic is a stronger failure mode and is inconsistent with the surrounding, deliberately-hardened checked-math style used everywhere else in this file and its sibling integration modules — indicating this line was likely missed during the overflow-hardening pass evidenced by the patch notes ("Fix `i128 → i64` overflow panic in `StakedWithPythPush` pricing (#559)").

### Likelihood Explanation
Reaching actual overflow requires `amount` (native units, no exponent applied at this stage) times `weight` to exceed I80F48's ~2^79 range — this is far from trivial for typical stablecoin/SOL banks with realistic `deposit_limit`s, so likelihood under default configuration is low. However, it becomes more plausible for banks with many decimals, permissionless bank creation, high `deposit_limit`/`borrow_limit`, or via long-lived share inflation from interest/emissions accrual, and is reachable without any special privilege — any depositor/borrower/liquidator triggering a health check on the affected balance hits this code path.

### Recommendation
Replace the bare `.unwrap()` on line 463 with the same checked-math pattern used elsewhere in the function:
```rust
let weighted_asset_amount = if let Some(weight) = weight {
    amount.checked_mul(weight).ok_or_else(math_error!())?
} else {
    amount
};
```
This converts a hard panic into the standard `MarginfiError` already used throughout the risk engine, and should be paired with the report's broader recommendation: bound `amount`/`weight` inputs (e.g., validate `asset_weight` configuration ranges, and/or use wider intermediate precision) so overflow is avoided or degrades gracefully rather than causing a hard failure on health-check-gated operations.

### Proof of Concept
Not independently reproduced against a live/bankrun test in this pass; likelihood analysis is based on static review of `calc_value()` and its I80F48 range limits versus realistic bank configurations (`deposit_limit`, `mint_decimals`, and share-value growth from `accrue_interest`/emissions). A concrete PoC would require constructing (or finding) a bank configuration where `get_asset_amount(shares) * asset_weight` exceeds I80F48::MAX under achievable deposit/accrual conditions and confirming the resulting panic (vs. a controlled `MarginfiError`) during a subsequent health-check-triggering instruction.

### Citations

**File:** programs/marginfi/src/state/marginfi_account.rs (L385-393)
```rust
            let value = calc_value(
                bank.get_asset_amount(balance.asset_shares.into())?,
                lower_price,
                bank.get_balance_decimals(),
                Some(asset_weight),
            )?;

            Ok((value, lower_price))
        }
```

**File:** programs/marginfi/src/state/marginfi_account.rs (L450-481)
```rust
pub fn calc_value(
    amount: I80F48,
    price: I80F48,
    mint_decimals: u8,
    weight: Option<I80F48>,
) -> MarginfiResult<I80F48> {
    if amount == I80F48::ZERO {
        return Ok(I80F48::ZERO);
    }

    let scaling_factor = EXP_10_I80F48[mint_decimals as usize];

    let weighted_asset_amount = if let Some(weight) = weight {
        amount.checked_mul(weight).unwrap()
    } else {
        amount
    };

    #[cfg(target_os = "solana")]
    debug!(
        "weighted_asset_qt: {}, price: {}, expo: {}",
        weighted_asset_amount, price, mint_decimals
    );

    let value = weighted_asset_amount
        .checked_mul(price)
        .ok_or_else(math_error!())?
        .checked_div(scaling_factor)
        .ok_or_else(math_error!())?;

    Ok(value)
}
```

**File:** type-crate/src/types/price.rs (L76-90)
```rust
/// Multiply two `I80F48` values, returning `None` on overflow.
#[inline]
pub fn mul_i80f48(value: I80F48, multiplier: I80F48) -> Option<I80F48> {
    value.checked_mul(multiplier)
}

/// Multiply and divide `I80F48` values, returning `None` on overflow or divide-by-zero.
#[inline]
pub fn mul_div_i80f48(value: I80F48, numerator: I80F48, denominator: I80F48) -> Option<I80F48> {
    if denominator == I80F48::ZERO {
        return None;
    }

    value.checked_mul(numerator)?.checked_div(denominator)
}
```

**File:** programs/marginfi/src/state/interest_rate.rs (L385-396)
```rust
fn calc_interest_payment_for_period(apr: I80F48, time_delta: u64, value: I80F48) -> Option<I80F48> {
    if apr.is_zero() {
        return Some(I80F48::ZERO);
    }

    let interest_payment: I80F48 = value
        .checked_mul(apr)?
        .checked_mul(time_delta.into())?
        .checked_div(SECONDS_PER_YEAR)?;

    Some(interest_payment)
}
```
