### Title
`lending_pool_collect_bank_fees` bundles three fee transfers in one atomic instruction, so a failure on any single destination blocks unrelated fee recipients - ([File: programs/marginfi/src/instructions/marginfi_group/collect_bank_fees.rs])

### Summary
The reported ERC314 bug is a class of "shared fee-claim atomicity" issue: a single instruction pays out multiple, unrelated fee recipients, and if a transfer to any one recipient reverts, none of the recipients get paid. Marginfi's `lending_pool_collect_bank_fees` instruction has the same structural shape: it moves `collected_group_fees_outstanding`, `collected_insurance_fees_outstanding`, and `collected_program_fees_outstanding` from the `liquidity_vault` to the `fee_vault`, `insurance_vault`, and `fee_ata` (the global fee wallet's ATA) respectively, all inside one atomic call.

### Finding Description
`lending_pool_collect_bank_fees` performs three sequential `bank.withdraw_spl_transfer` CPIs in a single instruction:
1. Group fee → `fee_vault`
2. Insurance fee → `insurance_vault`
3. Program fee → `fee_ata` (canonical ATA of `FeeState.global_fee_wallet`) [1](#0-0) 

The bank uses `TokenInterface`/`InterfaceAccount<TokenAccount>` throughout, meaning the underlying mint can be a Token-2022 mint with a freeze authority or transfer-hook extension. [2](#0-1) 

If any one of the three destination token accounts (most plausibly `fee_ata`, which belongs to an externally-controlled `global_fee_wallet` rather than a program-owned PDA) is frozen, closed, or governed by a transfer-hook program that reverts, the CPI transfer fails and the whole instruction — including the `collected_group_fees_outstanding` and `collected_insurance_fees_outstanding` decrements and their corresponding transfers to the `fee_vault`/`insurance_vault` PDAs — reverts too, per Solana's atomic transaction semantics. This mirrors the report's core problem: one recipient's ability to receive funds becoming coupled to (and able to block) another, unrelated recipient's ability to receive funds, because both payouts are bundled in a non-fault-isolated batch.

Per the docs, this instruction is permissionless and expected to run frequently ("we run this ix just before a withdraw"), so if the `fee_ata` transfer becomes unable to succeed, the insurance vault and group fee vault also stop receiving their outstanding fees indefinitely, since `fees_outstanding` fields are never reset without a successful full instruction execution. [3](#0-2) 

### Impact Explanation
If the program/global fee recipient path becomes unable to receive tokens (frozen ATA, revoked/incompatible ATA, or a reverting Token-2022 transfer hook on the mint), the insurance fund and group fee vault — both legitimately protocol-owned accounts — are also durably blocked from receiving their share of already-accrued fees, since all three transfers share one atomic instruction with no fault isolation. This is a durable freeze of fee/insurance accrual with financial effect for the group and the protocol.

### Likelihood Explanation
Low-to-moderate. It requires a mint (via Token-2022 freeze authority or transfer-hook program) or the specific `fee_ata` account to be in a state that makes transfers to it fail persistently, which is more likely for exotic Token-2022 mints supported by the bank's `TokenInterface` typing than for standard SPL mints. It does not require any marginfi-privileged action — only that the `global_fee_wallet`'s token account/mint enters a failing state.

### Recommendation
Decouple the three fee transfers so a failure in one does not block the others — e.g., split `lending_pool_collect_bank_fees` into independent, individually-permissionless legs per destination (group/insurance/program), or wrap each `withdraw_spl_transfer` CPI so a failure on one leg does not revert the fee-outstanding decrements/transfers already performed for the other legs.

### Proof of Concept
1. Configure (or have) a Token-2022 mint used by a bank whose `global_fee_wallet` ATA is frozen by the mint's freeze authority, or attach a transfer-hook program to the mint that reverts specifically for transfers into the `fee_ata` account.
2. Accrue interest so `collected_group_fees_outstanding` and `collected_insurance_fees_outstanding` are non-zero (see `accrue_interest` referenced in `guides/ADMIN/COLLECTING_FEES.md`).
3. Call `lending_pool_collect_bank_fees`. The first two transfers (group fee, insurance fee) succeed inline, but the third CPI (program fee to `fee_ata`) fails, causing the whole instruction — including the state changes and transfers to `fee_vault`/`insurance_vault` — to revert.
4. Group and insurance fees remain stuck as `outstanding` indefinitely until the `fee_ata`/mint issue is resolved, even though those two vaults are otherwise fully capable of receiving funds. [4](#0-3)

### Citations

**File:** programs/marginfi/src/instructions/marginfi_group/collect_bank_fees.rs (L21-38)
```rust
pub fn lending_pool_collect_bank_fees<'info>(
    mut ctx: Context<'info, LendingPoolCollectBankFees<'info>>,
) -> MarginfiResult {
    let mut bank = ctx.accounts.bank.load_mut()?;

    // Validate the program fee ata is correct
    {
        let mint = &bank.mint;
        let global_fee_wallet = &ctx.accounts.fee_state.load()?.global_fee_wallet;
        let token_program_id = &ctx.accounts.token_program.key();
        let program_fee_ata = &ctx.accounts.fee_ata.key();
        let ata_expected =
            get_associated_token_address_with_program_id(global_fee_wallet, mint, token_program_id);
        check!(
            program_fee_ata.eq(&ata_expected),
            MarginfiError::InvalidFeeAta
        );
    }
```

**File:** programs/marginfi/src/instructions/marginfi_group/collect_bank_fees.rs (L93-163)
```rust
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

**File:** programs/marginfi/src/instructions/marginfi_group/collect_bank_fees.rs (L181-250)
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

    // Note: there is just one FeeState per program, so no further check is required.
    #[account(
        seeds = [FEE_STATE_SEED.as_bytes()],
        bump,
    )]
    pub fee_state: AccountLoader<'info, FeeState>,

    /// CHECK: Canonical ATA of the `FeeState.global_fee_wallet` for the mint used by this bank
    /// (validated in handler). Must already exist, may require initializing the ATA if it does not
    /// already exist prior to this ix.
    #[account(mut)]
    pub fee_ata: InterfaceAccount<'info, TokenAccount>,

    pub token_program: Interface<'info, TokenInterface>,
}
```

**File:** guides/ADMIN/COLLECTING_FEES.md (L60-72)
```markdown
## How Fees Are Collected

(1) Interset accrues and increases the number in `fees_outstanding` fields. Optionally, origination
fees are charged and do the same.

(2) Anyone (the ix is permissionless) runs `LendingPoolCollectBankFees`. This moves:
* The amount specified in `collected_insurance_fees_outstanding` from the `liquidity_vault` to the
  `insurance_vault`
* The amount specified in `collected_group_fees_outstanding` from the `liquidity_vault` to the `fee_vault`
* The amount specified in `collected_program_fees_outstanding` from the `liquidity_vault` to the
    `fee_ata`, which is the cannonical ATA of the global fee wallet.

Then the `fees_outstanding` are all reset to zero. The vast majority of the time, we run this ix just before a withdraw.
```
