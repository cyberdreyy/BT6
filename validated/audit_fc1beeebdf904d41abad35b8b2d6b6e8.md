## Title
Missing depositor-specified minimum-shares-out protection on `kamino_deposit`, `drift_deposit`, and `juplend_deposit` integration CPI paths - ([File: programs/marginfi/src/instructions/juplend/deposit.rs])

### Summary
The Napier report describes `MetapoolRouter.addLiquidityOneETHKeepYt` issuing a variable amount of YT with only a slippage bound on the LP-token side (`minLiquidity`), leaving the YT amount unprotected against exchange-rate movement between transaction construction and execution. Marginfi's external-integration deposit instructions (`kamino_deposit`, `drift_deposit`, `juplend_deposit`) exhibit the same structural gap: the number of shares/scaled-balance units credited to the user is computed from a live, mutable on-chain exchange rate at execution time, and there is no user-supplied minimum-output parameter to bound that outcome.

### Finding Description
In `juplend_deposit`, the flow is:
1. CPI `update_rate` to refresh the JupLend `token_exchange_price`/`liquidity_exchange_price`.
2. Compute `expected_shares` from the just-refreshed rates.
3. Transfer underlying and CPI `deposit`.
4. Assert the actual minted shares equal the just-computed `expected_shares`. [1](#0-0) 

The `require_eq!` check only proves internal consistency between the instruction's own expectation and the CPI's return value — both are computed from the same (potentially moved) on-chain rate at execution time. There is no caller-supplied `min_shares` argument, unlike JupLend's own program, which exposes a `deposit_with_min_amount_out(assets, min_amount_out)` entry point that marginfi does not use. [2](#0-1) 

The same pattern recurs in `kamino_deposit` (expected collateral computed from `liquidity_to_collateral`, then only checked "within one token" of the actual obligation change, with no depositor-supplied floor) and in `drift_deposit` (expected scaled-balance increment computed from spot-market state right before the CPI, with no minimum bound param). [3](#0-2) [4](#0-3) 

Exchange rates for all three integrations (JupLend `token_exchange_price`/`liquidity_exchange_price`, Kamino reserve exchange rate, Drift `cumulative_deposit_interest`) accrue continuously and can also be nudged by other permissionless activity (e.g., interest-rate updates, other users' deposits/borrows/repays) landing ahead of a given transaction in the same block or between simulation and landing. None of `kamino_deposit`, `drift_deposit`, or `juplend_deposit` accept a `min_shares_out`/`min_collateral_out` instruction argument analogous to the Napier report's recommended `minYT`.

### Impact Explanation
This is a value/expectation-mismatch bug in the same class as the referenced Napier finding: a depositing user has no on-chain enforced guarantee that the shares/collateral they receive for their deposited underlying meet a minimum they consider acceptable. If the relevant exchange rate moves unfavorably between when a user signs/broadcasts the transaction and when it lands (e.g., due to interest accrual, or another party's transaction executing first in the same slot), the user's deposit is credited with fewer shares than expected, and the transaction still succeeds silently. This is a self-inflicted value loss for the depositor (not a fund-redirection vector to third parties), which limits severity but matches the reported bug class (missing slippage protection on a variable-conversion deposit path) exactly.

### Likelihood Explanation
Reachable by any unprivileged user calling `kamino_deposit`, `drift_deposit`, or `juplend_deposit` — no special permissions required. Likelihood of a materially bad outcome depends on how much the exchange rate can move within a block/slot window, which for interest-accrual-driven rates is typically small per transaction but grows with time-in-flight (e.g., congestion, retries) and is fully unbounded from the protocol's perspective since no cap exists.

### Recommendation
Add an optional `min_shares_out` (or `min_collateral_out`) parameter to `kamino_deposit`, `drift_deposit`, and `juplend_deposit`, and `require_gte!` the actual CPI-derived shares/collateral/scaled-balance against it before crediting the marginfi account, mirroring JupLend's own `deposit_with_min_amount_out` pattern. Consider routing the JupLend integration through `deposit_with_min_amount_out` directly rather than plain `deposit`.

### Proof of Concept
1. User A prepares a `juplend_deposit(amount)` transaction off-chain, expecting `expected_shares` based on the currently observed `token_exchange_price`.
2. Before User A's transaction lands, another transaction (e.g., a large borrow/repay or an `update_rate` call from another actor) shifts `token_exchange_price` unfavorably.
3. User A's transaction still succeeds — `expected_shares` is recomputed post-CPI-refresh inside the same instruction and matched exactly against the CPI result — but the number of shares credited is lower than what User A anticipated when signing, with no revert path available to them. [5](#0-4)

### Citations

**File:** programs/marginfi/src/instructions/juplend/deposit.rs (L41-83)
```rust
pub fn juplend_deposit(ctx: Context<JuplendDeposit>, amount: u64) -> MarginfiResult {
    let authority_bump: u8;
    {
        let marginfi_account = ctx.accounts.marginfi_account.load()?;
        let bank = ctx.accounts.bank.load()?;
        authority_bump = bank.liquidity_vault_authority_bump;

        validate_asset_tags(&bank, &marginfi_account)?;
        validate_bank_state(&bank, InstructionKind::FailsIfPausedOrReduceState)?;
    }

    // Refresh the exchange price (interest/rewards) for this slot.
    ctx.accounts.cpi_update_rate()?;

    let expected_shares = {
        let lending = ctx.accounts.integration_acc_1.load()?;
        // Compute expected shares minted (round-down) using the same math as JupLend.
        expected_shares_for_deposit_from_rates(
            amount,
            lending.liquidity_exchange_price,
            lending.token_exchange_price,
        )
        .ok_or_else(|| error!(MarginfiError::MathError))?
    };

    let pre_f_token_balance = accessor::amount(&ctx.accounts.integration_acc_2.to_account_info())?;

    // Move underlying into the vault and deposit into JupLend.
    ctx.accounts.cpi_transfer_user_to_liquidity_vault(amount)?;
    ctx.accounts.cpi_juplend_deposit(amount, authority_bump)?;

    let post_f_token_balance = accessor::amount(&ctx.accounts.integration_acc_2.to_account_info())?;
    let minted_shares = post_f_token_balance
        .checked_sub(pre_f_token_balance)
        .ok_or_else(|| error!(MarginfiError::MathError))?;

    // Exact match required.
    require_eq!(
        minted_shares,
        expected_shares,
        MarginfiError::JuplendDepositFailed
    );

```

**File:** idls-complete/juplend_earn.json (L105-123)
```json
    {
      "name": "deposit_with_min_amount_out",
      "discriminator": [
        116,
        144,
        16,
        97,
        118,
        109,
        40,
        119
      ],
      "accounts": [
        {
          "name": "signer",
          "writable": true,
          "signer": true
        },
        {
```

**File:** programs/marginfi/src/instructions/kamino/deposit.rs (L47-89)
```rust
pub fn kamino_deposit<'info>(
    ctx: Context<'info, KaminoDeposit<'info>>,
    amount: u64,
    refresh_reserve: Option<bool>,
) -> MarginfiResult {
    let refresh_reserve = refresh_reserve.unwrap_or(false);
    let authority_bump: u8;
    {
        let marginfi_account = ctx.accounts.marginfi_account.load()?;
        let bank = ctx.accounts.bank.load()?;
        authority_bump = bank.liquidity_vault_authority_bump;

        validate_asset_tags(&bank, &marginfi_account)?;
        validate_bank_state(&bank, InstructionKind::FailsIfPausedOrReduceState)?;
    }

    // Get initial obligation data to verify deposit amount later
    let initial_obligation_deposited_amount =
        ctx.accounts.integration_acc_2.load()?.deposits[0].deposited_amount;
    let expected_collateral_amount = ctx
        .accounts
        .integration_acc_1
        .load()?
        .liquidity_to_collateral(amount)?;

    if refresh_reserve {
        ctx.accounts.cpi_refresh_reserve()?;
    }

    ctx.accounts.cpi_transfer_user_to_obligation_owner(amount)?;
    ctx.accounts.cpi_kamino_deposit(amount, authority_bump)?;

    let final_obligation_deposited_amount =
        ctx.accounts.integration_acc_2.load()?.deposits[0].deposited_amount;

    // Verifying the deposit was successful by checking obligation balance increased by the correct amount
    let obligation_collateral_change =
        final_obligation_deposited_amount - initial_obligation_deposited_amount;
    assert_within_one_token(
        obligation_collateral_change,
        expected_collateral_amount,
        MarginfiError::KaminoDepositFailed,
    )?;
```

**File:** programs/marginfi/src/instructions/drift/deposit.rs (L45-85)
```rust
pub fn drift_deposit(ctx: Context<DriftDeposit>, amount: u64) -> MarginfiResult {
    let authority_bump: u8;
    let market_index: u16;
    {
        let marginfi_account = ctx.accounts.marginfi_account.load()?;
        let bank = ctx.accounts.bank.load()?;
        authority_bump = bank.liquidity_vault_authority_bump;

        validate_asset_tags(&bank, &marginfi_account)?;
        validate_bank_state(&bank, InstructionKind::FailsIfPausedOrReduceState)?;

        let integration_acc_1 = ctx.accounts.integration_acc_1.load()?;
        market_index = integration_acc_1.market_index;
    }

    ctx.accounts.cpi_update_spot_market_cumulative_interest()?;
    let expected_scaled_balance_change = ctx
        .accounts
        .integration_acc_1
        .load()?
        .get_scaled_balance_increment(amount)?;

    let initial_scaled_balance = {
        let integration_acc_2 = ctx.accounts.integration_acc_2.load()?;
        integration_acc_2.get_scaled_balance(market_index)
    };

    ctx.accounts.cpi_transfer_user_to_liquidity_vault(amount)?;
    ctx.accounts
        .cpi_drift_deposit(market_index, amount, authority_bump)?;

    let final_scaled_balance = {
        let integration_acc_2 = ctx.accounts.integration_acc_2.load()?;
        integration_acc_2.get_scaled_balance(market_index)
    };
    let scaled_balance_change = final_scaled_balance - initial_scaled_balance;
    require_eq!(
        scaled_balance_change,
        expected_scaled_balance_change,
        MarginfiError::DriftScaledBalanceMismatch
    );
```
