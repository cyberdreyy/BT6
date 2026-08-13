### Title
Depositors can dodge socialized bad-debt loss by withdrawing before `lending_pool_handle_bankruptcy` and re-depositing afterward, shifting the loss to slower depositors - (File: `programs/marginfi/src/state/bank.rs`, `programs/marginfi/src/instructions/marginfi_group/handle_bankruptcy.rs`)

### Summary
`lending_pool_handle_bankruptcy` socializes any bad debt not covered by the insurance fund by proportionally reducing `asset_share_value` for *all remaining* depositors of the affected bank via `Bank::socialize_loss`. There is no cooldown, queue, or snapshot mechanism protecting deposits/withdrawals from this event, so any depositor who becomes aware that a bad-debt position exists (this is public on-chain state, visible well before the settling transaction lands) can withdraw their full position beforehand to avoid absorbing any of the loss, then redeposit right after the loss has been socialized to buy back in at the now-discounted `asset_share_value`. This is the same MEV/loss-avoidance pattern as the Frankencoin `notifyLoss` finding: the loss-realizing instruction (`notifyLoss`/`MintingHub::end` there, `lending_pool_handle_bankruptcy` here) is not protected against being front-run by an exiting-then-re-entering depositor, and the loss is instead redistributed onto whoever remains in the pool.

