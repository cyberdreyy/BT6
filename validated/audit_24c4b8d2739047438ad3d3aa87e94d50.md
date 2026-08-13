### Title
Protocol fee reserves (`collected_insurance_fees_outstanding`, `collected_group_fees_outstanding`, `collected_program_fees_outstanding`) can be borrowed out of the `liquidity_vault` before they are collected - ([File: programs/marginfi/src/instructions/marginfi_account/borrow.rs])

### Summary
`lending_account_borrow` transfers tokens straight out of a bank's `liquidity_vault` based only on the borrower's collateral health, with no check that the vault retains enough balance to cover the bank's outstanding, uncollected fee buckets (`collected_insurance_fees_outstanding`, `collected_group_fees_outstanding`, `collected_program_fees_outstanding`). This mirrors the Sentiment `LToken.lendTo` finding: a protocol reserve that is intentionally meant to be preserved (insurance backstop, group/program revenue) can be lent out to borrowers because the borrow path never subtracts the reserve from "available to lend" liquidity.

### Finding Description
In marginfi, fee accrual is pure bookkeeping: `Bank::accrue_interest` increments `collected_insurance_fees_outstanding`, `collected_group_fees_outstanding`, and `collected_program_fees_outstanding` without moving any tokens [1](#0-0) . These amounts remain sitting in the bank's `liquidity_vault` as an implicit reserve until someone runs the permissionless `LendingPoolCollectBankFees` instruction, which sweeps `min(outstanding, available_liquidity)` from `liquidity_vault` into the `insurance_vault`/`fee_vault`/`fee_ata` [2](#0-1) . The use of `min()` here is itself evidence the protocol anticipated the vault may not have enough balance to cover outstanding fees.

The bank-solvency invariant confirms the intended relationship: `vault_balance - outstanding_fees ≈ total_deposits - total_liabilities` [3](#0-2) . In other words, the outstanding fee buckets are meant to be a reserved slice of the vault's SPL balance that is separate from, and should not be consumed by, ordinary borrow/withdraw flows.

However, `lending_account_borrow` never checks this. It accrues interest, validates the borrower's collateral/health, and then unconditionally calls `bank.withdraw_spl_transfer` for the requested `amount_pre_fee`, moving tokens from `liquidity_vault` to the borrower's destination account [4](#0-3) . There is no `check!` comparing `liquidity_vault.amount` (or `amount - outstanding_fees`) against the requested borrow amount anywhere in this instruction's account or handler logic [5](#0-4) . As long as the borrower is sufficiently collateralized elsewhere in the protocol (health check at the end of the function), they can drain the vault down to (or below) the outstanding-fees threshold, exactly as described in the Sentiment report for `LToken.lendTo`.

### Impact Explanation
The `collected_insurance_fees_outstanding` bucket funds the insurance vault, which is the designated backstop used by `LendingPoolHandleBankruptcy` to cover socialized bad debt [6](#0-5) . If borrowers have drained the liquidity vault such that outstanding insurance fees cannot be fully collected (the `min()` cap in `LendingPoolCollectBankFees` silently defers collection), the insurance vault can end up under-funded exactly when a bankruptcy event needs it, forcing a larger socialized loss onto remaining LPs than intended. Group and program fee revenue can similarly be deferred/starved. This is a durable accounting/availability degradation with real financial effect (insurance backstop failing to materialize, fee revenue withheld), not merely a cosmetic issue.

### Likelihood Explanation
This requires no special privilege — any account holder with adequate collateral can call `lending_account_borrow` repeatedly/aggressively on a bank whose outstanding fees have accrued, pushing the vault balance below the outstanding-fee total. Since fee collection (`LendingPoolCollectBankFees`) is permissionless but is "usually run just before a withdraw" per the admin guide rather than before every borrow, and interest/fees accrue continuously on active banks, the window in which reserves can be under-collected is realistic on any bank with meaningful utilization and borrow activity.

### Recommendation
Before transferring tokens in `lending_account_borrow` (and other paths that pull from `liquidity_vault`, e.g. `lending_account_withdraw`), require that the vault's post-withdrawal balance remains at least equal to the sum of the bank's outstanding fee buckets (`collected_insurance_fees_outstanding + collected_group_fees_outstanding + collected_program_fees_outstanding`), or reject/cap the operation otherwise:
```rust
let outstanding_reserves: I80F48 = I80F48::from(bank.collected_insurance_fees_outstanding)
    + I80F48::from(bank.collected_group_fees_outstanding)
    + I80F48::from(bank.collected_program_fees_outstanding);
check!(
    I80F48::from_num(bank_liquidity_vault.amount) - I80F48::from_num(amount_pre_fee) >= outstanding_reserves,
    MarginfiError::InsufficientLiquidityReserve
);
```
Alternatively, opportunistically run fee collection as part of `accrue_interest`/borrow so outstanding fees never accumulate unbounded inside a vault that borrowers can freely draw down from.

### Proof of Concept
Conceptual sequence (matches the Sentiment report's pattern):
1. Bank B has `liquidity_vault` balance = 1,000 (LP deposits), and over time interest accrues so `collected_insurance_fees_outstanding` = 50, `collected_group_fees_outstanding` = 30 (per `Bank::accrue_interest`, no tokens move yet) [1](#0-0) .
2. A well-collateralized borrower calls `lending_account_borrow` for 1,000 tokens against bank B. `lending_account_borrow` accrues interest, checks the borrower's own account health, and unconditionally transfers the full requested amount from `liquidity_vault` [4](#0-3)  — nothing checks that 80 tokens should stay reserved for insurance/group fees.
3. `liquidity_vault` balance drops to 0 (or near 0).
4. Anyone calls `LendingPoolCollectBankFees`; `available_liquidity` is 0, so `min(outstanding, available_liquidity)` transfers 0 to `insurance_vault`/`fee_vault`, leaving `collected_insurance_fees_outstanding`/`collected_group_fees_outstanding` unchanged [7](#0-6) .
5. If bank B's borrowers subsequently default and `LendingPoolHandleBankruptcy` is invoked, the insurance vault lacks the funds it should have accumulated, reducing coverage for socialized bad debt [6](#0-5) .

Note: I could not fully trace whether `LendingPoolHandleBankruptcy`'s insurance-coverage calculation reads `bank.collected_insurance_fees_outstanding` directly or only the actual `insurance_vault` SPL balance (the file read was truncated before I could confirm this detail), so the exact bankruptcy-time impact quantification is based on the visible `withdraw_spl_transfer` call moving funds *from* `insurance_vault` and the general architecture rather than a full read of the coverage-calculation logic.

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

**File:** programs/marginfi/src/instructions/marginfi_group/collect_bank_fees.rs (L53-91)
```rust
    let mut available_liquidity = I80F48::from_num(liquidity_vault.amount);

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

**File:** programs/marginfi/src/instructions/marginfi_account/borrow.rs (L43-156)
```rust
pub fn lending_account_borrow<'info>(
    mut ctx: Context<'info, LendingAccountBorrow<'info>>,
    amount: u64,
) -> MarginfiResult {
    let LendingAccountBorrow {
        marginfi_account: marginfi_account_loader,
        destination_token_account,
        liquidity_vault: bank_liquidity_vault,
        token_program,
        bank_liquidity_vault_authority,
        bank: bank_loader,
        group: marginfi_group_loader,
        ..
    } = ctx.accounts;
    let clock = Clock::get()?;
    let maybe_bank_mint = utils::maybe_take_bank_mint(
        &mut ctx.remaining_accounts,
        &*bank_loader.load()?,
        token_program.key,
    )?;

    let mut marginfi_account = marginfi_account_loader.load_mut()?;
    let group = marginfi_group_loader.load()?;

    let program_fee_rate: I80F48 = group.fee_state_cache.program_fee_rate.into();

    check!(
        !marginfi_account.get_flag(ACCOUNT_DISABLED)
        // Sanity check: liquidation doesn't allow the borrow ix, but just in case
            && !marginfi_account.get_flag(ACCOUNT_IN_RECEIVERSHIP),
        MarginfiError::AccountDisabled
    );

    bank_loader.load_mut()?.accrue_interest(
        clock.unix_timestamp,
        &group,
        #[cfg(not(feature = "client"))]
        bank_loader.key(),
    )?;

    let group_rate_limit_enabled = group.rate_limiter.is_enabled();

    let mut origination_fee: I80F48 = I80F48::ZERO;
    let amount_pre_fee;
    {
        let mut bank = bank_loader.load_mut()?;

        validate_asset_tags(&bank, &marginfi_account)?;
        validate_bank_state(&bank, InstructionKind::FailsIfPausedOrReduceState)?;

        let liquidity_vault_authority_bump = bank.liquidity_vault_authority_bump;
        let origination_fee_rate: I80F48 = bank
            .config
            .interest_rate_config
            .protocol_origination_fee
            .into();

        let lending_account = &mut marginfi_account.lending_account;
        let mut bank_account =
            BankAccountWrapper::find_or_create(&bank_loader.key(), &mut bank, lending_account)?;

        // User needs to borrow amount + fee to receive amount
        amount_pre_fee = maybe_bank_mint
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

        let (origination_fee_u64, share_amount): (u64, I80F48);
        if !origination_fee_rate.is_zero() {
            origination_fee = I80F48::from_num(amount_pre_fee)
                .checked_mul(origination_fee_rate)
                .ok_or_else(math_error!())?;
            origination_fee_u64 = origination_fee.checked_to_num().ok_or_else(math_error!())?;

            // Incurs a borrow that includes the origination fee (but withdraws just the amt)
            share_amount =
                bank_account.borrow(I80F48::from_num(amount_pre_fee) + origination_fee)?;
        } else {
            // Incurs a borrow for the amount without any fee
            origination_fee_u64 = 0;
            share_amount = bank_account.borrow(I80F48::from_num(amount_pre_fee))?;
        }

        let resulting_liability_shares: I80F48 = bank_account.balance.liability_shares.into();
        check!(
            resulting_liability_shares <= I80F48::ZERO
                || resulting_liability_shares >= EMPTY_BALANCE_THRESHOLD,
            MarginfiError::IllegalBalanceState,
            "Borrow would leave positive liability shares below the empty balance threshold"
        );

        marginfi_account.last_update = clock.unix_timestamp as u64;

        bank.withdraw_spl_transfer(
            amount_pre_fee,
            bank_liquidity_vault.to_account_info(),
            destination_token_account.to_account_info(),
            bank_liquidity_vault_authority.to_account_info(),
            maybe_bank_mint.as_ref(),
            token_program.to_account_info(),
            bank_signer!(
                BankVaultType::Liquidity,
                bank_loader.key(),
                liquidity_vault_authority_bump
            ),
            ctx.remaining_accounts,
        )?;
```

**File:** programs/marginfi/src/instructions/marginfi_group/handle_bankruptcy.rs (L150-188)
```rust
    // Cover bad debt with insurance funds.
    let covered_by_insurance_rounded_up: u64 = covered_by_insurance
        .checked_ceil()
        .ok_or_else(math_error!())?
        .checked_to_num()
        .ok_or_else(math_error!())?;
    debug!(
        "covered_by_insurance_rounded_up: {}; socialized loss {}",
        covered_by_insurance_rounded_up,
        socialized_loss.to_num::<f64>()
    );

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
