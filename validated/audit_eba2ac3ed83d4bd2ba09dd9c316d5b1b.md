Confirmed: `LendingPoolCollectBankFees` has no signer/admin requirement — any address can invoke `lending_pool_collect_bank_fees` permissionlessly, and it unconditionally executes up to three separate SPL transfers (group fee, insurance fee, program fee) regardless of whether each individual amount is zero. [1](#0-0) 

### Title
Permissionless `lending_pool_collect_bank_fees` performs unconditional zero-value SPL transfers, permanently bricking fee collection for tokens that revert on zero transfers - (File: programs/marginfi/src/instructions/marginfi_group/collect_bank_fees.rs)

### Summary
`lending_pool_collect_bank_fees` is callable by anyone (no signer or admin constraint on the accounts struct) and computes three independent transfer amounts — group fees, insurance fees, and program fees — each as `min(outstanding, available_liquidity)`. It then unconditionally calls `bank.withdraw_spl_transfer` for all three amounts, even when an individual amount is `0`, exactly mirroring the OpenQ `TieredPercentageBountyV1` pattern where `_transferERC20` is invoked without checking `_volume != 0`.

### Finding Description
The three fee-transfer legs are computed and dispatched independently: [2](#0-1) [3](#0-2) 

None of these three calls to `bank.withdraw_spl_transfer` are guarded by a zero-amount check. `withdraw_spl_transfer` itself performs a raw CPI `transfer`/`transfer_checked` with no zero-check either: [4](#0-3) 

Because `collected_group_fees_outstanding`, `collected_insurance_fees_outstanding`, and `collected_program_fees_outstanding` accrue independently (from interest accrual, origination fees, etc.), it is common for exactly one or two of the three to be non-zero at any given collection time while the others sit at `0`. Any caller invoking this permissionless instruction will therefore frequently trigger a `transfer`/`transfer_checked` CPI with `amount == 0`. For a standard SPL Token mint this is harmless, but for any Token-2022 mint whose configuration (e.g., certain transfer-hook programs, or custom token implementations reachable via the Token Interface used here, `InterfaceAccount<TokenAccount>`/`Interface<TokenInterface>`) reverts on a zero-value transfer, the entire instruction — including the two other legitimate fee legs — will always fail. Since Anchor treats any failing inner CPI as failing the whole transaction, this permanently blocks fee collection for that bank on every future call, because the outstanding amount that never gets collected simply persists (or grows) while the zero-leg keeps causing the whole instruction to abort.

Unlike `lending_pool_emissions_deposit`, which explicitly checks `nonzero_fee`/`has_transfer_hook` before proceeding, no such check protects the vault transfer paths in `collect_bank_fees.rs`, `super_admin_withdraw.rs`, or the standard user-facing `lending_account_withdraw` flow, all of which route through the same unguarded `withdraw_spl_transfer`. [5](#0-4) 

### Impact Explanation
If a bank is created (or configured) with a Token-2022 mint that reverts on a zero-amount transfer, fee collection for that bank becomes permanently non-functional: any of insurance fees, group fees, or program fees that legitimately accrue can never be swept out, because the always-included zero-value leg(s) will cause the whole permissionless instruction to revert forever. This causes a durable freeze of protocol/insurance/group fee funds with financial effect (fees permanently uncollectable), matching the "durable freeze/inconsistency with financial effect" bar. It does not directly brick user deposits/withdrawals (which is a mitigating factor relative to the original OpenQ report where entire payouts were bricked), so the blast radius is narrower — limited to the fee-collection accounting for the affected bank — but it is a genuine, permanent, unprivileged-triggerable freeze of funds legitimately owed to the insurance fund / protocol / group.

### Likelihood Explanation
Likelihood depends entirely on whether marginfi's bank-creation/admin flow can ever be configured with a Token-2022 mint extension (or a future Token-Interface implementation) that reverts on a zero-value transfer. No global guard exists to reject such mints at `add_bank`/`configure_bank` time; the only such check found (`nonzero_fee`, `has_transfer_hook`) is scoped to the `lending_pool_emissions_deposit` instruction only, not bank admission itself. Given marginfi's design intent (evidenced in the sibling `lending_pool_emissions_deposit` guard) to support "any" Token-2022 mint including ones with transfer hooks, this is a plausible configuration, but exploitation requires either a listed bank using such a mint or a future/permissionless bank-listing path that does not filter for this property.

### Recommendation
Add a zero-amount guard before invoking `withdraw_spl_transfer` for each of the three fee legs in `lending_pool_collect_bank_fees` (and ideally centralize the guard inside `withdraw_spl_transfer`/`deposit_spl_transfer` themselves), skipping the transfer when the computed amount is `0`. This prevents an always-zero leg from perpetually reverting the whole instruction and blocking collection of the other, non-zero fee legs.

### Proof of Concept
1. A bank is created for a Token-2022 mint whose transfer-hook program (or otherwise) reverts when the transferred amount is `0`.
2. Normal usage accrues `collected_insurance_fees_outstanding > 0` while `collected_group_fees_outstanding == 0` and `collected_program_fees_outstanding == 0` (a common, non-adversarial state since these accrue at different rates from different fee types). [6](#0-5) 
3. Anyone (permissionless caller, no signer constraint required) calls `lending_pool_collect_bank_fees`. [7](#0-6) 
4. The `group_fee_transfer_amount` (=0) transfer CPI executes first and reverts because the mint disallows zero-value transfers, causing the entire instruction — including the legitimate insurance-fee sweep — to fail. [8](#0-7) 
5. Because `collected_group_fees_outstanding` will remain `0` indefinitely until new group fees accrue (which may never happen for that bank), this call will permanently fail, and insurance/program fees that do accrue can never be collected either, since they're all bundled in the same failing instruction.

### Citations

**File:** programs/marginfi/src/instructions/marginfi_group/collect_bank_fees.rs (L21-24)
```rust
pub fn lending_pool_collect_bank_fees<'info>(
    mut ctx: Context<'info, LendingPoolCollectBankFees<'info>>,
) -> MarginfiResult {
    let mut bank = ctx.accounts.bank.load_mut()?;
```

**File:** programs/marginfi/src/instructions/marginfi_group/collect_bank_fees.rs (L55-125)
```rust
    let (insurance_fee_transfer_amount, new_outstanding_insurance_fees) = {
        let outstanding = I80F48::from(bank.collected_insurance_fees_outstanding);
        let transfer_amount = min(outstanding, available_liquidity).int();

        (
            transfer_amount.int(),
            outstanding
                .checked_sub(transfer_amount)
                .ok_or_else(math_error!())?,
        )
    };

    bank.collected_insurance_fees_outstanding = new_outstanding_insurance_fees.into();

    available_liquidity = available_liquidity
        .checked_sub(insurance_fee_transfer_amount)
        .ok_or_else(math_error!())?;

    let (group_fee_transfer_amount, new_outstanding_group_fees) = {
        let outstanding = I80F48::from(bank.collected_group_fees_outstanding);
        let transfer_amount = min(outstanding, available_liquidity).int();

        (
            transfer_amount.int(),
            outstanding
                .checked_sub(transfer_amount)
                .ok_or_else(math_error!())?,
        )
    };

    available_liquidity = available_liquidity
        .checked_sub(group_fee_transfer_amount)
        .ok_or_else(math_error!())?;

    assert!(available_liquidity >= I80F48::ZERO);

    bank.collected_group_fees_outstanding = new_outstanding_group_fees.into();

    bank.withdraw_spl_transfer(
        group_fee_transfer_amount
            .checked_to_num()
            .ok_or_else(math_error!())?,
        liquidity_vault.to_account_info(),
        fee_vault.to_account_info(),
        liquidity_vault_authority.to_account_info(),
        maybe_bank_mint.as_ref(),
        token_program.to_account_info(),
        bank_signer!(
            BankVaultType::Liquidity,
            ctx.accounts.bank.key(),
            bank.liquidity_vault_authority_bump
        ),
        ctx.remaining_accounts,
    )?;

    bank.withdraw_spl_transfer(
        insurance_fee_transfer_amount
            .checked_to_num()
            .ok_or_else(math_error!())?,
        liquidity_vault.to_account_info(),
        insurance_vault.to_account_info(),
        liquidity_vault_authority.to_account_info(),
        maybe_bank_mint.as_ref(),
        token_program.to_account_info(),
        bank_signer!(
            BankVaultType::Liquidity,
            ctx.accounts.bank.key(),
            bank.liquidity_vault_authority_bump
        ),
        ctx.remaining_accounts,
    )?;
```

**File:** programs/marginfi/src/instructions/marginfi_group/collect_bank_fees.rs (L127-163)
```rust
    // Transfer the program fee
    let (program_fee_transfer_amount, new_outstanding_program_fees) = {
        let outstanding = I80F48::from(bank.collected_program_fees_outstanding);
        let transfer_amount = min(outstanding, available_liquidity).int();

        (
            transfer_amount.int(),
            outstanding
                .checked_sub(transfer_amount)
                .ok_or_else(math_error!())?,
        )
    };

    available_liquidity = available_liquidity
        .checked_sub(program_fee_transfer_amount)
        .ok_or_else(math_error!())?;

    assert!(available_liquidity >= I80F48::ZERO);

    bank.collected_program_fees_outstanding = new_outstanding_program_fees.into();

    bank.withdraw_spl_transfer(
        program_fee_transfer_amount
            .checked_to_num()
            .ok_or_else(math_error!())?,
        liquidity_vault.to_account_info(),
        fee_ata.to_account_info(),
        liquidity_vault_authority.to_account_info(),
        maybe_bank_mint.as_ref(),
        token_program.to_account_info(),
        bank_signer!(
            BankVaultType::Liquidity,
            ctx.accounts.bank.key(),
            bank.liquidity_vault_authority_bump
        ),
        ctx.remaining_accounts,
    )?;
```

**File:** programs/marginfi/src/instructions/marginfi_group/collect_bank_fees.rs (L181-234)
```rust
#[derive(Accounts)]
pub struct LendingPoolCollectBankFees<'info> {
    #[account(
        constraint = (
            !group.load()?.is_protocol_paused()
        ) @ MarginfiError::ProtocolPaused
    )]
    pub group: AccountLoader<'info, MarginfiGroup>,

    #[account(
        mut,
        has_one = group @ MarginfiError::InvalidGroup
    )]
    pub bank: AccountLoader<'info, Bank>,

    /// CHECK: ⋐ ͡⋄ ω ͡⋄ ⋑
    #[account(
        seeds = [
            LIQUIDITY_VAULT_AUTHORITY_SEED.as_bytes(),
            bank.key().as_ref(),
        ],
        bump = bank.load()?.liquidity_vault_authority_bump
    )]
    pub liquidity_vault_authority: UncheckedAccount<'info>,

    #[account(
        mut,
        seeds = [
            LIQUIDITY_VAULT_SEED.as_bytes(),
            bank.key().as_ref(),
        ],
        bump = bank.load()?.liquidity_vault_bump
    )]
    pub liquidity_vault: InterfaceAccount<'info, TokenAccount>,

    #[account(
        mut,
        seeds = [
            INSURANCE_VAULT_SEED.as_bytes(),
            bank.key().as_ref(),
        ],
        bump = bank.load()?.insurance_vault_bump
    )]
    pub insurance_vault: InterfaceAccount<'info, TokenAccount>,

    #[account(
        mut,
        seeds = [
            FEE_VAULT_SEED.as_bytes(),
            bank.key().as_ref(),
        ],
        bump = bank.load()?.fee_vault_bump
    )]
    pub fee_vault: InterfaceAccount<'info, TokenAccount>,
```

**File:** programs/marginfi/src/state/bank.rs (L768-850)
```rust
    #[allow(unused_variables)]
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