### Finding Description
`Bank::socialize_loss` computes a new `asset_share_value` by subtracting the uncovered loss from the *total value of all currently outstanding shares* and dividing by the *current* `total_asset_shares`: [1](#0-0) 

Because the denominator is `total_asset_shares` **at the moment `handle_bankruptcy` executes**, any depositor who exits before that instruction runs removes their shares from the denominator, causing the same absolute loss amount to be divided among fewer remaining shares — i.e., a larger drop in `asset_share_value` for whoever stays. The withdrawing depositor pays nothing, and can then redeposit after the socialization at the discounted `asset_share_value`, effectively buying shares "on sale" without ever having exposed themselves to the loss.

The relevant instruction (`lending_pool_handle_bankruptcy`) is a simple, publicly observable state transition: [2](#0-1) 
and is even explicitly permissionless when `PERMISSIONLESS_BAD_DEBT_SETTLEMENT_FLAG` is set on the bank: [3](#0-2) 

The precondition for bankruptcy (a user with `liabilities > assets` and `assets < BANKRUPT_THRESHOLD`) is itself readable on-chain ahead of time via `check_account_bankrupt`: [4](#0-3) 

The project's own documentation confirms the mechanics: "the remainder will be covered by taking liquidity out of the bank, reducing the asset share value. This socializes the loss to all remaining depositors." [5](#0-4) 

Unlike Frankencoin, marginfi deposits/withdrawals have **no cooldown period at all** for standard lending positions — deposits and withdrawals are effectively instantaneous (subject only to health checks and liquidity in the vault), which makes the "redeem → let loss land → redeposit" pattern easier to execute than in the original report, not harder. There is no withdrawal queue, no delayed settlement, and no mechanism (e.g., pro-rata haircut applied at withdrawal time based on pending bad debt) that would prevent an informed/monitoring depositor from exiting ahead of a `handle_bankruptcy` call and returning after.

### Impact Explanation
Depositors who monitor bank/account health (which is fully public — bank state, oracle prices, and account balances are all on-chain) can systematically avoid ever bearing socialized bad debt while capturing the post-loss discounted share price on re-entry. This transfers value away from depositors who do not react in time (typically retail/passive LPs) to the depositor(s) executing the redeem+redeposit strategy, with a directly quantifiable financial effect equal to their pro-rata share of the avoided loss. This matches the "exploitable misvaluation / value redirection" bar: it degrades trust in the socialized-loss mechanism and disproportionately harms passive depositors, though it requires a pre-existing bad-debt/bankruptcy event (a relatively rare condition per the project's own bankruptcy guide) to be triggerable.

### Likelihood Explanation
Likelihood is limited by the fact that a bank-level bad-debt/bankruptcy condition must already exist, which the project describes as rare (triggered by liquidator downtime, illiquid assets, or a sharp price move) and never yet triggered on the main pool as of the docs. However, once such a condition exists, exploitation is straightforward and permissionless: it requires no special access, no admin privileges, and works with ordinary deposit/withdraw instructions, and is easier on marginfi than on the original report's target because there is no cooldown lockup at all.

### Recommendation
- Snapshot pending bad debt / socialize the loss atomically with the liquidation event itself (or immediately, auto-coupled), rather than leaving a window where the bankrupt balance is visible but unsettled and other depositors can react to it (the fuzz harness note about "auto-coupled" bankruptcy on liquidation success is a step in this direction, but any latency-inducing window, e.g. insurance top-up via `collect_bank_fees` before running `handle_bankruptcy`, reopens the exposure).
- Consider applying the socialize_loss haircut retroactively to withdrawals made during the bad-debt window (e.g., track an outstanding-bad-debt flag on the bank and apply a pro-rata haircut on withdrawal once a bank has unresolved bad debt), or introduce a short withdrawal delay/queue for banks with an active unresolved bankruptcy condition.
- Alternatively, amortize socialized losses over a period (similar to the report's "amortize the loss" mitigation) rather than applying them in a single atomic state transition, reducing the incentive/benefit of a single-block exit.

### Proof of Concept
1. Bank `B` has depositors A (attacker) and V (victim/other LPs), each with shares worth $X.
2. A borrower position in `B`'s debt token becomes bankrupt (liabilities > assets, per `check_account_bankrupt` at `programs/marginfi/src/state/marginfi_account.rs:964-996`), and this state is publicly visible on-chain before `lending_pool_handle_bankruptcy` is submitted.
3. Attacker A withdraws their full deposit from bank `B` immediately (standard `lending_account_withdraw`/`withdraw_all`, no cooldown), removing their shares from `total_asset_shares`.
4. Anyone (or the admin, or a permissionless caller if `PERMISSIONLESS_BAD_DEBT_SETTLEMENT_FLAG` is set) calls `lending_pool_handle_bankruptcy`, which invokes `Bank::socialize_loss` at `programs/marginfi/src/state/bank.rs:858-878`. Because A's shares are no longer in `total_asset_shares`, the same `loss_amount` is spread over fewer shares, and `asset_share_value` drops more than it would have with A still in the pool — V absorbs a larger-than-fair-share haircut.
5. Attacker A redeposits the same $X into bank `B` after the socialization, now receiving more shares than before (since `asset_share_value` is lower), capturing future yield/upside on shares effectively subsidized by V's absorbed loss.

This flow is buildable directly from existing test scaffolding, e.g. `programs/marginfi/tests/admin_actions/bankruptcy.rs` (bankruptcy setup/execution) combined with ordinary `try_bank_deposit`/`try_bank_withdraw` calls timed around the `try_handle_bankruptcy` call, as already exercised (without the withdraw/redeposit step) in [6](#0-5) .

### Citations

**File:** programs/marginfi/src/state/bank.rs (L858-878)
```rust
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
```

**File:** programs/marginfi/src/instructions/marginfi_group/handle_bankruptcy.rs (L41-70)
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
```

**File:** programs/marginfi/src/state/marginfi_account.rs (L964-996)
```rust
/// Check bankruptcy condition with heap reuse optimization.
///
/// Uses heap reuse to process positions one at a time.
pub fn check_account_bankrupt<'info>(
    marginfi_account: &MarginfiAccount,
    remaining_ais: &'info [AccountInfo<'info>],
    health_cache: &mut Option<&mut HealthCache>,
) -> MarginfiResult {
    // TODO remove this check here and raise it to the top-level instruction
    check!(
        !marginfi_account.get_flag(ACCOUNT_IN_FLASHLOAN),
        MarginfiError::AccountInFlashloan
    );

    let (equity_assets, equity_liabs) = get_health_components(
        marginfi_account,
        remaining_ais,
        RequirementType::Equity,
        health_cache,
        HealthPriceMode::Live { liq_cache: None },
    )?;

    let has_liabilities = equity_liabs > I80F48::ZERO;
    let below_bankruptcy_threshold = equity_assets < BANKRUPT_THRESHOLD;
    let liabilities_exceed_assets = equity_liabs > equity_assets;
    let is_bankrupt = has_liabilities && below_bankruptcy_threshold && liabilities_exceed_assets;

    if !is_bankrupt {
        return err!(MarginfiError::AccountNotBankrupt);
    }

    Ok(())
}
```

**File:** guides/RISK_AND_LIQUIDATORS/BANKRUPTCY.md (L40-46)
```markdown
* If bank's insurance fund > liabilities, then the insurance fund is used to repay the user's liability.
* If the bank's insurance fund is not sufficient, the remainder will be covered by taking liquidity
  out of the bank, reducing the asset share value. This socializes the loss to all remaining
  depositors.
* If the bank's insurance fund and liquidity are not sufficient (super-bankruptcy), the bank is
  killed. The asset share value is set to zero, wiping out all holdings for all other depositors.
  This state is irrecoverable, and the bank is permanently disabled.
```

**File:** programs/marginfi/tests/admin_actions/bankruptcy.rs (L746-813)
```rust
#[test_case(10_000., BankMint::Usdc, BankMint::Sol)]
#[test_case(10_000., BankMint::Sol, BankMint::Usdc)]
#[test_case(10_000., BankMint::PyUSD, BankMint::T22WithFee)]
#[test_case(10_000., BankMint::T22WithFee, BankMint::Sol)]
#[tokio::test]
async fn marginfi_group_handle_bankruptcy_success_not_insured(
    borrow_amount: f64,
    collateral_mint: BankMint,
    debt_mint: BankMint,
) -> anyhow::Result<()> {
    // -------------------------------------------------------------------------
    // Setup
    // -------------------------------------------------------------------------

    let mut test_f = TestFixture::new(Some(TestSettings::all_banks_payer_not_admin())).await;

    // LP

    let lp_deposit_amount = 2. * borrow_amount;
    let lp_wallet_balance = get_max_deposit_amount_pre_fee(lp_deposit_amount);
    let lp_mfi_account_f = test_f.create_marginfi_account().await;
    let lp_token_account_f_sol = test_f
        .get_bank(&debt_mint)
        .mint
        .create_token_account_and_mint_to(lp_wallet_balance)
        .await;
    lp_mfi_account_f
        .try_bank_deposit(
            lp_token_account_f_sol.key,
            test_f.get_bank(&debt_mint),
            lp_deposit_amount,
            None,
        )
        .await?;

    // User

    let mut user_mfi_account_f = test_f.create_marginfi_account().await;
    let sufficient_collateral_amount = test_f
        .get_sufficient_collateral_for_outflow(borrow_amount, &collateral_mint, &debt_mint)
        .await;
    let user_wallet_balance = get_max_deposit_amount_pre_fee(sufficient_collateral_amount);
    let user_collateral_token_account_f = test_f
        .get_bank_mut(&collateral_mint)
        .mint
        .create_token_account_and_mint_to(user_wallet_balance)
        .await;
    let user_debt_token_account_f = test_f
        .get_bank_mut(&debt_mint)
        .mint
        .create_empty_token_account()
        .await;
    user_mfi_account_f
        .try_bank_deposit(
            user_collateral_token_account_f.key,
            test_f.get_bank(&collateral_mint),
            sufficient_collateral_amount,
            None,
        )
        .await?;
    user_mfi_account_f
        .try_bank_borrow(
            user_debt_token_account_f.key,
            test_f.get_bank(&debt_mint),
            borrow_amount,
        )
        .await?;

```
