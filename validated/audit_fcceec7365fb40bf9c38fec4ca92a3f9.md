Based on my research, I found a marginfi analog that maps closely to the Malt "theft of system profit" pattern: a permissionless instruction that injects value into a share-based pool, whose distribution is proportional to current share ownership at call-time, with no anti-flash-deposit protection.

### Title
Permissionless `lending_pool_emissions_deposit` can be sandwiched with a flash-loaned deposit to steal reward value from genuine depositors - (File: `programs/marginfi/src/instructions/marginfi_group/configure_bank.rs`)

### Summary
`lending_pool_emissions_deposit` lets *anyone* push tokens directly into a bank's liquidity vault and immediately re-price `asset_share_value` for all outstanding shares, exactly as `total_assets / total_asset_shares` is recomputed at call time [1](#0-0) . Because the payout is split strictly in proportion to whoever holds shares at the instant the instruction executes, an attacker can inflate their own share of the pool immediately beforehand (via a flash-loaned deposit) to capture a disproportionate slice of a reward/emissions injection intended for existing, genuine depositors — mirroring the Malt "stabilize" sandwich, where an outsider with no real economic stake extracts value meant for legitimate stakeholders around a permissionless, value-redistributing call.

### Finding Description
`lending_pool_emissions_deposit` is explicitly documented as permissionless and is designed to raise `asset_share_value` for the whole bank: "Permissionlessly deposit same-mint emissions directly into the bank liquidity vault, increasing depositor value through asset share value" [2](#0-1) . The instruction transfers `amount` tokens from an `emissions_funding_account` into the `liquidity_vault`, then recomputes the global `asset_share_value` as `(total_assets + amount) / total_asset_shares` — with no accounting of *when* each depositor's shares were acquired [3](#0-2) . This is corroborated by the patch notes describing it as a new "permissionless" instruction that raises `asset_share_value` [4](#0-3) , and by tests confirming that reward capture is purely proportional to each depositor's share of `total_asset_shares` at the moment the deposit lands [5](#0-4) .

There is no minimum holding period, no snapshotting of prior balances, and no restriction on who may call `lending_pool_emissions_deposit` or who may deposit into the bank beforehand. A user can:
1. Take a flash loan (`lending_account_start_flashloan`) of the bank's underlying mint.
2. Deposit the borrowed amount into the target bank (a low-cost, oracle-free operation), instantly becoming the dominant (or a much larger) shareholder of `total_asset_shares`.
3. When a legitimate emissions/reward injection via `lending_pool_emissions_deposit` executes (whether self-triggered with the attacker's own capital timed opportunistically, or raced ahead of a scheduled/observed reward-funding transaction), the new `asset_share_value` bump is distributed according to the *post-flash-deposit* share distribution, giving the attacker's temporary stake a share of the reward far larger than any genuine, long-standing depositor would normally receive.
4. Withdraw the flash-loaned principal (plus the captured reward proportion) and repay the flash loan in the same transaction, since `lending_account_withdraw` for a healthy, liability-free account passes trivially and requires no price check delay [6](#0-5) .

This is structurally identical to the Malt bug class: a permissionless, value-redistributing action tied to the pool's current state (share proportions, in this case, rather than AMM price) can be sandwiched by anyone with capital access (a flash loan), letting them siphon value intended for genuine long-term participants without ever needing to hold a real position.

### Impact Explanation
Reward/emissions capital meant to accrue to real depositors is redirected to opportunistic flash-loan actors with no durable exposure to the bank, directly diminishing payouts to legitimate LPs. This is a value-redirection/financial-effect bug rather than a fund-freeze or authorization bypass, consistent with the Medium severity the original judge assigned to the Malt analog.

### Likelihood Explanation
The attack requires only a flash loan of the bank's mint (broadly available across DeFi) and knowledge/timing of an upcoming `lending_pool_emissions_deposit` call. Because the instruction is permissionless and has no cooldown/snapshot protection, exploitation is straightforward to script; the main constraint is being able to time entry immediately before the reward-funding transaction lands (feasible via priority-fee races or bundle submission, similar to standard Solana MEV sandwiching).

### Recommendation
- Require `lending_pool_emissions_deposit` payouts to be distributed based on a time-weighted or snapshot share balance (e.g., balances as of N slots/epochs prior) rather than the instantaneous `total_asset_shares` at call time.
- Alternatively, restrict who may call `lending_pool_emissions_deposit` (e.g., admin/risk_admin only) or add a minimum-holding-duration requirement before a deposit counts toward reward-share eligibility.
- Consider disallowing deposits and emissions-deposits from landing in the same transaction/flash-loan context.

### Proof of Concept
1. Attacker starts a flash loan for bank `B`'s mint via `lending_account_start_flashloan`.
2. Attacker calls `lending_account_deposit` into `B` with the borrowed amount, becoming a majority (or large minority) holder of `B.total_asset_shares` [7](#0-6) .
3. A `lending_pool_emissions_deposit(amount)` call executes for bank `B` (self-triggered or raced ahead of a legitimate reward funder), recomputing `asset_share_value = (total_assets + amount) / total_asset_shares` [1](#0-0) , crediting the attacker's inflated share proportionally.
4. Attacker calls `lending_account_withdraw` to redeem shares at the new, higher `asset_share_value`, then `lending_account_end_flashloan` to repay the loan — pocketing the disproportionate reward share, at the expense of genuine depositors who held shares before and after but received a diluted portion of the injected value.

**Uncertainty note:** I was not able to fully confirm (due to iteration limits) whether `lending_pool_emissions_deposit` is realistically ever triggered by a third party in a way an attacker could front-run on Solana (vs. always being self-triggered by the same actor supplying the funds, in which case the "theft" only dilutes other depositors rather than yielding net attacker profit), nor did I get to review the full `SECURITY.md` entry that matched search terms like "flash loan," which may indicate this class of issue is already flagged as a known/out-of-scope risk. I recommend verifying against `SECURITY.md`'s full text before treating this as a novel finding.

### Citations

**File:** programs/marginfi/src/instructions/marginfi_group/configure_bank.rs (L84-92)
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
```

**File:** programs/marginfi/src/instructions/marginfi_group/configure_bank.rs (L111-146)
```rust
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
```

**File:** patch-note-drafts/patch-notes-0.1.9.md (L151-154)
```markdown
### Emissions

- `lending_pool_emissions_deposit(amount)` (permissionless) — deposit same-bank emissions directly
  into the liquidity vault, raising `asset_share_value`.
```

**File:** programs/marginfi/tests/misc/emissions_deposit.rs (L211-286)
```rust
#[tokio::test]
async fn emissions_same_bank_deposit_updates_asset_share_value() -> anyhow::Result<()> {
    let test_f = TestFixture::new(Some(TestSettings::all_banks_payer_not_admin())).await;

    let usdc_bank = test_f.get_bank(&BankMint::Usdc);

    let emissions_funding = test_f.usdc_mint.create_token_account_and_mint_to(50).await;

    let depositor_a = test_f.create_marginfi_account().await;
    let depositor_b = test_f.create_marginfi_account().await;

    let depositor_a_usdc = test_f.usdc_mint.create_token_account_and_mint_to(40).await;
    let depositor_b_usdc = test_f.usdc_mint.create_token_account_and_mint_to(60).await;

    let depositor_a_amount = 40;
    depositor_a
        .try_bank_deposit(
            depositor_a_usdc.key,
            usdc_bank,
            depositor_a_amount as f64,
            None,
        )
        .await?;

    let depositor_b_amount = 60;
    depositor_b
        .try_bank_deposit(
            depositor_b_usdc.key,
            usdc_bank,
            depositor_b_amount as f64,
            None,
        )
        .await?;

    let bank_before = usdc_bank.load().await;
    let shares_before = I80F48::from(bank_before.total_asset_shares);
    let share_value_before = I80F48::from(bank_before.asset_share_value);

    let liquidity_vault_before =
        TokenAccountFixture::fetch(test_f.context.clone(), bank_before.liquidity_vault)
            .await
            .balance()
            .await;

    let emissions_deposit = 50;
    usdc_bank
        .try_emissions_deposit(native!(emissions_deposit, "USDC"), emissions_funding.key)
        .await?;

    let bank_after = usdc_bank.load().await;
    let shares_after = I80F48::from(bank_after.total_asset_shares);
    let share_value_after = I80F48::from(bank_after.asset_share_value);

    let liquidity_vault_after =
        TokenAccountFixture::fetch(test_f.context.clone(), bank_after.liquidity_vault)
            .await
            .balance()
            .await;

    let asset_shares_value_multiplier =
        1.0 + emissions_deposit as f64 / (depositor_a_amount + depositor_b_amount) as f64;

    assert_eq!(shares_after, shares_before);

    // Should be equal, zero liabilities are present
    assert_eq!(
        share_value_before
            .checked_mul(I80F48::from_num(asset_shares_value_multiplier))
            .unwrap(),
        share_value_after
    );
    assert_eq!(
        liquidity_vault_after - liquidity_vault_before,
        native!(emissions_deposit, "USDC")
    );
    assert_eq!(I80F48::from(bank_after.emissions_remaining), I80F48::ZERO);
```

**File:** programs/marginfi/src/instructions/marginfi_account/withdraw.rs (L207-246)
```rust
    }

    let mut health_cache = HealthCache::zeroed();
    health_cache.timestamp = clock.unix_timestamp;

    marginfi_account.lending_account.sort_balances();
    marginfi_account.sync_indexer_flags();

    // To update the bank's price cache
    let maybe_price: Option<OraclePriceWithMultiplier>;
    let bank_pk = bank_loader.key();

    // Note: during receivership and order execution, we skip all health checks until the end of the transaction.
    if !marginfi_account.get_flag(ACCOUNT_IN_RECEIVERSHIP | ACCOUNT_IN_ORDER_EXECUTION) {
        // Check account health, if below threshold fail transaction
        // Assuming `ctx.remaining_accounts` holds only oracle accounts
        // Uses heap-efficient health check to support accounts with up to 16 positions
        check_account_init_health(
            &marginfi_account,
            ctx.remaining_accounts,
            &mut Some(&mut health_cache),
        )?;
        health_cache.program_version = PROGRAM_VERSION;

        health_cache.set_engine_ok(true);
        marginfi_account.health_cache = health_cache;
    }

    // Fetch unbiased price for cache update
    // Note: during receivership, callers may omit oracle accounts; the cache simply won't update.
    {
        let bank = bank_loader.load()?;
        maybe_price =
            fetch_unbiased_price_for_bank_cache(&bank_pk, &bank, &clock, ctx.remaining_accounts)
                .ok();
    }

    bank_loader.load_mut()?.update_cache_price(maybe_price)?;

    Ok(())
```
