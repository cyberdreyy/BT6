Confirmed: no zero-amount guard exists before the SPL transfer, and no other code path mitigates it.

### Title
Unchecked zero-value insurance-fee SPL transfer during liquidation reverts entire liquidation for tokens that reject zero-amount transfers - (File: `programs/marginfi/src/instructions/marginfi_account/liquidate.rs`)

### Summary
`lending_account_liquidate` unconditionally performs an SPL token transfer of the computed insurance fee (`insurance_fee_to_transfer`) from the liability bank's liquidity vault to its insurance vault, without checking that the amount is non-zero. `checked_to_num::<u64>()` truncates the fractional insurance fee, so for small liquidations or low-decimal liability tokens this value legitimately rounds down to `0`. If the liability token enforces "no zero-value transfer" semantics (a known weird-token behavior, as raised for a comparable code pattern in the referenced Sherlock report), the `transfer`/`transfer_checked` CPI reverts, and the entire liquidation instruction fails.

### Finding Description
In `lending_account_liquidate`, the insurance fee owed on a liquidation is computed as: [1](#0-0) 
```
let (insurance_fee_to_transfer, insurance_fee_dust) = (
    insurance_fund_fee
        .checked_to_num::<u64>()
        .ok_or(MarginfiError::MathError)?,
    insurance_fund_fee.frac(),
);
```
`insurance_fund_fee` is a fixed-point (`I80F48`) fraction of the liability amount corresponding to `LIQUIDATION_INSURANCE_FEE`. Converting to `u64` via `checked_to_num` truncates any fractional token unit, meaning `insurance_fee_to_transfer` can legitimately be `0` for small `asset_amount` liquidations, low-decimal tokens, or liability tokens priced such that the discount difference is sub-unit.

This value is then passed directly and unconditionally into `withdraw_spl_transfer`, which performs the actual CPI transfer: [2](#0-1) 
```
// ## SPL transfer ##
// Insurance fund receives fee
liab_bank.withdraw_spl_transfer(
    insurance_fee_to_transfer,
    ctx.accounts.bank_liquidity_vault.to_account_info(),
    ctx.accounts.bank_insurance_vault.to_account_info(),
    ...
)?;
```
`withdraw_spl_transfer` itself performs no zero-amount short-circuit before invoking `transfer`/`transfer_checked`: [3](#0-2) 

For SPL tokens/Token-2022 extensions that revert on zero-amount transfers (a documented weird-token behavior — the same class flagged in the referenced report for Teller Finance), this CPI call fails, causing the entire `lending_account_liquidate` transaction to revert. Because liquidation is a permissionless, time-sensitive operation triggered whenever a marginfi account's health drops below zero, this failure is fully attacker/market-triggerable and not limited to a privileged actor.

### Impact Explanation
Liquidation is the core mechanism protecting the protocol from insolvency when a borrower's collateral value falls below their liability. If any bank configured with such a "zero-transfer-reverts" liability token mint is systematically un-liquidatable whenever the computed insurance fee rounds to zero (which is common for small/partial liquidations that liquidators intentionally perform to minimize price risk, per the classic liquidation guide's own recommendation to liquidate ~70–80% of the negative health in increments), liquidators are blocked from repaying that account's debt via `lending_account_liquidate`. This durably prevents unhealthy positions from being remediated through the standard liquidation path, directly risking bad debt / protocol insolvency for that bank — a financial-effect freeze condition, not merely a griefing nuisance, since it can be triggered by legitimate liquidation attempts, not just adversarial ones.

### Likelihood Explanation
The trigger condition (fee truncating to zero) is common in normal operation, not a rare edge case: it occurs whenever `asset_amount * LIQUIDATION_INSURANCE_FEE` (converted to liability-token native units) is less than 1. This is likely for any low-decimal liability token, or for the smaller/partial liquidations that are the recommended liquidator strategy. The only additional precondition is that the liability bank's mint enforces revert-on-zero-transfer — a real, documented ERC20/SPL edge-case behavior the report explicitly calls out as in-scope for "weird token" review. Given marginfi supports arbitrary bank mints (including Token-2022 with extensions), this is a realistic configuration.

### Recommendation
Skip the insurance-fee SPL transfer when `insurance_fee_to_transfer == 0`, mirroring guards already used elsewhere in the codebase (e.g., `super_admin_withdraw`'s early return for `amount == 0`): [4](#0-3) 
```
if amount == 0 {
    return Ok(());
}
```
Apply an equivalent `if insurance_fee_to_transfer > 0 { ... }` guard around the `withdraw_spl_transfer` call in `lending_account_liquidate`, and audit other unconditional `withdraw_spl_transfer`/`deposit_spl_transfer` call sites (e.g. `collect_bank_fees.rs`) for the same class of issue.

### Proof of Concept
1. Configure a bank whose mint is a Token-2022 (or wrapped) token that reverts when `transfer_checked`/`transfer` is called with `amount == 0`.
2. Open a liquidatee position with a small liability balance in that bank and drive it unhealthy.
3. As a liquidator, call `lending_account_liquidate` with an `asset_amount` small enough that `insurance_fund_fee = liab_amount_liquidator - liab_amount_final` truncates to `0` when passed through `checked_to_num::<u64>()` at `programs/marginfi/src/instructions/marginfi_account/liquidate.rs:353-358`.
4. The subsequent `liab_bank.withdraw_spl_transfer(0, ...)` call at lines 386-401 triggers the token program's zero-transfer revert, causing the whole liquidation instruction to fail, even though `pre_liquidation_health` indicated the account was genuinely unhealthy and liquidatable.

### Citations

**File:** programs/marginfi/src/instructions/marginfi_account/liquidate.rs (L353-358)
```rust
        let (insurance_fee_to_transfer, insurance_fee_dust) = (
            insurance_fund_fee
                .checked_to_num::<u64>()
                .ok_or(MarginfiError::MathError)?,
            insurance_fund_fee.frac(),
        );
```

**File:** programs/marginfi/src/instructions/marginfi_account/liquidate.rs (L384-401)
```rust
            // ## SPL transfer ##
            // Insurance fund receives fee
            liab_bank.withdraw_spl_transfer(
                insurance_fee_to_transfer,
                ctx.accounts.bank_liquidity_vault.to_account_info(),
                ctx.accounts.bank_insurance_vault.to_account_info(),
                ctx.accounts
                    .bank_liquidity_vault_authority
                    .to_account_info(),
                maybe_liab_bank_mint.as_ref(),
                ctx.accounts.token_program.to_account_info(),
                bank_signer!(
                    BankVaultType::Liquidity,
                    ctx.accounts.liab_bank.key(),
                    liab_bank_liquidity_authority_bump
                ),
                ctx.remaining_accounts,
            )?;
```

**File:** programs/marginfi/src/state/bank.rs (L769-850)
```rust
    fn withdraw_spl_transfer<'info>(
        &self,
        amount: u64,
        from: AccountInfo<'info>,
        to: AccountInfo<'info>,
        authority: AccountInfo<'info>,
        maybe_mint: Option<&InterfaceAccount<'info, Mint>>,
        program: AccountInfo<'info>,
        signer_seeds: &[&[&[u8]]],
        remaining_accounts: &[AccountInfo<'info>],
    ) -> MarginfiResult {
        debug!(
            "withdraw_spl_transfer: amount: {} from {} to {}, auth {}",
            amount, from.key, to.key, authority.key
        );

        #[cfg(feature = "client")]
        if let Some(mint) = maybe_mint {
            invoke_client_token_transfer(
                program.key,
                amount,
                from,
                Some(mint.to_account_info()),
                to,
                authority,
                Some(mint.decimals),
                remaining_accounts,
                signer_seeds,
            )?;
        } else {
            // `transfer_checked` and `transfer` does the same thing, the additional `_checked` logic
            // is only to assert the expected attributes by the user (mint, decimal scaling),
            //
            // Security of `transfer` is equal to `transfer_checked`.
            invoke_client_token_transfer(
                program.key,
                amount,
                from,
                None,
                to,
                authority,
                None,
                remaining_accounts,
                signer_seeds,
            )?;
        }

        #[cfg(not(feature = "client"))]
        if let Some(mint) = maybe_mint {
            spl_token_2022::onchain::invoke_transfer_checked(
                program.key,
                from,
                mint.to_account_info(),
                to,
                authority,
                remaining_accounts,
                amount,
                mint.decimals,
                signer_seeds,
            )?;
        } else {
            // `transfer_checked` and `transfer` does the same thing, the additional `_checked` logic
            // is only to assert the expected attributes by the user (mint, decimal scaling),
            //
            // Security of `transfer` is equal to `transfer_checked`.
            #[allow(deprecated)]
            transfer(
                CpiContext::new_with_signer(
                    program.key(),
                    Transfer {
                        from,
                        to,
                        authority,
                    },
                    signer_seeds,
                ),
                amount,
            )?;
        }

        Ok(())
    }
```

**File:** programs/marginfi/src/instructions/marginfi_group/super_admin_withdraw.rs (L43-45)
```rust
    if amount == 0 {
        return Ok(());
    }
```
