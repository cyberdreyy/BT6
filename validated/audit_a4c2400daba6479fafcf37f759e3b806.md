### Title
`kamino_init_obligation`'s hardcoded `amount == 10` minimum does not guarantee non-zero collateral is deposited, breaking the "obligation always kept alive" invariant - ([File: programs/marginfi/src/instructions/kamino/init_obligation.rs])

### Summary
`kamino_init_obligation` enforces only `require_gte!(amount, 10, ...)` on the raw liquidity amount, asserting in a comment that this guarantees a non-zero Kamino obligation balance forever. The check operates purely on the raw liquidity unit count and never accounts for the reserve's actual liquidity→collateral exchange rate, so a reserve whose ratio has drifted such that `floor(10 * total_col / total_liq) == 0` will pass the instruction check yet deposit zero collateral into the obligation.

### Finding Description
`kamino_init_obligation` (programs/marginfi/src/instructions/kamino/init_obligation.rs:28-51) checks only: [1](#0-0) 
and then transfers `amount` raw liquidity units and deposits them via `cpi_kamino_deposit`, which invokes Kamino's `deposit_reserve_liquidity_and_obligation_collateral_v2`: [2](#0-1) 

The actual collateral minted for a given liquidity deposit is `floor(liquidity * total_col / total_liq)`, exactly as modeled by the repo's own Kamino model for this conversion: [3](#0-2) [4](#0-3) 

The repo's own unit tests confirm that small liquidity amounts can floor to zero collateral for reasonable exchange rates, e.g. `liquidity_to_collateral(1) == 0` when the ratio is `2:3`: [5](#0-4) 

Since `amount` is fixed at the instruction-level minimum of `10` raw units (not scaled by decimals or the reserve's exchange rate), any reserve where the liquidity:collateral ratio exceeds roughly `10:1` (i.e., `floor(10 * total_col/total_liq) == 0`) will pass the `require_gte!` check but mint zero collateral into `integration_acc_2`. Such a ratio is a normal, reachable state for an actively-used Kamino reserve as `total_liq` grows relative to `total_col` through interest accrual over time — it does not require any malicious or out-of-scope admin action to create the exchange-rate skew itself, since the exchange rate is a Kamino-protocol-level economic property, not something set arbitrarily by marginfi's admin.

The function contains no post-CPI verification that the obligation actually gained a non-zero collateral balance; it simply logs a success message and returns `Ok(())`.

### Impact Explanation
The code comment explicitly states the design intent: "Arbitrarily setting minimum deposit to 10 absolute units to always keep the obligation alive" and that the fee payer's nominal deposit will "prevent the obligation from ever being closed... even if the bank is otherwise empty." If the deposit rounds to zero collateral, Kamino's obligation-auto-close logic can still close the "permanent" `integration_acc_2` obligation later, invalidating the protocol's downstream assumption (used throughout the deposit/withdraw Kamino instructions) that this obligation always exists and is non-empty. This is a protocol-consistency/durable-state issue rather than direct fund loss, matching the "Medium" scope of the question.

### Likelihood Explanation
This requires a Kamino reserve whose exchange rate has drifted so that 10 raw liquidity units round to 0 collateral units — plausible for actively used, mature reserves with accrued interest, and independent of admin intent. The instruction itself (`kamino_init_obligation`) has no admin/authority gate beyond a `Signer` fee payer, so any unprivileged caller invoking it against such a reserve triggers the issue. The main uncertainty is that `cpi_kamino_deposit` calls the real, external Kamino lending program in production (only the `kamino-mocks` crate models the math locally for tests), so it cannot be fully confirmed from this repository alone whether the live Kamino program itself rejects a deposit that would mint zero collateral tokens; the local `kamino-mocks` model used for this repo's own tests does not perform such a rejection.

### Recommendation
Do not rely on a fixed raw-unit minimum. After computing/estimating the resulting collateral amount for the given `amount` against the reserve's actual exchange rate (or after the CPI completes), explicitly verify that the obligation's deposited collateral for this reserve is non-zero, and abort/require a larger `amount` if it would round to zero. Alternatively, derive the minimum `amount` dynamically from the reserve's current exchange rate (e.g., `amount >= ceil(total_liq / total_col)` ensures at least 1 unit of collateral) rather than using a fixed constant.

### Proof of Concept
Add a unit test in `programs/marginfi/src/instructions/kamino/local_tests.rs` using `MinimalReserve`/`generic_reserve` helpers already present in that file:
1. Construct a `MinimalReserve` with `total_liq`/`total_col` such that the ratio exceeds 10:1 (e.g. `generic_reserve(supply=110, mint_decimals=6, mint=10)` so that `total_col/total_liq = 10/110 ≈ 0.0909`).
2. Assert `reserve.liquidity_to_collateral(10).unwrap() == 0`.
3. This demonstrates that `kamino_init_obligation`'s `require_gte!(amount, 10, ...)` check passes while the resulting collateral deposited would be zero, contradicting the code's documented invariant, since no code path in `kamino_init_obligation` checks the post-deposit collateral balance.

### Citations

**File:** programs/marginfi/src/instructions/kamino/init_obligation.rs (L29-32)
```rust
    // Arbitrarily setting minimum deposit to 10 absolute units to always keep the obligation alive.
    // Obligations auto close when empty, but Kamino does not have any threshold that rounds down to
    // zero: even a single lamport suffices to keep a balance open.
    require_gte!(amount, 10, MarginfiError::ObligationInitDepositInsufficient);
```

**File:** programs/marginfi/src/instructions/kamino/init_obligation.rs (L282-320)
```rust
    pub fn cpi_kamino_deposit(&self, amount: u64) -> MarginfiResult {
        let deposit_accounts = DepositReserveLiquidityAndObligationCollateral {
            owner: self.liquidity_vault_authority.to_account_info(),
            obligation: self.integration_acc_2.to_account_info(),
            lending_market: self.lending_market.to_account_info(),
            lending_market_authority: self.lending_market_authority.to_account_info(),
            reserve: self.integration_acc_1.to_account_info(),
            reserve_liquidity_mint: self.mint.to_account_info(),
            reserve_liquidity_supply: self.reserve_liquidity_supply.to_account_info(),
            reserve_collateral_mint: self.reserve_collateral_mint.to_account_info(),
            reserve_destination_deposit_collateral: self
                .reserve_destination_deposit_collateral
                .to_account_info(),
            user_source_liquidity: self.liquidity_vault.to_account_info(),
            placeholder_user_destination_collateral: None,
            collateral_token_program: self.collateral_token_program.to_account_info(),
            liquidity_token_program: self.liquidity_token_program.to_account_info(),
            instruction_sysvar_account: self.instruction_sysvar_account.to_account_info(),
        };

        // --- optional “farms_accounts” group ---
        let farms_accounts = DepositFarmsAccounts {
            obligation_farm_user_state: optional_account!(self.obligation_farm_user_state),
            reserve_farm_state: optional_account!(self.reserve_farm_state),
        };

        // --- wrap both groups in the outer struct ---
        let accounts = DepositReserveLiquidityAndObligationCollateralV2 {
            deposit_accounts,
            deposit_farms_accounts: farms_accounts,
            farms_program: self.farms_program.to_account_info(),
        };
        let program = self.kamino_program.to_account_info();
        let bump = self.bank.load()?.liquidity_vault_authority_bump;
        let signer_seeds: &[&[&[u8]]] =
            bank_signer!(BankVaultType::Liquidity, self.bank.key(), bump);
        let cpi_ctx = CpiContext::new_with_signer(program.key(), accounts, signer_seeds);
        deposit_reserve_liquidity_and_obligation_collateral_v2(cpi_ctx, amount)?;
        Ok(())
```

**File:** programs/kamino-mocks/src/state.rs (L153-159)
```rust
    /// Convert liquidity tokens to equivalent value in collateral token.
    /// * Returns collateral equivalent (in `mint_decimals`)
    pub fn liquidity_to_collateral(&self, liquidity: u64) -> Result<u64> {
        let (total_liq, total_col) = self.scaled_supplies()?;
        liquidity_to_collateral_from_scaled(liquidity, total_liq, total_col)
            .ok_or(KaminoMocksError::MathError.into())
    }
```

**File:** type-crate/src/types/price.rs (L167-183)
```rust
/// Convert liquidity tokens to collateral tokens given scaled supplies.
/// Returns None on overflow or divide-by-zero.
#[inline]
pub fn liquidity_to_collateral_from_scaled(
    liquidity: u64,
    total_liq: I80F48,
    total_col: I80F48,
) -> Option<u64> {
    if total_liq == I80F48::ZERO {
        return None;
    }

    I80F48::from_num(liquidity)
        .checked_mul(total_col)?
        .checked_div(total_liq)?
        .checked_to_num::<u64>()
}
```

**File:** programs/marginfi/src/instructions/kamino/local_tests.rs (L378-391)
```rust
    #[test]
    fn collateral_to_liquidity_fractional_truncation() {
        // supply=3, mint_total_supply=2 -> ratio = 3/2 = 1.5
        let r = generic_reserve(300, 6, 200);

        // floor(1 * 3/2) = 1
        assert_eq!(r.collateral_to_liquidity(1).unwrap(), 1);
        // floor(1 * 2/3) = 0
        assert_eq!(r.liquidity_to_collateral(1).unwrap(), 0);

        // a slightly larger collateral: floor(5 * 3/2) = floor(7.5) = 7
        assert_eq!(r.collateral_to_liquidity(5).unwrap(), 7);
        // floor(7 * 2/3) = 4
        assert_eq!(r.liquidity_to_collateral(7).unwrap(), 4);
```
