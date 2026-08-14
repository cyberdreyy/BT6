Confirmed: `lending_pool_add_bank_drift` explicitly supports Token-2022 mints (setting `IS_T22` flag when `bank_mint.to_account_info().owner == &anchor_spl::token_2022::ID`) with no check against `TransferFeeConfig` or transfer hooks, unlike `lending_pool_emissions_deposit`, which explicitly rejects such mints. The same pattern (Token-2022 support, no fee/hook rejection) applies to JupLend, Kamino, and Solend integration `add_pool` instructions, based on their shared use of `anchor_spl::token_interface` and lack of any `nonzero_fee`/`has_transfer_hook` checks in those modules.

### Title
Fee-on-transfer / transfer-hook Token-2022 mints break Drift/JupLend/Kamino/Solend integration deposit accounting, allowing DoS or misappropriation of the liquidity vault - (File: `programs/marginfi/src/instructions/drift/deposit.rs`)

### Summary
The reported THORChain bug is: a nominal amount is transferred into an intermediary, and a downstream call is then made using that same nominal amount instead of the amount actually received, which breaks for fee-on-transfer tokens. The same pattern exists in marginfi's third-party lending integrations (Drift, JupLend, Kamino, Solend): user funds are transferred into the bank's `liquidity_vault` intermediary account with a nominal `amount`, and the CPI into the downstream protocol is then invoked with that same nominal `amount`, without verifying that the vault's balance actually increased by `amount`.

