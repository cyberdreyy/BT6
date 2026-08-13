## Title
Unchecked subtraction on externally-reported Drift scaled balance can panic and brick deposits/withdrawals for a Drift-integrated bank - (File: `programs/marginfi/src/instructions/drift/deposit.rs`, `programs/marginfi/src/instructions/drift/withdraw.rs`)

### Summary
`drift_deposit` and `drift_withdraw` snapshot the Drift-reported scaled balance of the bank's pooled Drift sub-account (`integration_acc_2`) before and after a single Drift CPI, then subtract the two snapshots as plain `u64` values, assuming the balance moves strictly in the expected direction (up on deposit, down on withdraw). This is the same trust assumption that caused the referenced jpegd `yVaultLPFarming` bug: an external, protocol-controlled value (`currentBalance`) is assumed to always be `>=`/`<=` a previously recorded value, and the delta is computed with unchecked subtraction instead of a checked/saturating operation with an explicit guard.

### Finding Description
In `drift_deposit`:
```
programs/marginfi/src/instructions/drift/deposit.rs:67-85
let initial_scaled_balance = { integration_acc_2.load()?.get_scaled_balance(market_index) };
...
let final_scaled_balance = { integration_acc_2.load()?.get_scaled_balance(market_index) };
let scaled_balance_change = final_scaled_balance - initial_scaled_balance;
``` [1](#0-0) 

In `drift_withdraw`:
```
programs/marginfi/src/instructions/drift/withdraw.rs:214-232
let initial_scaled_balance = { integration_acc_2.load()?.get_scaled_balance(market_index) };
...
ctx.accounts.cpi_drift_withdraw(market_index, token_amount, authority_bump)?;
let final_scaled_balance = { integration_acc_2.load()?.get_scaled_balance(market_index) };
...
let actual_scaled_balance_change = initial_scaled_balance - final_scaled_balance;
``` [2](#0-1) 

Both snapshots are read directly from Drift's own account (`MinimalUser`/`MinimalSpotMarket`), i.e. the value is fundamentally controlled by the external Drift program's interest/funding bookkeeping (`cumulative_deposit_interest`), not by marginfi. marginfi's local `get_scaled_balance_increment`/`get_scaled_balance_decrement` math (in `drift-mocks/src/state.rs`) is only an approximation of Drift's real on-chain calculation used to predict `expected_scaled_balance_change`, which is then checked via `require_eq!` — but the *raw subtraction* that produces `scaled_balance_change`/`actual_scaled_balance_change` happens **before** that check, exactly mirroring jpegd's `currentBalance - previousBalance` pattern that trusted the vault/strategy always to report a value in the expected direction. [3](#0-2) 

If Drift's real cumulative-interest bookkeeping causes the post-CPI scaled balance to move in the unexpected direction relative to marginfi's locally predicted amount (e.g. deposit CPI yields a smaller scaled balance than the pre-CPI snapshot, or withdraw CPI yields a larger post-CPI scaled balance than pre-CPI), the subtraction underflows a `u64` before the `require_eq!` guard can run, causing a raw arithmetic-overflow panic instead of a graceful custom error.

### Impact Explanation
An underflow panic aborts the transaction with an unhandled arithmetic error rather than a clean `MarginfiError`. If the underlying condition is systemic (e.g. a lasting mismatch between marginfi's local Drift scaled-balance math and Drift's actual production interest accrual for a given spot market, or any future Drift-side change to interest/funding mechanics for that market), every subsequent `drift_deposit`/`drift_withdraw` call against that bank would panic in the same way, permanently freezing user deposits and withdrawals for that Drift-integrated bank — the same "deposits and crucially, withdrawals fail" impact identified in the original H-05 finding for `yVaultLPFarming`.

### Likelihood Explanation
Within a single atomic Solana transaction there is no way for a third party to interleave and directly manipulate Drift's internal state between the two snapshots, so the trigger is less “trivially reproducible” than the original off-chain jpegd scenario (which involved separate strategy/farm migration transactions). The residual risk here is a discrepancy between marginfi's local prediction of Drift's scaled-balance delta and Drift's live, production interest/funding accounting for the specific spot market — the same "trust the external protocol's math" class of bug, just triggered by mismatch rather than by explicit migration. Given `programs/marginfi/src/instructions/drift` is user/permissionless-callable production code, and the underlying weakness is architecturally identical to H-05 (delta of an externally-influenced balance computed with unchecked subtraction, only guarded after the fact), likelihood is assessed as low-to-moderate but the bug class itself is directly present in reachable code.

### Recommendation
Replace the raw subtractions with `checked_sub`/`saturating_sub`, and short-circuit with a dedicated `MarginfiError` (e.g. `DriftScaledBalanceMismatch`) if the post-CPI balance did not move in the expected direction, instead of letting the operation panic:
```rust
let scaled_balance_change = final_scaled_balance
    .checked_sub(initial_scaled_balance)
    .ok_or_else(|| error!(MarginfiError::DriftScaledBalanceMismatch))?;
```
Apply the same pattern to `actual_amount_received` (`post_transfer_vault_balance - pre_transfer_vault_balance`) and `actual_scaled_balance_change` in `drift_withdraw`.

### Proof of Concept
Not independently reproduced on-chain (would require inducing a live discrepancy between marginfi's local `get_scaled_balance_increment`/`get_scaled_balance_decrement` math and Drift's production interest accrual for a market during a single deposit/withdraw CPI). The structural defect — computing `final - initial` (or `initial - final`) via plain `u64` subtraction on a value fully controlled by an external program, before any bounds check — is demonstrated directly by the cited source lines in `deposit.rs:80` and `withdraw.rs:231-232`, which is the exact bug pattern described in the H-05 report (`currentBalance - previousBalance` without a `<=`/`>=` guard).

### Citations

**File:** programs/marginfi/src/instructions/drift/deposit.rs (L67-85)
```rust
    let initial_scaled_balance = {
        let integration_acc_2 = ctx.accounts.integration_acc_2.load()?;
        integration_acc_2.get_scaled_balance(market_index)
    };

    ctx.accounts.cpi_transfer_user_to_liquidity_vault(amount)?;
    ctx.accounts
        .cpi_drift_deposit(market_index, amount, authority_bump)?;

    let final_scaled_balance = {
        let integration_acc_2 = ctx.accounts.integration_acc_2.load()?;
        integration_acc_2.get_scaled_balance(market_index)
    };
    let scaled_balance_change = final_scaled_balance - initial_scaled_balance;
    require_eq!(
        scaled_balance_change,
        expected_scaled_balance_change,
        MarginfiError::DriftScaledBalanceMismatch
    );
```

**File:** programs/marginfi/src/instructions/drift/withdraw.rs (L214-232)
```rust
        let initial_scaled_balance = {
            let integration_acc_2 = ctx.accounts.integration_acc_2.load()?;
            integration_acc_2.get_scaled_balance(market_index)
        };
        let pre_transfer_vault_balance =
            accessor::amount(&ctx.accounts.liquidity_vault.to_account_info())?;

        ctx.accounts
            .cpi_drift_withdraw(market_index, token_amount, authority_bump)?;

        let final_scaled_balance = {
            let integration_acc_2 = ctx.accounts.integration_acc_2.load()?;
            integration_acc_2.get_scaled_balance(market_index)
        };
        let post_transfer_vault_balance =
            accessor::amount(&ctx.accounts.liquidity_vault.to_account_info())?;

        let actual_amount_received = post_transfer_vault_balance - pre_transfer_vault_balance;
        let actual_scaled_balance_change = initial_scaled_balance - final_scaled_balance;
```

**File:** programs/drift-mocks/src/state.rs (L172-233)
```rust
impl MinimalSpotMarket {
    /// Core scaled balance calculation used by both increment and decrement.
    /// See `get_spot_balance` function on Drift program.
    /// https://github.com/drift-labs/protocol-v2/blob/master/programs/drift/src/math/spot_balance.rs#L16
    ///
    /// # Parameters
    /// * `amount` - Token amount in native mint precision
    /// * `round_up` - If true, rounds up by 1 (used for withdrawals/decrements to prevent dust)
    fn get_scaled_balance(&self, amount: u64, round_up: bool) -> Result<u64> {
        let precision_increase = get_precision_increase(self.decimals)?;
        let cumulative_interest = u128::from_le_bytes(self.cumulative_deposit_interest);

        let mut balance: u64 = (amount as u128)
            .checked_mul(precision_increase)
            .ok_or_else(math_error!())?
            .checked_div(cumulative_interest)
            .ok_or_else(math_error!())?
            .try_into()?;

        // Drift rounds up withdrawals to prevent dust accumulation
        if round_up && balance != 0 {
            balance = balance
                .checked_add(1)
                .ok_or(error!(DriftMocksError::MathError))?;
        }

        Ok(balance)
    }

    /// Calculate scaled balance decrement for withdrawals (rounds up).
    pub fn get_scaled_balance_decrement(&self, amount: u64) -> Result<u64> {
        self.get_scaled_balance(amount, true)
    }

    /// Calculate scaled balance increment for deposits (floors).
    pub fn get_scaled_balance_increment(&self, amount: u64) -> Result<u64> {
        self.get_scaled_balance(amount, false)
    }

    /// Convert scaled balance back to token amount for withdrawals
    ///
    /// # Parameters
    /// * `scaled_balance` - Balance in Drift's internal scaled units (SPOT_BALANCE_PRECISION = 10^9)
    ///
    /// # Returns
    /// * Token amount in native mint precision (mint_decimals)
    pub fn get_withdraw_token_amount(&self, scaled_balance: u64) -> Result<u64> {
        // See `get_token_amount` function on drift
        let precision_increase = get_precision_increase(self.decimals)?;

        let cumulative_interest = u128::from_le_bytes(self.cumulative_deposit_interest);

        let floored_token_amount: u64 = (scaled_balance as u128)
            .checked_mul(cumulative_interest)
            .ok_or_else(math_error!())?
            .checked_div(precision_increase)
            .ok_or_else(math_error!())?
            .try_into()
            .map_err(|_| error!(DriftMocksError::MathError))?;

        Ok(floored_token_amount)
    }
```
