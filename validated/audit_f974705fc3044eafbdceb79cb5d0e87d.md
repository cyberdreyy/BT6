### Title
Permissionless bank closure permanently strands uncollected protocol/group/insurance fees - ([File: programs/marginfi/src/instructions/marginfi_group/close_bank.rs])

### Summary
`lending_pool_close_bank` only verifies that `total_asset_shares`, `total_liability_shares`, and `emissions_remaining` are zero before closing a bank, but never checks that the fee accrual buckets (`collected_insurance_fees_outstanding`, `collected_group_fees_outstanding`, `collected_program_fees_outstanding`) are zero, or that the `liquidity_vault` token balance is zero. This mirrors the SteadeFi report's root cause: yield/fees are only claimable through one specific instruction, and an unrelated "close"/state-transition path removes the ability to ever call that instruction again, permanently stranding the funds.

### Finding Description
`accrue_interest` accumulates outstanding fees into `collected_insurance_fees_outstanding`, `collected_group_fees_outstanding`, and `collected_program_fees_outstanding` whenever there is nonzero interest activity, and these amounts sit as real tokens inside the bank's `liquidity_vault` until someone calls the permissionless `LendingPoolCollectBankFees` instruction to sweep them into the `insurance_vault`/`fee_vault`/`fee_ata`: [1](#0-0) 

Critically, these fee buckets are only ever decremented by `lending_pool_collect_bank_fees`, not by repayment or withdrawal flows. Once all borrowers repay in full and all depositors withdraw, `total_asset_shares` and `total_liability_shares` both go to zero, but `collected_*_fees_outstanding` can remain nonzero, with the corresponding tokens still physically sitting in `liquidity_vault`. This exact reconciliation invariant (`vault_balance - outstanding_fees == total_deposits - total_liabilities`) is explicitly encoded in the project's own fuzz/invariant tests: [2](#0-1) 

