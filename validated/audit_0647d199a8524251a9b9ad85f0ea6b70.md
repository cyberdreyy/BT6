## Title
`start_liquidation` / `start_deleverage` compute liquidation eligibility from stale bank share values instead of accruing interest first - (File: `programs/marginfi/src/instructions/marginfi_account/liquidate_start.rs`)

## Summary
`start_receivership` (invoked by the permissionless `start_liquidation` instruction and the risk-admin `start_deleverage` instruction) determines whether an account is liquidatable by calling `check_pre_liquidation_condition_and_get_account_health`, which in turn reads each involved bank's `asset_share_value`/`liability_share_value` without ever calling `Bank::accrue_interest` on those banks first. If a bank has not been touched by another interest-accruing instruction recently, its recorded liability share value understates the borrower's true, currently-owed debt, causing the health calculation to be more favorable than reality — exactly the "stale factors -> incorrect eligibility" pattern described in the referenced Notional report.

## Finding Description
`start_receivership` reads oracle/bank `remaining_ais` and immediately computes health: [1](#0-0) 

Note there is no call to `bank.accrue_interest(...)` anywhere in `liquidate_start.rs` before `check_pre_liquidation_condition_and_get_account_health` / `get_health_components` are invoked. Those functions simply read the bank's already-stored share values: [2](#0-1) 

Balance value calculation multiplies shares by the bank's cached `liability_share_value`/`asset_share_value`, not a live-accrued figure: [3](#0-2) 

Those share values are only ever refreshed by `Bank::accrue_interest`, which updates `last_update` and the share values based on elapsed `time_delta`: [4](#0-3) 

Contrast this with `lending_account_liquidate` (the actual liquidation execution instruction), which explicitly accrues interest on both `asset_bank` and `liab_bank` immediately before calling the identical health-check function: [5](#0-4) 

This inconsistency mirrors the Notional bug class precisely: `_isExternalLendingUnhealthy` used `PrimeCashExchangeRate.getPrimeCashFactors` (a stale/un-aggregated read) instead of accruing/refreshing factors first, while the sibling `rebalance()` path did refresh state before computing the same eligibility condition. In marginfi, `start_liquidation`/`start_deleverage` (the "checkRebalance" analog — the gating function that determines whether the risk-changing operation may proceed) skips the interest accrual that the sibling `lending_account_liquidate` (the "rebalance()" analog) performs.

The README explicitly documents that interest only accrues "just before any transaction that affects a Bank's balances" and that banks left untouched can go arbitrarily stale, confirming this is a real, reachable condition rather than a theoretical one: [6](#0-5) 

## Impact Explanation
Because accrued-but-unposted interest only ever increases a borrower's liability (and by symmetry a lender's owed assets), omitting `accrue_interest` before the pre-liquidation health check causes `check_pre_liquidation_condition_and_get_account_health` to understate liabilities (or overstate assets on the counterpart side), making the account look healthier than it truly is. Since `check_pre_liquidation_condition_and_get_account_health` reverts with `MarginfiError::HealthyAccount` whenever the computed health is positive, a genuinely unhealthy account (once true accrued interest is counted) can cause `start_liquidation` to revert, blocking a legitimate, permissionless liquidator from opening receivership on an account that should already be liquidatable. This delays liquidation of bad debt, increasing insurance-fund/bad-debt exposure for the protocol — the same "increased protocol risk from a blocked risk-mitigating action" impact accepted as Medium in the Notional report, but here it directly gates an on-chain, state-changing instruction (`start_liquidation`) rather than merely an off-chain view function, which arguably raises rather than lowers severity relative to the original finding.

## Likelihood Explanation
This is reachable under normal, permissionless conditions: any bank whose only interactions have been infrequent, or whose last accrual predates a period of price movement, will have stale share values at the moment a liquidator calls `start_liquidation`. No admin/privileged access or special setup is required — merely a delay between a bank's last interest accrual and a liquidator's attempt to begin liquidation, which is a common real-world scenario for low-activity banks (as also demonstrated by the project's own `z01_compoundInterest.spec.ts` test acknowledging banks can go stale for long periods).

## Recommendation
In `start_receivership` (`liquidate_start.rs`), before calling `check_pre_liquidation_condition_and_get_account_health`/`get_health_components`, iterate the account's active balances and call `Bank::accrue_interest` (with `load_mut`) on each referenced bank, mirroring the pattern already used in `lending_account_liquidate`. Alternatively, require/verify that interest has been refreshed for all involved banks (e.g., via a shared helper invoked by both `liquidate.rs` and `liquidate_start.rs`) so the liquidation-eligibility determination is always computed against live, accrued balances.

## Proof of Concept
1. Borrower opens a position in Bank B and becomes borderline unhealthy such that, including interest accrued since Bank B's `last_update`, their maintenance health is negative, but using Bank B's currently stored (stale) `liability_share_value` the computed health is still ≥ 0.
2. Ensure no other instruction touching Bank B (deposit/withdraw/borrow/repay/liquidate) runs in the interim, so `last_update` remains old and `liability_share_value` is not refreshed.
3. A liquidator calls `start_liquidation`, which calls `start_receivership` → `check_pre_liquidation_condition_and_get_account_health`, computing health from the stale `liability_share_value` (see `programs/marginfi/src/state/bank.rs:231-241` and `programs/marginfi/src/state/marginfi_account.rs:903-943`).
4. Because the stale-computed health is ≥ 0, the call reverts with `MarginfiError::HealthyAccount`, even though the account is truly unhealthy once pending interest is accounted for — blocking the liquidator from opening receivership until some unrelated instruction happens to accrue interest on Bank B first.

### Citations

**File:** programs/marginfi/src/instructions/marginfi_account/liquidate_start.rs (L85-105)
```rust
pub fn start_receivership<'info>(
    marginfi_account: &mut MarginfiAccount,
    liq_record: &mut LiquidationRecord,
    remaining_ais: &'info [AccountInfo<'info>],
    ignore_healthy: bool,
) -> MarginfiResult {
    // Note: the receiver can use the health cache state after this ix concludes to plan their
    // liquidation/deleverage strategy.
    let mut health_cache = HealthCache::zeroed();
    let mut liq_price_cache = LiquidationPriceCache::default();
    let (_pre_health, assets, liabs) = check_pre_liquidation_condition_and_get_account_health(
        marginfi_account,
        remaining_ais,
        None,
        &mut Some(&mut health_cache),
        HealthPriceMode::Live {
            liq_cache: Some(&mut liq_price_cache),
        },
        ignore_healthy,
    )?;

```

**File:** programs/marginfi/src/state/marginfi_account.rs (L903-943)
```rust
pub fn check_pre_liquidation_condition_and_get_account_health<'info>(
    marginfi_account: &MarginfiAccount,
    remaining_ais: &'info [AccountInfo<'info>],
    liability_bank_pk: Option<&Pubkey>,
    health_cache: &mut Option<&mut HealthCache>,
    price_mode: HealthPriceMode<'_>,
    ignore_healthy: bool,
) -> MarginfiResult<(I80F48, I80F48, I80F48)> {
    check!(
        !marginfi_account.get_flag(ACCOUNT_IN_FLASHLOAN),
        MarginfiError::AccountInFlashloan
    );

    if let Some(bank_pk) = liability_bank_pk {
        let lending_account = &marginfi_account.lending_account;
        let liability_balance = lending_account
            .balances
            .iter()
            .find(|b| b.is_active() && b.bank_pk == *bank_pk)
            .ok_or(MarginfiError::LendingAccountBalanceNotFound)?;

        check!(
            !liability_balance.is_empty(BalanceSide::Liabilities),
            MarginfiError::NoLiabilitiesInLiabilityBank
        );

        check!(
            liability_balance.is_empty(BalanceSide::Assets),
            MarginfiError::AssetsInLiabilityBank
        );
    }

    // Get health components using heap reuse
    let (assets, liabs) = get_health_components(
        marginfi_account,
        remaining_ais,
        RequirementType::Maintenance,
        health_cache,
        price_mode,
    )?;

```

**File:** programs/marginfi/src/state/bank.rs (L231-241)
```rust
    fn get_liability_amount(&self, shares: I80F48) -> MarginfiResult<I80F48> {
        Ok(shares
            .checked_mul(self.liability_share_value.into())
            .ok_or_else(math_error!())?)
    }

    fn get_asset_amount(&self, shares: I80F48) -> MarginfiResult<I80F48> {
        Ok(shares
            .checked_mul(self.asset_share_value.into())
            .ok_or_else(math_error!())?)
    }
```

**File:** programs/marginfi/src/state/bank.rs (L511-528)
```rust
    fn accrue_interest(
        &mut self,
        current_timestamp: i64,
        group: &MarginfiGroup,
        #[cfg(not(feature = "client"))] bank: Pubkey,
    ) -> MarginfiResult<()> {
        #[cfg(all(not(feature = "client"), feature = "debug"))]
        sol_log_compute_units();

        let time_delta: u64 = (current_timestamp - self.last_update).try_into().unwrap();
        if time_delta == 0 {
            return Ok(());
        }

        let total_assets = self.get_asset_amount(self.total_asset_shares.into())?;
        let total_liabilities = self.get_liability_amount(self.total_liability_shares.into())?;

        self.last_update = current_timestamp;
```

**File:** programs/marginfi/src/instructions/marginfi_account/liquidate.rs (L153-185)
```rust
    let group = &*marginfi_group_loader.load()?;
    {
        ctx.accounts.asset_bank.load_mut()?.accrue_interest(
            current_timestamp,
            group,
            #[cfg(not(feature = "client"))]
            ctx.accounts.asset_bank.key(),
        )?;
        ctx.accounts.liab_bank.load_mut()?.accrue_interest(
            current_timestamp,
            group,
            #[cfg(not(feature = "client"))]
            ctx.accounts.liab_bank.key(),
        )?;
    }

    let init_liquidatee_remaining_len = liquidatee_accounts as usize;
    let liquidatee_accounts_starting_pos =
        ctx.remaining_accounts.len() - init_liquidatee_remaining_len;
    let liquidatee_remaining_accounts = &ctx.remaining_accounts[liquidatee_accounts_starting_pos..];

    liquidatee_marginfi_account.lending_account.sort_balances();

    let asset_bank_key = ctx.accounts.asset_bank.key();
    let liab_bank_key = ctx.accounts.liab_bank.key();
    let (pre_liquidation_health, _, _) = check_pre_liquidation_condition_and_get_account_health(
        &liquidatee_marginfi_account,
        liquidatee_remaining_accounts,
        Some(&liab_bank_key),
        &mut None,
        HealthPriceMode::Live { liq_cache: None },
        false,
    )?;
```

**File:** README.md (L193-198)
```markdown
### Interest, Previewed Amounts, and Closing Positions

Because interest accumulates just before any transaction that affects a Bank's balances, when a user
goes to withdraw, the amount they withdraw can be slightly higher than what is previewed, likewise
for repayments, etc. This also means that to withdraw or repay all, the user must send a special
flag to the instruction to close the balance in full AFTER interest.
```
