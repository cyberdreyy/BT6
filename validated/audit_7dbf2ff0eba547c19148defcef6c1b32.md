## Analysis

The `lending_pool_emissions_deposit` instruction in marginfi is the direct on-chain analog of Balancer's `reinvestReward`: both are **permissionless functions that inject an external value gain directly into the pool's share-value accounting**, instantly and proportionally benefiting *whoever holds shares at that exact moment* — with no fee, no cooldown, and no snapshot of deposit duration. [1](#0-0) 

### Title
Emissions Value Injected via `lending_pool_emissions_deposit` Can Be Front-Run and Captured by a Same-Transaction Depositor - (File: `programs/marginfi/src/instructions/marginfi_group/configure_bank.rs`)

### Summary
`lending_pool_emissions_deposit` transfers tokens straight into a bank's `liquidity_vault` and recomputes `asset_share_value` by dividing `total_assets_after` by the *unchanged* `total_asset_shares`, instantly raising the value of every existing share: [2](#0-1) 

Because (a) this instruction is permissionless, (b) `lending_account_deposit`/`lending_account_withdraw` have zero fees and no minimum holding period, and (c) there is no snapshot mechanism weighting the emissions boost by deposit duration, an attacker can deposit a large amount into the bank immediately before an emissions deposit lands, absorb a disproportionate share of the value increase, and withdraw everything in the same transaction — diluting legitimate long-term depositors exactly as in Sherlock M-7.

### Finding Description
The root causes mirror M-7 precisely:

1. **No deposit/withdraw fee** — confirmed in the protocol's own documentation: "There are never any fees to deposit into p0" / "There are never any fees to withdraw from p0." [3](#0-2) 

2. **No same-block/same-tx restriction** — `lending_account_deposit` and `lending_account_withdraw` contain no cooldown, block-delay, or lock-up check; a user can call both in the same atomic transaction. [4](#0-3) [5](#0-4) 

3. **Permissionless value-injection instruction** — `lending_pool_emissions_deposit` can be triggered by any signer supplying `emissions_funding_account` tokens, and it uniformly re-prices all existing shares the instant it executes. [6](#0-5) 

4. **No snapshotting / time-weighting** — shares minted seconds before the emissions boost are treated identically to shares held since inception; `total_asset_shares` is unchanged by the emissions deposit, so the multiplier `(total_assets_after / total_asset_shares)` applies flat across all holders regardless of when they joined. [7](#0-6) 

This is functionally identical to the Balancer `_convertBPTClaimToStrategyTokens`/`_convertStrategyTokensToBPTClaim` mechanism abused in M-7: a large, freshly-minted share position captures a share of value added to the pool that should have accrued mostly to pre-existing, long-term depositors.

### Impact Explanation
Any account (or admin) that funds `lending_pool_emissions_deposit` with real value intended to reward depositors of a bank has that value redirected: a sandwiching attacker who deposits immediately beforehand and withdraws immediately afterward captures a share of the injected emissions proportional to their (temporary, flash-sized) share of `total_asset_shares` at the moment of the call, at the expense of genuine long-term depositors. This is a durable value-redirection issue with direct financial effect on legitimate depositors of any bank where `lending_pool_emissions_deposit` is used.

### Likelihood Explanation
The attack requires only:
- Sufficient capital (or a flash loan, since marginfi explicitly charges zero flashloan fees per `guides/USER/FEES.md`) to temporarily dominate `total_asset_shares` for the target bank,
- Knowledge/observation (mempool or predictable admin cadence) of an impending `lending_pool_emissions_deposit` call,
- No borrowing needed (so no origination fee, no health-check friction), and no minimum holding period to defeat.

Given marginfi explicitly documents zero deposit, withdraw, and flashloan fees, the economic barrier that Notional's team ultimately relied on to mitigate the analogous Balancer bug (fees + minimum holding period + minimum leverage requirements) is absent here, making the attack straightforwardly profitable whenever a bank has thin existing liquidity relative to available flash capital.

### Recommendation
- Restrict `lending_pool_emissions_deposit` (or at least its economic effect) so that it cannot be sandwiched: e.g., require a minimum holding period before newly deposited shares are eligible for the emissions-driven `asset_share_value` increase, or snapshot `total_asset_shares` at a fixed point (e.g. prior block) before applying the emissions boost.
- Consider disallowing deposit-then-withdraw (or withdraw-then-deposit) of the same bank within the same transaction/slot for banks that receive periodic emissions deposits.
- Alternatively, restrict who can call `lending_pool_emissions_deposit` (e.g. `emissions_admin`-gated) and have that admin submit it as a private transaction, and/or impose a small deposit/withdraw fee on banks using emissions to make the sandwich unprofitable in typical liquidity regimes.

### Proof of Concept
1. Bank X has `total_asset_shares` S with `asset_share_value` V (total assets = S·V), held entirely by long-term depositor Alice.
2. Attacker observes (or triggers, since the depositor role is unrestricted) an imminent `lending_pool_emissions_deposit(amount)` call funding Bank X's `liquidity_vault` with `amount` E of real value.
3. In the same transaction, attacker calls `lending_account_deposit` with a very large `D` (via flash loan, zero fee) into Bank X just before the emissions instruction executes, minting `D/V` new shares — see `bank_account.deposit()` in `programs/marginfi/src/instructions/marginfi_account/deposit.rs:92`.
4. The `lending_pool_emissions_deposit` instruction executes, updating `bank.asset_share_value = (total_assets + E) / total_asset_shares` — see `configure_bank.rs:138-146` — uniformly boosting every existing share, including the attacker's freshly minted ones.
5. Still within the same transaction, attacker calls `lending_account_withdraw(amount, withdraw_all=true)` (`programs/marginfi/src/instructions/marginfi_account/withdraw.rs:112-131`), redeeming shares at the new, boosted `asset_share_value` — with zero withdrawal fee per `guides/USER/FEES.md`.
6. Attacker repays the flash loan, retaining a disproportionate cut of `E` that should have accrued to Alice, who held her position through the entire emissions cycle.

### Citations

**File:** programs/marginfi/src/instructions/marginfi_group/configure_bank.rs (L84-156)
```rust
/// Permissionlessly deposit same-mint emissions directly into the bank liquidity vault,
/// increasing depositor value through asset share value.
pub fn lending_pool_emissions_deposit(
    ctx: Context<LendingPoolEmissionsDeposit>,
    amount: u64,
) -> MarginfiResult {
    if amount == 0 {
        return Ok(());
    }

    let clock = Clock::get()?;
    let mut bank = ctx.accounts.bank.load_mut()?;
    let group = ctx.accounts.group.load()?;

    utils::validate_bank_state(&bank, utils::InstructionKind::FailsIfPausedOrReduceState)?;

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

    let total_asset_shares = I80F48::from(bank.total_asset_shares);
    check!(
        total_asset_shares > I80F48::ZERO,
        MarginfiError::EmissionsUpdateError
    );

    bank.accrue_interest(
        clock.unix_timestamp,
        &group,
        #[cfg(not(feature = "client"))]
        ctx.accounts.bank.key(),
    )?;

    transfer_checked(
        CpiContext::new(
            ctx.accounts.token_program.key(),
            TransferChecked {
                from: ctx.accounts.emissions_funding_account.to_account_info(),
                to: ctx.accounts.liquidity_vault.to_account_info(),
                authority: ctx.accounts.depositor.to_account_info(),
                mint: ctx.accounts.mint.to_account_info(),
            },
        ),
        amount,
        ctx.accounts.mint.decimals,
    )?;

    let total_assets = bank.get_asset_amount(total_asset_shares)?;
    let updated_total_assets = total_assets
        .checked_add(I80F48::from_num(amount))
        .ok_or_else(math_error!())?;

    bank.asset_share_value = updated_total_assets
        .checked_div(total_asset_shares)
        .ok_or_else(math_error!())?
        .into();

    bank.update_bank_cache(&group)?;

    msg!(
        "Deposited {} same-bank emissions into liquidity vault",
        amount
    );

    Ok(())
}
```

**File:** guides/USER/FEES.md (L9-16)
```markdown
### Deposit Fees

There are never any fees to deposit into p0.

### Withdraw fees

There are never any fees to withdraw from p0.

```

**File:** programs/marginfi/src/instructions/marginfi_account/deposit.rs (L33-93)
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
    marginfi_account.last_update = clock.unix_timestamp as u64;
```

**File:** programs/marginfi/src/instructions/marginfi_account/withdraw.rs (L45-131)
```rust
pub fn lending_account_withdraw<'info>(
    mut ctx: Context<'info, LendingAccountWithdraw<'info>>,
    amount: u64,
    withdraw_all: Option<bool>,
) -> MarginfiResult {
    let LendingAccountWithdraw {
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

    let withdraw_all = withdraw_all.unwrap_or(false);
    let mut marginfi_account = marginfi_account_loader.load_mut()?;
    let group = marginfi_group_loader.load()?;

    {
        let maybe_bank_mint = {
            let bank = bank_loader.load()?;
            utils::maybe_take_bank_mint(&mut ctx.remaining_accounts, &bank, token_program.key)?
        };

        let in_receivership_or_order_execution =
            marginfi_account.get_flag(ACCOUNT_IN_RECEIVERSHIP | ACCOUNT_IN_ORDER_EXECUTION);
        let mut bank = bank_loader.load_mut()?;
        validate_bank_state(&bank, InstructionKind::FailsInPausedState)?;

        // Fetch oracle price for rate limiting and deleverage tracking
        // When group rate limiter is enabled, oracle is required
        let group_rate_limit_enabled = group.rate_limiter.is_enabled();
        let price = if in_receivership_or_order_execution || group_rate_limit_enabled {
            let price = fetch_asset_price_for_bank_low_bias(
                &bank_loader.key(),
                &bank,
                &clock,
                ctx.remaining_accounts,
            )?;

            // Validate price is non-zero during liquidation/deleverage to prevent exploits
            if in_receivership_or_order_execution {
                check!(price > I80F48::ZERO, MarginfiError::ZeroAssetPrice);
            }

            price
        } else {
            I80F48::ZERO
        };

        bank.accrue_interest(
            clock.unix_timestamp,
            &group,
            #[cfg(not(feature = "client"))]
            bank_loader.key(),
        )?;

        let liquidity_vault_authority_bump = bank.liquidity_vault_authority_bump;

        let in_receivership = marginfi_account.get_flag(ACCOUNT_IN_RECEIVERSHIP);
        let lending_account = &mut marginfi_account.lending_account;
        let mut bank_account =
            BankAccountWrapper::find(&bank_loader.key(), &mut bank, lending_account)?;

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

**File:** programs/marginfi/src/lib.rs (L209-216)
```rust
    /// (permissionless) Deposit same-bank emissions directly into liquidity vault and increase
    /// depositors' value via `asset_share_value`.
    pub fn lending_pool_emissions_deposit(
        ctx: Context<LendingPoolEmissionsDeposit>,
        amount: u64,
    ) -> MarginfiResult {
        marginfi_group::lending_pool_emissions_deposit(ctx, amount)
    }
```
