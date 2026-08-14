Based on my analysis of `lending_pool_handle_bankruptcy` and the utility functions in `general.rs`, I found no valid, exploitable vulnerability matching the described scenario.

The bankruptcy flow computes `available_insurance_fund` via `calculate_post_fee_spl_deposit_amount(mint, insurance_vault.amount, epoch)`, which correctly represents the maximum post-fee amount that could ever be delivered to the liquidity vault if the entire insurance vault balance were transferred out. [1](#0-0) 
This is then used only as an upper bound via `min(bad_debt, available_insurance_fund)`, so `covered_by_insurance` never exceeds the post-fee-realizable value of the vault. [2](#0-1) 
The actual withdrawal amount is then derived from `covered_by_insurance_rounded_up` via `calculate_pre_fee_spl_deposit_amount`, the exact inverse operation, using the SPL-ported `calculate_pre_fee_amount` formula which is explicitly noted as a backported correctness fix from `solana-program-library`. [3](#0-2) [4](#0-3) 
This is a coherent pre-fee/post-fee round trip: post-fee amount bounds the insurance draw, and pre-fee amount is computed as the inverse to obtain exactly that post-fee amount at the liquidity vault, consistent with the pattern used identically in `deposit.rs` (pre-fee amount computed from a post-fee/user-specified `deposit_amount`) and `withdraw.rs` (same pattern for withdrawals). [5](#0-4) [6](#0-5) 

There is no "cross-family substitution" opportunity here: the insurance vault and liquidity vault accounts are both strictly derived via PDA `seeds`/`bump` constraints tied to the specific `bank` account (`INSURANCE_VAULT_SEED`, `LIQUIDITY_VAULT_SEED`), so an attacker cannot substitute a foreign bank's vault, a fee vault, or an integration vault into these slots — Anchor's seed constraints will reject any account that doesn't match the exact PDA derivation for that specific `bank`. [7](#0-6) 
The `insurance_vault` is further typed as `InterfaceAccount<TokenAccount>` (mint/owner validated by Anchor's SPL account deserialization), and `maybe_take_bank_mint` validates that any supplied Token-2022 mint matches `bank.mint` exactly, rejecting foreign mints. [8](#0-7) 

The only theoretical residual risk is integer rounding in the pre-fee/post-fee round trip (`ceil_div` in `calculate_pre_fee_amount`) potentially causing the computed pre-fee withdrawal to be off by at most 1 token unit from the true inverse, which could at most cause a transaction failure (insufficient vault balance) — not value theft or redirection — and this is explicitly called out as a backported upstream correctness fix, not a marginfi-specific defect. [9](#0-8) 

I was unable to find any consuming path where a "pre-fee" value from one helper is compared against or substituted for a "post-fee" value from a different, incompatible context (e.g., an integration/Kamino/Drift/Solend settlement path improperly reusing insurance-fund accounting). The integration asset tags (`is_kamino_asset_tag`, `is_drift_asset_tag`, etc.) are unrelated to this transfer-fee accounting and are not used in `lending_pool_handle_bankruptcy`, which is gated to `is_marginfi_asset_tag` banks only. [10](#0-9) [11](#0-10) 

#No vulnerability found for this question.

### Citations

**File:** programs/marginfi/src/instructions/marginfi_group/handle_bankruptcy.rs (L130-148)
```rust
    let (covered_by_insurance, socialized_loss) = {
        let available_insurance_fund: I80F48 = maybe_bank_mint
            .as_ref()
            .map(|mint| {
                utils::calculate_post_fee_spl_deposit_amount(
                    mint.to_account_info(),
                    insurance_vault.amount,
                    clock.epoch,
                )
            })
            .transpose()?
            .unwrap_or(insurance_vault.amount)
            .into();

        let covered_by_insurance = min(bad_debt, available_insurance_fund);
        let socialized_loss = max(bad_debt - covered_by_insurance, I80F48::ZERO);

        (covered_by_insurance, socialized_loss)
    };
```

**File:** programs/marginfi/src/instructions/marginfi_group/handle_bankruptcy.rs (L162-187)
```rust
    let insurance_coverage_deposit_pre_fee = maybe_bank_mint
        .as_ref()
        .map(|mint| {
            utils::calculate_pre_fee_spl_deposit_amount(
                mint.to_account_info(),
                covered_by_insurance_rounded_up,
                clock.epoch,
            )
        })
        .transpose()?
        .unwrap_or(covered_by_insurance_rounded_up);

    bank.withdraw_spl_transfer(
        insurance_coverage_deposit_pre_fee,
        ctx.accounts.insurance_vault.to_account_info(),
        ctx.accounts.liquidity_vault.to_account_info(),
        ctx.accounts.insurance_vault_authority.to_account_info(),
        maybe_bank_mint.as_ref(),
        token_program.to_account_info(),
        bank_signer!(
            BankVaultType::Insurance,
            bank_loader.key(),
            bank.insurance_vault_authority_bump
        ),
        ctx.remaining_accounts,
    )?;
```

**File:** programs/marginfi/src/instructions/marginfi_group/handle_bankruptcy.rs (L244-250)
```rust
    #[account(
        mut,
        has_one = group @ MarginfiError::InvalidGroup,
        constraint = is_marginfi_asset_tag(bank.load()?.config.asset_tag)
            @ MarginfiError::WrongAssetTagForStandardInstructions
    )]
    pub bank: AccountLoader<'info, Bank>,
```

**File:** programs/marginfi/src/instructions/marginfi_group/handle_bankruptcy.rs (L264-296)
```rust
    /// CHECK: Seed constraint
    #[account(
        mut,
        seeds = [
            LIQUIDITY_VAULT_SEED.as_bytes(),
            bank.key().as_ref(),
        ],
        bump = bank.load()?.liquidity_vault_bump
    )]
    pub liquidity_vault: UncheckedAccount<'info>,

    #[account(
        mut,
        seeds = [
            INSURANCE_VAULT_SEED.as_bytes(),
            bank.key().as_ref(),
        ],
        bump = bank.load()?.insurance_vault_bump
    )]
    pub insurance_vault: Box<InterfaceAccount<'info, TokenAccount>>,

    /// CHECK: Seed constraint
    #[account(
        seeds = [
            INSURANCE_VAULT_AUTHORITY_SEED.as_bytes(),
            bank.key().as_ref(),
        ],
        bump = bank.load()?.insurance_vault_authority_bump
    )]
    pub insurance_vault_authority: UncheckedAccount<'info>,

    pub token_program: Interface<'info, TokenInterface>,
}
```

**File:** programs/marginfi/src/utils/general.rs (L154-181)
```rust
pub fn maybe_take_bank_mint<'info>(
    remaining_accounts: &mut &'info [AccountInfo<'info>],
    bank: &Bank,
    token_program: &Pubkey,
) -> MarginfiResult<Option<InterfaceAccount<'info, Mint>>> {
    match *token_program {
        anchor_spl::token::ID => Ok(None),
        anchor_spl::token_2022::ID => {
            let (maybe_mint, remaining) = remaining_accounts
                .split_first()
                .ok_or(MarginfiError::T22MintRequired)?;
            *remaining_accounts = remaining;

            if bank.mint != *maybe_mint.key {
                return err!(MarginfiError::T22MintRequired);
            }

            InterfaceAccount::try_from(maybe_mint)
                .map(Option::Some)
                .map_err(|e| {
                    msg!("failed to parse mint account: {:?}", e);
                    MarginfiError::T22MintRequired.into()
                })
        }

        _ => panic!("unsupported token program"),
    }
}
```

**File:** programs/marginfi/src/utils/general.rs (L183-209)
```rust
const ONE_IN_BASIS_POINTS: u128 = 10_000;
/// backported fix from
/// https://github.com/solana-labs/solana-program-library/commit/20e6792179fc7f1251579c1c33a4a0feec48e15e
pub fn calculate_pre_fee_amount(transfer_fee: &TransferFee, post_fee_amount: u64) -> Option<u64> {
    let maximum_fee = u64::from(transfer_fee.maximum_fee);
    let transfer_fee_basis_points = u16::from(transfer_fee.transfer_fee_basis_points) as u128;
    match (transfer_fee_basis_points, post_fee_amount) {
        // no fee, same amount
        (0, _) => Some(post_fee_amount),
        // 0 zero out, 0 in
        (_, 0) => Some(0),
        // 100%, cap at max fee
        (ONE_IN_BASIS_POINTS, _) => maximum_fee.checked_add(post_fee_amount),
        _ => {
            let numerator = (post_fee_amount as u128).checked_mul(ONE_IN_BASIS_POINTS)?;
            let denominator = ONE_IN_BASIS_POINTS.checked_sub(transfer_fee_basis_points)?;
            let raw_pre_fee_amount = ceil_div(numerator, denominator)?;

            if raw_pre_fee_amount.checked_sub(post_fee_amount as u128)? >= maximum_fee as u128 {
                post_fee_amount.checked_add(maximum_fee)
            } else {
                // should return `None` if `pre_fee_amount` overflows
                u64::try_from(raw_pre_fee_amount).ok()
            }
        }
    }
}
```

**File:** programs/marginfi/src/utils/general.rs (L424-458)
```rust
pub fn is_marginfi_asset_tag(asset_tag: u8) -> bool {
    matches!(
        asset_tag,
        ASSET_TAG_DEFAULT | ASSET_TAG_SOL | ASSET_TAG_STAKED
    )
}

/// Helper function for constraint validation - checks if asset tag is valid for Kamino operations
pub fn is_kamino_asset_tag(asset_tag: u8) -> bool {
    asset_tag == ASSET_TAG_KAMINO
}

/// Helper function for constraint validation - checks if asset tag is valid for Drift operations
pub fn is_drift_asset_tag(asset_tag: u8) -> bool {
    asset_tag == ASSET_TAG_DRIFT
}

/// Helper function for constraint validation - checks if asset tag is valid for Solend operations
pub fn is_solend_asset_tag(asset_tag: u8) -> bool {
    asset_tag == ASSET_TAG_SOLEND
}

/// Helper function for constraint validation - checks if asset tag is valid for JupLend operations
pub fn is_juplend_asset_tag(asset_tag: u8) -> bool {
    asset_tag == ASSET_TAG_JUPLEND
}

/// Helper function - checks if asset tag is an integration type (Kamino, Drift, Solend, or JupLend)
/// These integrations share a position limit due to their 3-account-per-position overhead
pub fn is_integration_asset_tag(asset_tag: u8) -> bool {
    matches!(
        asset_tag,
        ASSET_TAG_KAMINO | ASSET_TAG_DRIFT | ASSET_TAG_SOLEND | ASSET_TAG_JUPLEND
    )
}
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

**File:** programs/marginfi/src/instructions/marginfi_account/withdraw.rs (L112-131)
```rust
        let (amount_pre_fee, share_amount) = if withdraw_all {
            // Note: In liquidation, we still want this passed on the books
            bank_account.withdraw_all(in_receivership)?
        } else {
            let amount_pre_fee = maybe_bank_mint
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

            let share_amount = bank_account.withdraw(I80F48::from_num(amount_pre_fee))?;

            (amount_pre_fee, share_amount)
        };
```