### Finding Description
In `drift_deposit`, the flow is: [1](#0-0) 

```
ctx.accounts.cpi_transfer_user_to_liquidity_vault(amount)?;
ctx.accounts.cpi_drift_deposit(market_index, amount, authority_bump)?;
```

`cpi_transfer_user_to_liquidity_vault` uses `transfer_checked` with the raw `amount`: [2](#0-1) . There is no pre/post-balance check on `liquidity_vault` after this transfer (unlike the withdraw path, which does verify `actual_amount_received` via pre/post balances: [3](#0-2) ). Immediately after, `cpi_drift_deposit` is invoked with the same nominal `amount`, which instructs Drift to pull `amount` tokens out of `liquidity_vault` via `spl_token::transfer` inside Drift's own program: [4](#0-3) .

`lending_pool_add_bank_drift` explicitly allows the bank mint to be Token-2022 and even sets an `IS_T22` flag for it, with no rejection of `TransferFeeConfig` or `TransferHook` extensions: [5](#0-4) . This is in stark contrast to `lending_pool_emissions_deposit`, which explicitly checks and rejects both nonzero transfer fees and active transfer hooks before transferring into the liquidity vault: [6](#0-5) . The main `lending_account_deposit` path also correctly compensates for Token-2022 transfer fees by pre-computing `amount_pre_fee` so that the vault receives exactly `deposit_amount`: [7](#0-6) , [8](#0-7) . The Drift/JupLend/Kamino/Solend deposit instructions do not use this `calculate_pre_fee_spl_deposit_amount` helper at all, nor do they check `nonzero_fee`/`has_transfer_hook` on the mint before transferring, and JupLend's deposit path exhibits the identical structure (transfer nominal `amount` into vault, then CPI-deposit the same nominal `amount`): [9](#0-8) .

If a fee-on-transfer or transfer-hook-diverting Token-2022 mint is ever configured for one of these integration banks, the `liquidity_vault` will receive `amount - fee` tokens, but the downstream CPI (Drift/JupLend/Kamino/Solend `deposit`) will attempt to move the full nominal `amount` out of that same vault:
- If the vault has no surplus balance, the downstream CPI's internal transfer will fail (insufficient funds), reverting the deposit — a denial-of-service on that bank.
- If the vault happens to hold surplus tokens (e.g., dust left over from Drift's known reachable rounding dust described in `drift/withdraw.rs`, or tokens temporarily present due to another in-flight operation), the shortfall is silently paid out of that surplus, effectively socializing/misappropriating funds that belong to other depositors or to the protocol, exactly mirroring the THORChain_Aggregator griefing scenario.

### Impact Explanation
This is a durable accounting/availability issue directly caused by trusting a nominal transfer amount rather than the actually-received amount before triggering an external protocol interaction, matching the "Token-Transfer" bug class in the reference report. Depending on vault surplus state, the effect ranges from a deposit-path DoS on the affected bank to loss/misdirection of other users' or the vault's own tokens to cover the shortfall demanded by the downstream CPI.

### Likelihood Explanation
Exploitability is entirely gated by whether marginfi group admins ever configure a Drift/JupLend/Kamino/Solend integration bank with a Token-2022 mint that has an active `TransferFeeConfig` or `TransferHook` extension. The codebase's own `add_pool` instructions for these integrations impose no such restriction (unlike `lending_pool_emissions_deposit`, which explicitly guards against it), so nothing at the protocol level currently prevents an admin from onboarding such a mint. This makes it a real, reachable admin-driven configuration risk rather than a purely theoretical one, though it requires an admin action (choosing the mint) to materialize — this repo's `SECURITY.md` scope/known-issues could not be inspected in this session to confirm whether fee-on-transfer/T22-hook mints in integration banks are already a documented out-of-scope assumption.

### Recommendation
Apply the same defenses already used elsewhere in the codebase to the Drift/JupLend/Kamino/Solend deposit and `add_pool` instructions:
1. In each `lending_pool_add_bank_*` instruction, reject mints with nonzero transfer fees or active transfer hooks (reuse `utils::nonzero_fee` / `utils::has_transfer_hook`, as already done in `lending_pool_emissions_deposit`), or explicitly document and enforce that these integrations are Token-program-only.
2. In each `*_deposit`/`*_init_position` instruction, measure the liquidity vault's actual balance delta after `cpi_transfer_user_to_liquidity_vault` (as already done for the corresponding withdraw paths and for JupLend/Kamino/Solend fToken/collateral deltas) and pass that actual received amount into the downstream CPI deposit call instead of the nominal `amount`.

### Proof of Concept
1. Group admin creates a Drift-integration bank via `lending_pool_add_bank_drift` using a Token-2022 mint that has `TransferFeeConfig` active (nothing in `add_pool.rs` prevents this — see `programs/marginfi/src/instructions/drift/add_pool.rs` lines 86-89, contrasted with the explicit rejection in `lending_pool_emissions_deposit`).
2. A user calls `drift_deposit(amount)`. `cpi_transfer_user_to_liquidity_vault(amount)` moves `amount` from the user, but the mint's transfer fee causes `liquidity_vault` to only receive `amount - fee`.
3. `cpi_drift_deposit(market_index, amount, ...)` is called with the full nominal `amount`; Drift's CPI attempts to pull `amount` tokens out of `liquidity_vault`, which only holds `amount - fee`.
4. Absent surplus balance, the transaction reverts (DoS on deposits for that bank). If surplus tokens exist in the vault (e.g., Drift's documented rounding dust, or another user's in-flight funds), the shortfall is drawn from that surplus, misappropriating it — directly analogous to the referenced THORChain `THORChain_Aggregator` fund-griefing report.

### Citations

**File:** programs/marginfi/src/instructions/drift/deposit.rs (L60-85)
```rust
    ctx.accounts.cpi_update_spot_market_cumulative_interest()?;
    let expected_scaled_balance_change = ctx
        .accounts
        .integration_acc_1
        .load()?
        .get_scaled_balance_increment(amount)?;

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

**File:** programs/marginfi/src/instructions/drift/deposit.rs (L262-274)
```rust
    pub fn cpi_transfer_user_to_liquidity_vault(&self, amount: u64) -> MarginfiResult {
        let program = self.token_program.to_account_info();
        let accounts = TransferChecked {
            from: self.signer_token_account.to_account_info(),
            to: self.liquidity_vault.to_account_info(),
            authority: self.authority.to_account_info(),
            mint: self.mint.to_account_info(),
        };
        let cpi_ctx = CpiContext::new(program.key(), accounts);
        let decimals = self.mint.decimals;
        transfer_checked(cpi_ctx, amount, decimals)?;
        Ok(())
    }
```

**File:** programs/marginfi/src/instructions/drift/deposit.rs (L276-319)
```rust
    pub fn cpi_drift_deposit(
        &self,
        market_index: u16,
        amount: u64,
        authority_bump: u8,
    ) -> MarginfiResult {
        let accounts = Deposit {
            state: self.drift_state.to_account_info(),
            user: self.integration_acc_2.to_account_info(),
            user_stats: self.integration_acc_3.to_account_info(),
            authority: self.liquidity_vault_authority.to_account_info(),
            spot_market_vault: self.drift_spot_market_vault.to_account_info(),
            user_token_account: self.liquidity_vault.to_account_info(),
            token_program: self.token_program.to_account_info(),
        };

        let program = self.drift_program.to_account_info();
        let signer_seeds: &[&[&[u8]]] =
            bank_signer!(BankVaultType::Liquidity, self.bank.key(), authority_bump);
        let mut cpi_ctx = CpiContext::new_with_signer(program.key(), accounts, signer_seeds);

        // Construct remaining accounts in the required order for Drift:
        // 1. Oracle accounts (if provided)
        // 2. Spot market account (always required)
        // 3. Token mint (required for Token-2022, harmless to include for regular mints)
        let mut remaining_accounts = Vec::new();

        // Add oracle if provided (not needed if using oracle type QuoteAsset)
        if let Some(oracle) = &self.drift_oracle {
            remaining_accounts.push(oracle.to_account_info());
        }

        // Always add spot market account
        remaining_accounts.push(self.integration_acc_1.to_account_info());

        // Always add token mint (needed for Token-2022 support)
        remaining_accounts.push(self.mint.to_account_info());

        cpi_ctx = cpi_ctx.with_remaining_accounts(remaining_accounts);

        // Call drift deposit with reduce_only = false
        deposit(cpi_ctx, market_index, amount, false)?;
        Ok(())
    }
```

**File:** programs/marginfi/src/instructions/drift/withdraw.rs (L218-238)
```rust
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

        require_eq!(
            actual_amount_received,
            token_amount,
            MarginfiError::DriftWithdrawFailed
        );
```

**File:** programs/marginfi/src/instructions/drift/add_pool.rs (L86-89)
```rust
    bank.flags |= BANK_SEED_KNOWN;
    if bank_mint.to_account_info().owner == &anchor_spl::token_2022::ID {
        bank.flags |= IS_T22;
    }
```

**File:** programs/marginfi/src/instructions/marginfi_group/configure_bank.rs (L100-109)
```rust
    // Reject mints with non-zero transfer fees or active transfer hooks.
    let mint_ai = ctx.accounts.mint.to_account_info();
    check!(
        !utils::nonzero_fee(mint_ai.clone(), clock.epoch)?,
        MarginfiError::InvalidTransfer
    );
    check!(
        !utils::has_transfer_hook(mint_ai)?,
        MarginfiError::InvalidTransfer
    );
```

**File:** programs/marginfi/src/instructions/marginfi_account/deposit.rs (L104-124)
```rust
    let amount_pre_fee = maybe_bank_mint
        .as_ref()
        .map(|mint| {
            utils::calculate_pre_fee_spl_deposit_amount(
                mint.to_account_info(),
                deposit_amount,
                clock.epoch,
            )
        })
        .transpose()?
        .unwrap_or(deposit_amount);

    bank.deposit_spl_transfer(
        amount_pre_fee,
        signer_token_account.to_account_info(),
        bank_liquidity_vault.to_account_info(),
        signer.to_account_info(),
        maybe_bank_mint.as_ref(),
        token_program.to_account_info(),
        ctx.remaining_accounts,
    )?;
```

**File:** programs/marginfi/src/utils/general.rs (L87-112)
```rust
pub fn calculate_post_fee_spl_deposit_amount(
    mint_ai: AccountInfo,
    input_amount: u64,
    epoch: u64,
) -> MarginfiResult<u64> {
    if mint_ai.owner.eq(&Token::id()) {
        return Ok(input_amount);
    }

    let mint_data = mint_ai.try_borrow_data()?;
    let mint = StateWithExtensions::<spl_token_2022::state::Mint>::unpack(&mint_data)?;

    let fee = if let Ok(transfer_fee_config) = mint.get_extension::<TransferFeeConfig>() {
        transfer_fee_config
            .calculate_epoch_fee(epoch, input_amount)
            .unwrap()
    } else {
        0
    };

    let output_amount = input_amount
        .checked_sub(fee)
        .ok_or(MarginfiError::MathError)?;

    Ok(output_amount)
}
```

**File:** programs/marginfi/src/instructions/juplend/deposit.rs (L66-82)
```rust
    let pre_f_token_balance = accessor::amount(&ctx.accounts.integration_acc_2.to_account_info())?;

    // Move underlying into the vault and deposit into JupLend.
    ctx.accounts.cpi_transfer_user_to_liquidity_vault(amount)?;
    ctx.accounts.cpi_juplend_deposit(amount, authority_bump)?;

    let post_f_token_balance = accessor::amount(&ctx.accounts.integration_acc_2.to_account_info())?;
    let minted_shares = post_f_token_balance
        .checked_sub(pre_f_token_balance)
        .ok_or_else(|| error!(MarginfiError::MathError))?;

    // Exact match required.
    require_eq!(
        minted_shares,
        expected_shares,
        MarginfiError::JuplendDepositFailed
    );
```