`lending_pool_close_bank` checks only asset/liability shares and emissions before permanently closing the `Bank` account (`close = admin`), with no check on the fee buckets or on vault balances: [3](#0-2) 

After the bank account is closed, its `AccountLoader<Bank>` can no longer be deserialized (Anchor zeroes/invalidates the discriminator and transfers lamports to the admin). Every subsequent instruction that could move the stranded liquidity — `LendingPoolCollectBankFees`, `LendingPoolWithdrawFees`, `LendingPoolWithdrawFeesPermissionless` — requires `bank.load()`/`bank.load_mut()` and PDA bump values stored in that account: [4](#0-3) [5](#0-4) 

Once the bank account is gone, none of these instructions can be constructed/validated, so any tokens left in `liquidity_vault` (representing un-swept insurance/group/program fees) become permanently unrecoverable — analogous to the SteadeFi trove funds becoming unclaimable once `compound()` can no longer be called after the vault status transitions to `Closed`.

### Impact Explanation
This is a direct, durable loss of protocol funds. Any accrued insurance fee (meant to backstop bad debt), group fee (revenue for the group/marginfi), and program fee (protocol revenue) that has not yet been swept via `LendingPoolCollectBankFees` at the moment a bank is closed is permanently lost — the tokens remain locked in an orphaned `liquidity_vault` token account whose only spending authority PDA can no longer be validated on-chain. This can occur through ordinary admin bank wind-down (repay everything → withdraw everything → close bank) if the admin does not (or cannot, e.g., due to ordering/race with the last withdrawal transaction) call fee collection first. There is no code path or mechanism to recover these funds afterward.

### Likelihood Explanation
Moderate-to-high. Any bank that ever had active borrowing will have accrued nonzero fee-outstanding balances via `accrue_interest`. Closing a bank is an intentional, normal admin lifecycle action documented in the bank-state guide ("Closure: once all positions are closed and the bank is empty, the admin can close the bank"), and nothing in the `lending_pool_close_bank` checks or in the associated tests (`tests/specs/basic/13_closebank.spec.ts`) verifies fee buckets are drained first. The bug requires no attacker — it is a footgun in the standard wind-down flow, making it likely to be triggered accidentally in production bank retirement.

### Recommendation
Add checks in `lending_pool_close_bank` that `collected_insurance_fees_outstanding`, `collected_group_fees_outstanding`, and `collected_program_fees_outstanding` are all zero (with the same `is_zero_with_tolerance` pattern already used for shares), and/or require the `liquidity_vault`, `insurance_vault`, and `fee_vault` balances to be zero before allowing closure. Alternatively, automatically invoke fee collection/sweep logic as part of the close instruction so outstanding fees are flushed out before the bank account is destroyed.

### Proof of Concept
1. Create a bank; have a lender deposit and a borrower borrow against another collateral bank.
2. Let time pass so `accrue_interest` runs (e.g., via any deposit/withdraw/borrow/repay, or via permissionless `LendingPoolAccrueBankInterest`), causing `collected_group_fees_outstanding`/`collected_insurance_fees_outstanding`/`collected_program_fees_outstanding` to become nonzero while the corresponding tokens sit in `liquidity_vault` (see the invariant relation in `trident-tests/fuzz_0/invariants/solvency.rs`).
3. Have the borrower fully repay (driving `total_liability_shares` to 0) and the lender fully withdraw (driving `total_asset_shares` to 0), without anyone calling `LendingPoolCollectBankFees`.
4. Admin calls `lending_pool_close_bank`. The checks in `close_bank.rs` (lines 22-41) all pass since only shares/emissions are validated; the `Bank` account is closed via `close = admin`.
5. Attempt to call `LendingPoolCollectBankFees`, `LendingPoolWithdrawFees`, or `LendingPoolWithdrawFeesPermissionless` referencing the closed bank pubkey — these fail because `AccountLoader<Bank>::load()` cannot deserialize the closed account, and the vault PDAs' authority bumps cannot be verified.
6. The tokens equal to the outstanding fee amounts remain permanently stuck in the now-orphaned `liquidity_vault` token account, unrecoverable by any instruction in the program.

### Citations

**File:** programs/marginfi/src/state/bank.rs (L578-602)
```rust
        if group_fees_collected > I80F48::ZERO {
            self.collected_group_fees_outstanding = {
                group_fees_collected
                    .checked_add(self.collected_group_fees_outstanding.into())
                    .ok_or_else(math_error!())?
                    .into()
            };
        }

        if insurance_fees_collected > I80F48::ZERO {
            self.collected_insurance_fees_outstanding = {
                insurance_fees_collected
                    .checked_add(self.collected_insurance_fees_outstanding.into())
                    .ok_or_else(math_error!())?
                    .into()
            };
        }
        if protocol_fees_collected > I80F48::ZERO {
            self.collected_program_fees_outstanding = {
                protocol_fees_collected
                    .checked_add(self.collected_program_fees_outstanding.into())
                    .ok_or_else(math_error!())?
                    .into()
            };
        }
```

**File:** trident-tests/fuzz_0/invariants/solvency.rs (L58-66)
```rust
    let outstanding_fees = from_wrapped(bank.collected_group_fees_outstanding.value)
        + from_wrapped(bank.collected_insurance_fees_outstanding.value)
        + from_wrapped(bank.collected_program_fees_outstanding.value);

    let vault_balance = I80F48::from_num(token_balance(trident, bank.liquidity_vault));
    let net_vault = vault_balance - outstanding_fees;
    let net_book = total_deposits - total_liabilities;

    let drift = (net_vault - net_book).abs();
```

**File:** programs/marginfi/src/instructions/marginfi_group/close_bank.rs (L12-63)
```rust
pub fn lending_pool_close_bank(ctx: Context<LendingPoolCloseBank>) -> MarginfiResult {
    let mut group = ctx.accounts.group.load_mut()?;
    // Note: Groups created prior to 0.1.2 have a non-authoritative count here, so subtraction
    // without saturation could reduce the count below zero.
    group.banks = group.banks.saturating_sub(1);

    let bank = ctx.accounts.bank.load()?;

    // banks created prior to 0.1.4 can never be closed because we cannot guarantee an accurate
    // position count for those banks.
    check!(
        bank.get_flag(CLOSE_ENABLED_FLAG),
        MarginfiError::BankCannotClose,
        "Only banks created in 0.1.4 and later can close"
    );
    check!(
        bank.lending_position_count == 0 && bank.borrowing_position_count == 0,
        MarginfiError::BankCannotClose,
        "Only banks with no open positions can close"
    );
    check!(
        I80F48::from(bank.total_asset_shares).is_zero_with_tolerance(ZERO_AMOUNT_THRESHOLD)
            && I80F48::from(bank.total_liability_shares)
                .is_zero_with_tolerance(ZERO_AMOUNT_THRESHOLD),
        MarginfiError::BankCannotClose
    );
    check!(
        I80F48::from(bank.emissions_remaining).is_zero_with_tolerance(ZERO_AMOUNT_THRESHOLD),
        MarginfiError::BankCannotClose
    );

    drop(bank);

    // Bank will now be closed by anchor

    Ok(())
}

#[derive(Accounts)]
pub struct LendingPoolCloseBank<'info> {
    #[account(
        mut,
        has_one = admin @ MarginfiError::Unauthorized,
    )]
    pub group: AccountLoader<'info, MarginfiGroup>,

    #[account(
        mut,
        has_one = group @ MarginfiError::InvalidGroup,
        close = admin
    )]
    pub bank: AccountLoader<'info, Bank>,
```

**File:** programs/marginfi/src/instructions/marginfi_group/collect_bank_fees.rs (L21-24)
```rust
pub fn lending_pool_collect_bank_fees<'info>(
    mut ctx: Context<'info, LendingPoolCollectBankFees<'info>>,
) -> MarginfiResult {
    let mut bank = ctx.accounts.bank.load_mut()?;
```

**File:** programs/marginfi/src/instructions/marginfi_group/collect_bank_fees.rs (L181-214)
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
```
