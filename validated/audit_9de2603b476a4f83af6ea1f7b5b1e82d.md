### Title
`lending_pool_close_bank` permanently strands unclaimed insurance/group/program fees - ([File: programs/marginfi/src/instructions/marginfi_group/close_bank.rs])

### Summary
This bug is structurally analogous to the Popcorn `harvest()` finding: an "empty the vault before switching state" step can be skipped, permanently losing unclaimed value. In marginfi, `lending_pool_close_bank` closes a `Bank` account without ever verifying that outstanding, uncollected fees (`collected_insurance_fees_outstanding`, `collected_group_fees_outstanding`, `collected_program_fees_outstanding`) are zero before the bank account is destroyed.

### Finding Description
`lending_pool_close_bank` checks that the bank has `CLOSE_ENABLED_FLAG`, zero open positions, and zero total asset/liability shares before closing the bank account, but it never checks the three "fees outstanding" fields: [1](#0-0) 

Per the protocol's own documented invariant, whenever `total_asset_shares` and `total_liability_shares` are zero but fees remain outstanding, the `liquidity_vault` SPL token balance is not zero — it exactly equals the sum of the outstanding fee buckets (this is the "cash drawer reconciles with the books" solvency invariant used by the fuzzer): [2](#0-1) 

Fees only reach the vault as spendable/withdrawable by running the permissionless `LendingPoolCollectBankFees` instruction (the marginfi analog of "harvesting"), which moves `collected_insurance_fees_outstanding`/`collected_group_fees_outstanding`/`collected_program_fees_outstanding` out of the `liquidity_vault` into the insurance/fee/program vaults, per the documented fee lifecycle: [3](#0-2) [4](#0-3) 

Every account this collect instruction touches — the `liquidity_vault_authority`, `liquidity_vault`, `insurance_vault`, and `fee_vault` PDAs — derives its bump by reading `bank.load()?.<...>_bump` off the live `Bank` account: [5](#0-4) 

If the admin runs `LendingPoolCloseBank` while `collected_*_fees_outstanding` are nonzero (which the instruction currently permits, since `total_asset_shares`/`total_liability_shares` can independently be zero while fee buckets are still outstanding), the `bank` account — the only account from which `LendingPoolCollectBankFees` can source the vault bumps and group/mint validation — is closed by Anchor's `close = admin` constraint: [6](#0-5) 

With the `Bank` account gone, `LendingPoolCollectBankFees` (and any other instruction requiring `AccountLoader<'info, Bank>::load()` on that key) can never succeed again, because Anchor will fail to deserialize a closed/zeroed account as `Bank`. The insurance/group/program fee amounts left sitting in the `liquidity_vault` at the moment of closure become permanently unrecoverable through any program instruction.

### Impact Explanation
This causes a durable, unrecoverable loss of protocol/insurance/group fee revenue with no way to reclaim it — it is not a griefing scenario against a single user but a protocol-wide fund-freezing bug: any tokens sitting in the liquidity vault as unclaimed fees at bank-closure time become permanently stuck, unreachable by any subsequent instruction because the sole account that authorizes vault PDA derivation (the `Bank` account) has been destroyed. This mirrors the "unclaimed value permanently lost" impact of the Popcorn `harvest()` finding.

### Likelihood Explanation
The scenario requires only routine admin action, no attacker or special privilege abuse: `lending_pool_close_bank` is a normal admin-callable operational instruction, closing empty banks is an expected part of the bank lifecycle ("Closure" step is explicitly documented), and interest/fee accrual runs on essentially every balance-changing transaction, so it is easy for small residual fee amounts to exist at the moment an admin decides to close an empty (zero-position) bank without realizing `LendingPoolCollectBankFees` must be run first. Since `LendingPoolCollectBankFees` is permissionless and typically run "just before a withdraw" per the fee guide, there is no guarantee it has been run immediately prior to a close, especially for low-activity banks.

### Recommendation
Add checks in `lending_pool_close_bank` (mirroring the existing `total_asset_shares`/`total_liability_shares`/`emissions_remaining` checks) that revert if any of `bank.collected_insurance_fees_outstanding`, `bank.collected_group_fees_outstanding`, or `bank.collected_program_fees_outstanding` are non-zero (beyond `ZERO_AMOUNT_THRESHOLD`), forcing `LendingPoolCollectBankFees` to be run to completion before the bank can be closed:
```rust
check!(
    I80F48::from(bank.collected_insurance_fees_outstanding).is_zero_with_tolerance(ZERO_AMOUNT_THRESHOLD)
        && I80F48::from(bank.collected_group_fees_outstanding).is_zero_with_tolerance(ZERO_AMOUNT_THRESHOLD)
        && I80F48::from(bank.collected_program_fees_outstanding).is_zero_with_tolerance(ZERO_AMOUNT_THRESHOLD),
    MarginfiError::BankCannotClose,
    "Outstanding fees must be collected before closing"
);
```

### Proof of Concept
1. Admin creates and operates a bank; borrowers accrue interest over time, which increments `collected_insurance_fees_outstanding`, `collected_group_fees_outstanding`, and `collected_program_fees_outstanding` per `Bank::accrue_interest` (see `programs/marginfi/src/state/bank.rs`, lines ~578-602, not separately cited here for brevity, but part of the accrual path shown in the earlier `accrue_interest` snippet).
2. All lenders/borrowers exit the bank (positions close), driving `total_asset_shares` and `total_liability_shares` to zero, but nobody calls `LendingPoolCollectBankFees` for this bank (it is permissionless but not automatically triggered).
3. At this point the `liquidity_vault` SPL balance equals exactly the sum of the three outstanding-fee fields (per the solvency invariant cited above), and `lending_pool_close_bank`'s checks (`CLOSE_ENABLED_FLAG`, zero positions, zero asset/liability shares, zero emissions) all pass.
4. Admin calls `LendingPoolCloseBank`; the `Bank` account is closed via `close = admin`.
5. Any subsequent attempt to call `LendingPoolCollectBankFees` for this bank key fails, because the `bank: AccountLoader<'info, Bank>` account no longer deserializes as `Bank`, and the vault PDA bumps required by the account constraints can no longer be sourced from it. The tokens remaining in the (now-orphaned) `liquidity_vault` token account are permanently unreachable by any program instruction.

### Citations

**File:** programs/marginfi/src/instructions/marginfi_group/close_bank.rs (L22-47)
```rust
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
```

**File:** programs/marginfi/src/instructions/marginfi_group/close_bank.rs (L58-63)
```rust
    #[account(
        mut,
        has_one = group @ MarginfiError::InvalidGroup,
        close = admin
    )]
    pub bank: AccountLoader<'info, Bank>,
```

**File:** trident-tests/fuzz_0/invariants/solvency.rs (L58-64)
```rust
    let outstanding_fees = from_wrapped(bank.collected_group_fees_outstanding.value)
        + from_wrapped(bank.collected_insurance_fees_outstanding.value)
        + from_wrapped(bank.collected_program_fees_outstanding.value);

    let vault_balance = I80F48::from_num(token_balance(trident, bank.liquidity_vault));
    let net_vault = vault_balance - outstanding_fees;
    let net_book = total_deposits - total_liabilities;
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

**File:** programs/marginfi/src/instructions/marginfi_group/collect_bank_fees.rs (L21-24)
```rust
pub fn lending_pool_collect_bank_fees<'info>(
    mut ctx: Context<'info, LendingPoolCollectBankFees<'info>>,
) -> MarginfiResult {
    let mut bank = ctx.accounts.bank.load_mut()?;
```

**File:** programs/marginfi/src/instructions/marginfi_group/collect_bank_fees.rs (L196-234)
```rust
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
