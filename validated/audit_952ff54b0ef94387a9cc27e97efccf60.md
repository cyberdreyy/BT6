[1](#0-0) , [2](#0-1) , [3](#0-2) , [4](#0-3)  confirm the analog exists.

### Title
Depositors take on unsocialized bad debt because `lending_pool_handle_bankruptcy` is a discrete, delayed step that lags behind the actual loss event, letting new deposits mint shares at a stale (pre-loss) `asset_share_value` - (File: `programs/marginfi/src/instructions/marginfi_group/handle_bankruptcy.rs`, `programs/marginfi/src/state/bank.rs`)

### Summary
marginfi's bank share pricing mirrors the root cause described in the Maple report: the price used to mint shares on deposit (`asset_share_value`) is computed from the bank's current on-chain state, but that state does not reflect economic losses (bad debt from an under-collateralized/bankrupt borrower) until an explicit, separate settlement transaction (`lending_pool_handle_bankruptcy`) is executed. Between the moment a user's position becomes bad debt (assets = 0, liabilities > 0, per the bankruptcy definition) and the moment `lending_pool_handle_bankruptcy` actually runs `socialize_loss`, any depositor into that bank mints shares at an inflated price that does not yet account for the pending loss. When bankruptcy is eventually settled, the loss is spread pro-rata over all current shareholders, including those who deposited during the lag window, causing them to immediately realize a loss they had no part in causing.

### Finding Description
`Bank::socialize_loss` is the only code path that reduces `asset_share_value` to account for bad debt; it is invoked exclusively from `lending_pool_handle_bankruptcy` [5](#0-4) . This instruction is a discrete, separately-triggered transaction (either by an admin, or, for banks with `PERMISSIONLESS_BAD_DEBT_SETTLEMENT_FLAG` set, by anyone) that runs only after a user has already been fully liquidated into bad debt: `check_account_bankrupt` requires the account to already have zero (or below-threshold) assets and positive liabilities before the instruction can proceed [6](#0-5) .

The project's own documentation confirms this two-step, delayed process: bankruptcy at the user level happens first (all assets consumed by liquidation, debt remains), and only later does an admin/permissionless caller run `handle_bankruptcy` to actually apply `socialize_loss` and reduce `asset_share_value` for all depositors [7](#0-6) . It even explicitly acknowledges that if the settlement step is delayed, remaining depositors are impacted: *"If Bankruptcy isn't executed on a bankrupt user, then remaining depositors can never withdraw the whole balance in the bank"* [8](#0-7) .

Critically, `lending_account_deposit` performs no check for outstanding, unsettled bad debt on the bank before minting new shares at the current `asset_share_value` [9](#0-8) . The only gating check is `validate_bank_state(&bank, InstructionKind::FailsIfPausedOrReduceState)`, which blocks deposits solely based on the bank's `operational_state` (paused/reduce-only) — not on whether the bank currently carries unresolved bad debt from a bankrupt user. This is exactly the Maple `Pool.sol` pattern: a share price used for deposits that has not yet been marked down for a loss that is already known/pending on-chain, while a later, separate step (here, `handle_bankruptcy`/`socialize_loss`; there, loss realization in `LM.removeLoanImpairment`) applies the markdown retroactively across all current shareholders.

### Impact Explanation
When `socialize_loss` eventually executes, it recomputes a new, lower `asset_share_value` from `(total_value - loss_amount) / total_asset_shares` and applies it uniformly to every existing share, including shares minted by deposits that occurred after the bad debt existed but before settlement [10](#0-9) . A depositor who enters during this window is diluted by a loss that predates their deposit, incurring an immediate, uncompensated loss proportional to their share of the pool at settlement time — the same "unrealized loss dumped on new depositors" impact described in the Maple report. In the extreme (super-bankruptcy) case, `asset_share_value` can even be zeroed out, wiping out the new depositor's principal entirely [11](#0-10) .

### Likelihood Explanation
The window is bounded by how promptly `handle_bankruptcy` is run relative to a liquidation leaving bad debt, and the project treats permissionless settlement (`PERMISSIONLESS_BAD_DEBT_SETTLEMENT_FLAG`) as the mitigation for keeper delay, but this does not close the gap between the liquidation event and the next successful settlement transaction; deposits remain unrestricted throughout. The project's own docs note bankruptcy has never actually fired in the main pool as of the doc's writing [12](#0-11) , indicating this is a rare-but-real event whose likelihood depends on market volatility and liquidator/keeper responsiveness, not an everyday occurrence — consistent with the Maple report's own "Medium" severity classification for the analogous condition.

### Recommendation
- Track any bank with a known-bankrupt account (assets ~0, liabilities > 0) as carrying pending unsocialized bad debt, and either block/flag new deposits into that bank via `validate_bank_state` until `handle_bankruptcy` runs, or expose this state prominently so depositors/front ends can avoid depositing during the window.
- Consider adding a minimum-shares-received parameter to `lending_account_deposit` (similar to the report's `expectedMinimumShares` recommendation) so a depositor's transaction reverts if a `handle_bankruptcy` call is processed in the same slot/ahead of their deposit and would otherwise silently dilute them.
- Alternatively, make `socialize_loss` accrue automatically (e.g., during `accrue_interest` or cache updates) as soon as a user is provably bankrupt, shrinking the lag window instead of relying on a separate, later-triggered transaction.

### Proof of Concept
1. User B has an active borrow position in Bank X and an asset position priced via an oracle.
2. A price move or accrued interest pushes B's account underwater; a liquidator fully liquidates B's collateral, leaving B with `assets ≈ 0` and `liabilities > 0` in Bank X — B is now bankrupt per `check_account_bankrupt`, but `Bank.asset_share_value` for Bank X is unchanged (no `socialize_loss` has run yet).
3. Before anyone calls `lending_pool_handle_bankruptcy` for B, User C calls `lending_account_deposit` into Bank X. `lending_account_deposit` only checks `validate_bank_state`/`validate_asset_tags`/`ACCOUNT_DISABLED`/`ACCOUNT_IN_RECEIVERSHIP` — none of which reference B's outstanding bad debt — so C's shares are minted at the current, not-yet-marked-down `asset_share_value` [13](#0-12) .
4. Later, an admin or (if `PERMISSIONLESS_BAD_DEBT_SETTLEMENT_FLAG` is set) any caller invokes `lending_pool_handle_bankruptcy` for B against Bank X. `socialize_loss` recomputes `asset_share_value = (total_value - loss_amount) / total_asset_shares` over the bank's current `total_asset_shares`, which now includes C's newly minted shares [14](#0-13) , [15](#0-14) .
5. C, who deposited after B's debt was already unrecoverable but before settlement, is diluted along with pre-existing depositors, realizing a loss on funds deposited with no participation in causing B's bad debt.

### Citations

**File:** programs/marginfi/src/instructions/marginfi_account/deposit.rs (L33-92)
```rust
pub fn lending_account_deposit<'info>(
    mut ctx: Context<'info, LendingAccountDeposit<'info>>,
    amount: u64,
    deposit_up_to_limit: Option<bool>,
) -> MarginfiResult {
    let LendingAccountDeposit {
        marginfi_account: marginfi_account_loader,
        authority: signer,
        signer_token_account,
        liquidity_vault: bank_liquidity_vault,
        token_program,
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
    let deposit_up_to_limit = deposit_up_to_limit.unwrap_or(false);

    let mut bank = bank_loader.load_mut()?;
    let mut marginfi_account = marginfi_account_loader.load_mut()?;
    let group = marginfi_group_loader.load()?;
    validate_asset_tags(&bank, &marginfi_account)?;
    validate_bank_state(&bank, InstructionKind::FailsIfPausedOrReduceState)?;

    check!(
        !marginfi_account.get_flag(ACCOUNT_DISABLED)
            // Sanity check: liquidation doesn't allow the deposit ix, but just in case
            && !marginfi_account.get_flag(ACCOUNT_IN_RECEIVERSHIP),
        MarginfiError::AccountDisabled
    );

    bank.accrue_interest(
        clock.unix_timestamp,
        &group,
        #[cfg(not(feature = "client"))]
        bank_loader.key(),
    )?;

    let deposit_amount = if deposit_up_to_limit {
        amount.min(bank.get_remaining_deposit_capacity()?)
    } else {
        amount
    };

    if deposit_amount == 0 {
        return Ok(());
    }

    let mut bank_account = BankAccountWrapper::find_or_create(
        &bank_loader.key(),
        &mut bank,
        &mut marginfi_account.lending_account,
    )?;

    let share_amount = bank_account.deposit(I80F48::from_num(deposit_amount))?;
```

**File:** programs/marginfi/src/state/bank.rs (L852-886)
```rust
    /// Socialize a loss of `loss_amount` among depositors, the `total_deposit_shares` stays the
    /// same, but total value of deposits is reduced by `loss_amount`;
    ///
    /// In cases where assets < liabilities, the asset share value will be set to zero, but cannot
    /// go negative. Effectively, depositors forfeit their entire deposit AND all earned interest in
    /// this case.
    fn socialize_loss(&mut self, loss_amount: I80F48) -> MarginfiResult<bool> {
        let mut kill_bank = false;
        let total_asset_shares: I80F48 = self.total_asset_shares.into();
        let old_asset_share_value: I80F48 = self.asset_share_value.into();

        // Compute total "old" value of shares
        let total_value: I80F48 = total_asset_shares
            .checked_mul(old_asset_share_value)
            .ok_or_else(math_error!())?;

        // Subtract loss, clamping at zero (i.e. assets < liabilities, the bank is wiped out)
        if total_value <= loss_amount {
            self.asset_share_value = I80F48::ZERO.into();
            // This state is irrecoverable, the bank is dead.
            kill_bank = true;
        } else {
            // otherwise subtract then redistribute
            let new_share_value: I80F48 = (total_value - loss_amount)
                .checked_div(total_asset_shares)
                .ok_or_else(math_error!())?;
            self.asset_share_value = new_share_value.into();
            // Sanity check: should be unreachable.
            if new_share_value == I80F48::ZERO {
                kill_bank = true;
            }
        }

        Ok(kill_bank)
    }
```

**File:** programs/marginfi/src/instructions/marginfi_group/handle_bankruptcy.rs (L41-148)
```rust
pub fn lending_pool_handle_bankruptcy<'info>(
    mut ctx: Context<'info, LendingPoolHandleBankruptcy<'info>>,
) -> MarginfiResult {
    let LendingPoolHandleBankruptcy {
        marginfi_account: marginfi_account_loader,
        insurance_vault,
        token_program,
        bank: bank_loader,
        group: marginfi_group_loader,
        ..
    } = ctx.accounts;
    let maybe_bank_mint = {
        let bank = bank_loader.load()?;
        let group = marginfi_group_loader.load()?;
        let signer = ctx.accounts.signer.key();
        let is_admin_or_risk_admin = signer == group.risk_admin || signer == group.admin;
        let permissionless_bad_debt_settlement =
            bank.get_flag(PERMISSIONLESS_BAD_DEBT_SETTLEMENT_FLAG);

        if permissionless_bad_debt_settlement {
            // if permissionless, users can bankrupt reduce-only or operational banks
            validate_bank_state(&bank, InstructionKind::FailsInPausedState)?;
        } else {
            // admin can bankrupt banks in any state
            validate_bank_state(&bank, InstructionKind::Unrestricted)?;
            check!(is_admin_or_risk_admin, MarginfiError::Unauthorized);
        }

        utils::maybe_take_bank_mint(&mut ctx.remaining_accounts, &bank, token_program.key)?
    };

    let clock = Clock::get()?;

    let mut marginfi_account = marginfi_account_loader.load_mut()?;

    let mut health_cache = HealthCache::zeroed();
    health_cache.timestamp = clock.unix_timestamp;
    health_cache.program_version = PROGRAM_VERSION;

    check_account_bankrupt(
        &marginfi_account,
        ctx.remaining_accounts,
        &mut Some(&mut health_cache),
    )?;

    let bank = bank_loader.load()?;
    let cached_price = fetch_unbiased_price_for_bank_cache(
        &bank_loader.key(),
        &bank,
        &clock,
        ctx.remaining_accounts,
    )
    .ok();
    drop(bank);

    health_cache.set_engine_ok(true);
    marginfi_account.health_cache = health_cache;

    let mut bank = bank_loader.load_mut()?;
    let group = &marginfi_group_loader.load()?;

    bank.accrue_interest(
        clock.unix_timestamp,
        group,
        #[cfg(not(feature = "client"))]
        bank_loader.key(),
    )?;

    let lending_account_balance = marginfi_account
        .lending_account
        .balances
        .iter_mut()
        .find(|balance| balance.is_active() && balance.bank_pk == bank_loader.key());

    check!(
        lending_account_balance.is_some(),
        MarginfiError::LendingAccountBalanceNotFound
    );

    let lending_account_balance = lending_account_balance.unwrap();

    let bad_debt: I80F48 =
        bank.get_liability_amount(lending_account_balance.liability_shares.into())?;

    check!(
        bad_debt > ZERO_AMOUNT_THRESHOLD,
        MarginfiError::BalanceNotBadDebt
    );

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

**File:** programs/marginfi/src/instructions/marginfi_group/handle_bankruptcy.rs (L189-199)
```rust
    // Socialize bad debt among depositors.
    let kill_bank = bank.socialize_loss(socialized_loss)?;

    // Settle bad debt.
    // The liabilities of this account and global total liabilities are reduced by `bad_debt`
    BankAccountWrapper::find(
        &bank_loader.key(),
        &mut bank,
        &mut marginfi_account.lending_account,
    )?
    .repay(bad_debt)?;
```

**File:** guides/RISK_AND_LIQUIDATORS/BANKRUPTCY.md (L1-46)
```markdown
# Bankruptcy Guide

Want to learn more about how bankruptcy works on mrgn? Read on.

## Key Terms and Kinds of Bankruptcy

* User bankruptcy - When a user's liabilities exceed their assets, before accounting for weights,
  they are eligible to be bankrupt. A user is technically bankrupt after all their remaining assets
  are liquidated, at which point they will have 0 assets and a non-zero amount of liabilities. All
  bankruptcies are technically triggered when USERS go bankrupt. Some corollaries and notable facts:
    * Any time a bank is bankrupt, at least one lender in that bank is bankrupt.
    * When taking an eligible user into bankruptcy, if a user has several different liabilities,
      liquidators get to pick which ones will stay on their books, which means they get to pick
      which banks will absorb the bad debt.
* Bank bankruptcy - Banks where at least one user has bad debt in the manner described above are in
  a light state of bankruptcy. 
* Bank super-bankruptcy - Banks where the amount of bad debt outstanding in the manner described
  above exceeds the bank's assets are in a state of super-bankruptcy.


## How Does it Happen?

Bankruptcy is rare. It can only occur in one of the following circumstances: (1) liquidators haven't
been running properly, e.g. due to congestion, etc, (2) an asset is too illiquid for liquidators to
safely clear, (3) an asset's price changes drastically, before liquidators are able to respond. 

## Discharging a Bankruptcy

First, liquidators consume all the remaining assets that the user has. If the user has A dollars in
assets and B dollars in liabilities (in equity value, i.e. excluding any weights), we know that B >
A. After liquidation is complete, A_new = 0, and B_new = B - A + X, where X is the liquidation
premium and insurance.

Run `collect_bank_fees` before beginning the next step so the insurance fund is fully capitalized.

Next, the group administrator runs `handle_bankruptcy` on the user. For banks where
`PERMISSIONLESS_BAD_DEBT_SETTLEMENT_FLAG` is enabled, anyone can do this. This will perform the
following logic:

* If bank's insurance fund > liabilities, then the insurance fund is used to repay the user's liability.
* If the bank's insurance fund is not sufficient, the remainder will be covered by taking liquidity
  out of the bank, reducing the asset share value. This socializes the loss to all remaining
  depositors.
* If the bank's insurance fund and liquidity are not sufficient (super-bankruptcy), the bank is
  killed. The asset share value is set to zero, wiping out all holdings for all other depositors.
  This state is irrecoverable, and the bank is permanently disabled.
```

**File:** guides/RISK_AND_LIQUIDATORS/BANKRUPTCY.md (L50-54)
```markdown
### What Happens if it Doesn't Run?

If Bankruptcy isn't executed on a bankrupt user, then remaining depositors can never withdraw the
whole balance in the bank. The last few depositors who try to withdraw will find there are not
enough funds - proportional to the liabilities held by bankrupt users.
```

**File:** guides/RISK_AND_LIQUIDATORS/BANKRUPTCY.md (L56-58)
```markdown
### When Does This Matter?

Ideally, never. As of November 2025, bankruptcy has never been executed in the main pool.
```
