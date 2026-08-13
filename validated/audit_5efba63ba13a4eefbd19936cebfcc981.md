This confirms the design pattern: `destination_token_account` is unchecked because the authorization is enforced upstream — the transaction requires `authority` to be a valid signer authorized on the `marginfi_account` whose shares are being burned, via the `is_signer_authorized` constraint. The user who owns/controls the position chooses their own destination; this is identical to the standard `lending_account_withdraw` pattern where the account owner directs funds wherever they choose. The amount transferred is strictly the `received_underlying` delta measured before/after the JupLend CPI withdraw, not the intermediary ATA's total balance, so no stale/leftover balance from another user's transaction can be swept out in a subsequent withdraw. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

There is no "replay" primitive available here: each `juplend_withdraw` call independently burns fTokens tied to the caller's own `marginfi_account` shares (via `BankAccountWrapper::find`/`withdraw`/`withdraw_all` on the caller's own position), and the intermediary ATA (`integration_acc_3`) is a single shared PDA-owned account whose balance delta (not total) is what gets forwarded. An attacker cannot "replay a previously valid intermediary closeout context" to redirect a withdrawal to themselves, because:

1. They cannot cause fTokens to burn from someone else's position — `bank_account.withdraw()`/`withdraw_all()` operate strictly on the signer's own `marginfi_account.lending_account`, gated by `is_signer_authorized`.
2. Even if they supplied their own `destination_token_account` as the recipient, they would only receive the underlying corresponding to the shares burned from *their own* account, not another user's.
3. The transfer amount is the exact `received_underlying` delta from the just-completed CPI, so no residual/stale balance from a prior transaction in the shared intermediary ATA can leak into an unrelated withdrawal. [5](#0-4) 

#No vulnerability found for this question.

### Citations

**File:** programs/marginfi/src/instructions/juplend/withdraw.rs (L111-152)
```rust
        let in_receivership = marginfi_account.get_flag(ACCOUNT_IN_RECEIVERSHIP);
        let mut bank_account = BankAccountWrapper::find(
            &ctx.accounts.bank.key(),
            &mut bank,
            &mut marginfi_account.lending_account,
        )?;

        let (token_amount, shares_to_burn, share_amount) = if withdraw_all {
            // `withdraw_all` returns the user's full position amount and marginfi share delta.
            let (f_tokens_balance, share_amount) = bank_account.withdraw_all(in_receivership)?;
            // Redeemable underlying = floor(shares * price / 1e12)
            // Then recalculate shares_to_burn from token_amount to guarantee we match
            // JupLend's expected burn amount (should be identical, but this is safer).
            let (token_amount, shares_to_burn) = {
                let token_amount = expected_assets_for_redeem_from_rate(
                    f_tokens_balance,
                    lending.token_exchange_price,
                )
                .ok_or_else(|| error!(MarginfiError::MathError))?;
                let shares_to_burn = expected_shares_for_withdraw_from_rate(
                    token_amount,
                    lending.token_exchange_price,
                )
                .ok_or_else(|| error!(MarginfiError::MathError))?;
                (token_amount, shares_to_burn)
            };

            // Sanity check: recalculated shares should never exceed what we have
            require!(shares_to_burn <= f_tokens_balance, MarginfiError::MathError);

            (token_amount, shares_to_burn, share_amount)
        } else {
            // shares = ceil(assets * 1e12 / token_exchange_price)
            let shares_to_burn = {
                expected_shares_for_withdraw_from_rate(amount, lending.token_exchange_price)
                    .ok_or_else(|| error!(MarginfiError::MathError))?
            };

            let share_amount = bank_account.withdraw(I80F48::from_num(shares_to_burn))?;

            (amount, shares_to_burn, share_amount)
        };
```

**File:** programs/marginfi/src/instructions/juplend/withdraw.rs (L218-253)
```rust
    let received_underlying = if withdraw_all && token_amount == 0 {
        0
    } else {
        // CPI withdraw: burns fTokens and credits underlying into withdraw intermediary ATA.
        ctx.accounts
            .cpi_juplend_withdraw(token_amount, authority_bump)?;

        let post_withdraw_intermediary_ata_balance =
            accessor::amount(&ctx.accounts.integration_acc_3.to_account_info())?;
        let post_f_token_balance =
            accessor::amount(&ctx.accounts.integration_acc_2.to_account_info())?;

        let received_underlying = post_withdraw_intermediary_ata_balance
            .checked_sub(pre_withdraw_intermediary_ata_balance)
            .ok_or_else(|| error!(MarginfiError::MathError))?;
        require_eq!(
            received_underlying,
            token_amount,
            MarginfiError::JuplendWithdrawFailed
        );

        let burned_shares = pre_f_token_balance
            .checked_sub(post_f_token_balance)
            .ok_or_else(|| error!(MarginfiError::MathError))?;
        require_eq!(
            burned_shares,
            shares_to_burn,
            MarginfiError::JuplendWithdrawFailed
        );

        // Transfer underlying from withdraw intermediary ATA -> destination.
        ctx.accounts
            .cpi_transfer_withdraw_intermediary_ata_to_destination(
                received_underlying,
                authority_bump,
            )?;
```

**File:** programs/marginfi/src/instructions/juplend/withdraw.rs (L339-358)
```rust
    #[account(
        mut,
        has_one = group @ MarginfiError::InvalidGroup,
        constraint = {
            let acc = marginfi_account.load()?;
            !acc.get_flag(ACCOUNT_DISABLED)
        } @MarginfiError::AccountDisabled,
        constraint = {
            let a = marginfi_account.load()?;
            account_not_frozen_for_authority(&a, authority.key())
        } @ MarginfiError::AccountFrozen,
        constraint = {
            let a = marginfi_account.load()?;
            let g = group.load()?;
            is_signer_authorized(&a, g.admin, authority.key(), true, true)
        } @ MarginfiError::Unauthorized
    )]
    pub marginfi_account: AccountLoader<'info, MarginfiAccount>,

    pub authority: Signer<'info>,
```

**File:** programs/marginfi/src/instructions/juplend/withdraw.rs (L379-382)
```rust
    /// Token account that will receive the underlying withdrawal.
    /// WARN: Completely unchecked!
    #[account(mut)]
    pub destination_token_account: Box<InterfaceAccount<'info, TokenAccount>>,
```

**File:** programs/marginfi/src/instructions/juplend/withdraw.rs (L529-547)
```rust
    pub fn cpi_transfer_withdraw_intermediary_ata_to_destination(
        &self,
        amount: u64,
        authority_bump: u8,
    ) -> MarginfiResult {
        let program = self.token_program.to_account_info();
        let accounts = TransferChecked {
            from: self.integration_acc_3.to_account_info(),
            to: self.destination_token_account.to_account_info(),
            authority: self.liquidity_vault_authority.to_account_info(),
            mint: self.mint.to_account_info(),
        };

        let signer_seeds: &[&[&[u8]]] =
            bank_signer!(BankVaultType::Liquidity, self.bank.key(), authority_bump);
        let cpi_ctx = CpiContext::new_with_signer(program.key(), accounts, signer_seeds);
        transfer_checked(cpi_ctx, amount, self.mint.decimals)?;
        Ok(())
    }
```
