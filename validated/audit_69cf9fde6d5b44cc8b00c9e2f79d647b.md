Based on my investigation, marginfi-v2 does have an analogous mechanic to the Unitas M-5 issue, but the codebase demonstrates it is a known, explicitly-designed risk parameter with partial mitigation for the most-affected path (borrow/withdraw), while liquidation retains a hard-fail dependency on live, non-stale, within-confidence oracle data for the specific bank pair involved.

### Title
Depegged/volatile asset oracle (`OracleMaxConfidenceExceeded`/stale price) hard-fails classic liquidation for that specific bank pair, freezing bad-debt resolution during exactly the volatility event liquidation is needed for - (File: `programs/marginfi/src/instructions/marginfi_account/liquidate.rs`, `programs/marginfi/src/state/price.rs`)

### Summary
When an asset depegs or experiences high volatility, its Pyth/Switchboard confidence interval widens. marginfi's oracle adapters reject the raw price entirely with `OracleMaxConfidenceExceeded` if the reported confidence exceeds `oracle_max_confidence` (default 10%), before any bias-clamping is applied. [1](#0-0)  The same hard failure occurs if the oracle is merely stale (`PythPushStalePrice`/`SwitchboardStalePrice`). [2](#0-1) 

For borrow/withdraw, this is mitigated: the risk engine treats an errored balance as zero and only fails the whole tx with the generic `RiskEngineInitRejected` if the remaining, non-errored collateral is insufficient — this is exactly the "ignore stale/degraded oracle, keep the rest of the system working" mitigation Sherlock's report recommended. [3](#0-2) 

Classic liquidation (`lending_account_liquidate`), however, requires the specific asset bank's and liability bank's oracles to resolve successfully with no fallback; any oracle error for either bank (including `OracleMaxConfidenceExceeded` from a depeg or `PythPushStalePrice`) propagates directly and aborts the entire liquidation for that bank pairing. [4](#0-3)  This is confirmed by the test `re_liquidaiton_fail`, where a stale price on the borrower's sole collateral bank causes the liquidation attempt to hard-fail with `PythPushStalePrice`, and only succeeds once that specific oracle is refreshed. [5](#0-4) 

### Finding Description
The `oracle_max_confidence` check is a "min/max price band"-style safeguard functionally identical to Unitas's `minPrice`/`maxPrice` deviation check: both reject any price that moves/varies "too much," which is exactly the situation seen during a depeg or high-volatility event. In marginfi, this happens automatically at the oracle-confidence layer, without requiring a compromised feeder — a real market depeg widens the Pyth/Switchboard confidence interval organically and triggers `OracleMaxConfidenceExceeded`. [6](#0-5) 

For `lending_account_liquidate`, both `fetch_asset_price_for_bank_low_bias` (for the asset bank) and the direct `OraclePriceFeedAdapter::try_from_bank` call (for the liability bank) propagate this error with `?`, with no try/catch or fallback to a cached "last known good" price for the live liquidation math. [7](#0-6)  This means that during exactly the period when an asset is depegging (and liquidation of undercollateralized positions is most urgent), any liquidator attempting classic liquidation on a position collateralized/borrowed against that specific asset will have their liquidation transaction hard-fail, regardless of how severely underwater the position is.

The `BankCache.last_oracle_price` exists and is stamped after successful price fetches, but it is not used as a liquidation-time fallback for classic liquidation's live math — it is informational/for indexers, not a price source consumed by `lending_account_liquidate`'s core accounting. [8](#0-7) 

### Impact Explanation
If a bank's asset depegs (wide confidence interval) or its oracle goes stale during exactly that volatility, `lending_account_liquidate` for any position using that bank as asset or liability bank hard-fails for third-party liquidators, until the oracle stabilizes or is re-cranked with tighter confidence. [4](#0-3)  This can allow undercollateralized positions on the depegged bank to remain unliquidated during the depeg window, risking bad debt accrual and depositor losses — the same "freeze funds when it matters most" impact Sherlock ruled Medium in the Unitas report. Receivership liquidation (`start_receivership`/`check_pre_liquidation_condition_and_get_account_health`) has the same hard dependency on live oracle success via `HealthPriceMode::Live`, so this is not unique to classic liquidation. [9](#0-8) 

### Likelihood Explanation
This requires only a real market event (a stablecoin depeg or a spike in oracle-reported volatility) — no privileged actor or compromise is needed, and it affects any permissionless liquidator attempting `lending_account_liquidate` or receivership liquidation against a position collateralized/borrowed with the affected bank. [10](#0-9)  Likelihood is moderate: it is bounded to windows where confidence genuinely exceeds the configured `oracle_max_confidence` (default 10%, itself already a design safeguard), and is time-limited to that volatility window rather than being a permanent freeze.

### Recommendation
Marginfi already implements the core of Sherlock's recommended fix for borrow/withdraw (graceful degradation via `RiskEngineInitRejected` rather than a hard error whenever sufficient other collateral exists). [3](#0-2)  Consider extending an equivalent bounded fallback to classic/receivership liquidation — e.g., permitting liquidation to proceed using the last cached `BankCache.last_oracle_price`/confidence within a tightly bounded staleness/deviation window specifically for the liquidation path (not general accounting), so that liquidators can still resolve undercollateralized positions during depeg events, subject to conservative bias to avoid value extraction from stale data.

### Proof of Concept
1. Borrower deposits collateral in Bank A and borrows against Bank B, using Pyth push oracles for both.
2. Bank A's underlying asset depegs/becomes volatile; Pyth's reported confidence for Bank A widens beyond `oracle_max_confidence` (or the feed goes stale past `oracle_max_age`).
3. Borrower's position becomes unhealthy (maintenance health < 0) due to reweighting effects of the depeg (as simulated in `re_liquidaiton_fail` via asset-weight reduction, standing in for a genuine price shock). [11](#0-10) 
4. A third-party liquidator calls `lending_account_liquidate` passing Bank A as the asset bank; `fetch_asset_price_for_bank_low_bias` fails with `OracleMaxConfidenceExceeded` (or `PythPushStalePrice`), and the entire liquidation instruction reverts. [12](#0-11) 
5. This repeats for every liquidation attempt on this specific bank pairing until Bank A's oracle confidence narrows back under the threshold or is refreshed, during which the undercollateralized position cannot be liquidated through this path — confirmed directly by the assertion in `re_liquidaiton_fail` (`assert_custom_error!(res.unwrap_err(), MarginfiError::PythPushStalePrice)`). [13](#0-12)

### Citations

**File:** programs/marginfi/src/state/price.rs (L1402-1428)
```rust
    fn get_confidence_interval(&self, oracle_max_confidence: u32) -> MarginfiResult<I80F48> {
        let conf_interval: I80F48 = I80F48::from_num(self.feed.result.std_dev)
            .checked_div(EXP_10_I80F48[switchboard_on_demand::PRECISION as usize])
            .ok_or_else(math_error!())?
            .checked_mul(STD_DEV_MULTIPLE)
            .ok_or_else(math_error!())?;

        let price = self.get_price()?;

        // Fail the price fetch if confidence > price * oracle_max_confidence
        let oracle_max_confidence = if oracle_max_confidence > 0 {
            I80F48::from_num(oracle_max_confidence)
        } else {
            // The default max confidence is 10%
            U32_MAX_DIV_10
        };
        let max_conf = price
            .checked_mul(oracle_max_confidence)
            .ok_or_else(math_error!())?
            .checked_div(U32_MAX)
            .ok_or_else(math_error!())?;
        if conf_interval > max_conf {
            let conf_interval = conf_interval.to_num::<f64>();
            let max_conf = max_conf.to_num::<f64>();
            msg!("conf was {:?}, but max is {:?}", conf_interval, max_conf);
            return err!(MarginfiError::OracleMaxConfidenceExceeded);
        }
```

**File:** programs/marginfi/src/state/price.rs (L1547-1562)
```rust
    pub fn load_checked(ai: &AccountInfo, clock: &Clock, max_age: u64) -> MarginfiResult<Self> {
        let price_feed_account = load_price_update_v2_checked(ai)?;
        let feed_id = &price_feed_account.price_message.feed_id;

        let price = price_feed_account
            .get_price_no_older_than_with_custom_verification_level(
                clock,
                max_age,
                feed_id,
                MIN_PYTH_PUSH_VERIFICATION_LEVEL,
            )
            .map_err(|e| {
                debug!("Pyth push oracle error: {:?}", e);
                let error: MarginfiError = e.into();
                error
            })?;
```

**File:** programs/marginfi/src/state/price.rs (L1629-1670)
```rust
    fn get_confidence_interval(
        &self,
        use_ema: bool,
        oracle_max_confidence: u32,
    ) -> MarginfiResult<I80F48> {
        let price = if use_ema {
            &self.ema_price
        } else {
            &self.price
        };

        let conf_interval =
            pyth_price_components_to_i80f48(I80F48::from_num(price.conf), price.exponent)?
                .checked_mul(CONF_INTERVAL_MULTIPLE)
                .ok_or_else(math_error!())?;

        let price = pyth_price_components_to_i80f48(I80F48::from_num(price.price), price.exponent)?;

        // Fail the price fetch if confidence > price * oracle_max_confidence
        let oracle_max_confidence = if oracle_max_confidence > 0 {
            I80F48::from_num(oracle_max_confidence)
        } else {
            // The default max confidence is 10%
            U32_MAX_DIV_10
        };
        let max_conf = price
            .checked_mul(oracle_max_confidence)
            .ok_or_else(math_error!())?
            .checked_div(U32_MAX)
            .ok_or_else(math_error!())?;
        if conf_interval > max_conf {
            let price = price.to_num::<f64>();
            let conf_interval = conf_interval.to_num::<f64>();
            let max_conf = max_conf.to_num::<f64>();
            msg!(
                "oracle price: {:?}, conf was {:?}, but max is {:?}",
                price,
                conf_interval,
                max_conf
            );
            return err!(MarginfiError::OracleMaxConfidenceExceeded);
        }
```

**File:** programs/marginfi/tests/misc/risk_engine_flexible_oracle_checks.rs (L61-70)
```rust
    // Borrow SOL
    let res = borrower_mfi_account_f
        .try_bank_borrow_with_nonce(borrower_token_account_f_sol.key, sol_bank, 99, 1)
        .await;

    assert!(res.is_err());
    // Note that the error is RiskEngineInitRejected, and not PythPushStalePrice because
    // we're ignoring the stale oracle errors for the collateral banks. This is because
    // the most important thing is to have enough collateral (in non-stale banks) in total.
    assert_custom_error!(res.unwrap_err(), MarginfiError::RiskEngineInitRejected);
```

**File:** programs/marginfi/tests/misc/risk_engine_flexible_oracle_checks.rs (L218-325)
```rust
#[tokio::test]
/// Borrower borrows USDC against SOL, if SOL oracle is stale, the liquidation should fail.
///
/// Liquidator is using SOLE and USDC as collateral, if SOLE oracle is stale and USDC is live,
/// liquidation should succeed as the liquidator has enough USDC collateral.
async fn re_liquidaiton_fail() -> anyhow::Result<()> {
    let test_f = TestFixture::new(Some(TestSettings {
        banks: vec![
            TestBankSetting {
                mint: BankMint::Usdc,
                ..Default::default()
            },
            TestBankSetting {
                mint: BankMint::SolEquivalent,
                ..Default::default()
            },
            TestBankSetting {
                mint: BankMint::Sol,
                config: Some(BankConfig {
                    asset_weight_init: I80F48!(1).into(),
                    asset_weight_maint: I80F48!(1).into(),
                    ..*DEFAULT_SOL_TEST_BANK_CONFIG
                }),
            },
        ],
        protocol_fees: false,
    }))
    .await;

    test_f.set_time(0);

    let usdc_bank_f = test_f.get_bank(&BankMint::Usdc);
    let sol_bank_f = test_f.get_bank(&BankMint::Sol);
    let sole_bank_f = test_f.get_bank(&BankMint::SolEquivalent);

    let lender_mfi_account_f = test_f.create_marginfi_account().await;
    let lender_token_account_usdc = test_f
        .usdc_mint
        .create_token_account_and_mint_to(2_000)
        .await;
    lender_mfi_account_f
        .try_bank_deposit(lender_token_account_usdc.key, usdc_bank_f, 2_000, None)
        .await?;
    let lender_token_account_sole = test_f
        .sol_equivalent_mint
        .create_token_account_and_mint_to(100)
        .await;
    lender_mfi_account_f
        .try_bank_deposit(lender_token_account_sole.key, sole_bank_f, 100, None)
        .await?;

    let borrower_mfi_account_f = test_f.create_marginfi_account().await;
    let borrower_token_account_sol = test_f.sol_mint.create_token_account_and_mint_to(100).await;
    let borrower_token_account_usdc = test_f.usdc_mint.create_empty_token_account().await;

    // Borrower deposits 100 SOL worth $1000
    borrower_mfi_account_f
        .try_bank_deposit(borrower_token_account_sol.key, sol_bank_f, 100, None)
        .await?;

    // Borrower borrows $999
    borrower_mfi_account_f
        .try_bank_borrow(borrower_token_account_usdc.key, usdc_bank_f, 999)
        .await?;

    // Synthetically bring down the borrower account health by reducing the asset weights of the SOL bank
    sol_bank_f
        .update_config(
            BankConfigOpt {
                asset_weight_init: Some(I80F48!(0.25).into()),
                asset_weight_maint: Some(I80F48!(0.5).into()),
                ..Default::default()
            },
            None,
        )
        .await?;

    // Make borrower asset bank stale
    test_f.set_pyth_oracle_timestamp(PYTH_SOL_FEED, 0).await;
    test_f.set_pyth_oracle_timestamp(PYTH_USDC_FEED, 120).await;
    test_f
        .set_pyth_oracle_timestamp(PYTH_SOL_EQUIVALENT_FEED, 120)
        .await;

    test_f.advance_time(120).await;

    let res = lender_mfi_account_f
        .try_liquidate(&borrower_mfi_account_f, sol_bank_f, 1, usdc_bank_f)
        .await;

    assert!(res.is_err());
    assert_custom_error!(res.unwrap_err(), MarginfiError::PythPushStalePrice);

    // Make borrower asset bank not stale
    test_f.set_pyth_oracle_timestamp(PYTH_SOL_FEED, 120).await;
    // Make part of liquidator deposts stale
    test_f
        .set_pyth_oracle_timestamp(PYTH_SOL_EQUIVALENT_FEED, 0)
        .await;

    let res = lender_mfi_account_f
        .try_liquidate(&borrower_mfi_account_f, sol_bank_f, 2, usdc_bank_f)
        .await;

    assert!(res.is_ok());

    Ok(())
}
```

**File:** programs/marginfi/src/instructions/marginfi_account/liquidate.rs (L215-235)
```rust
        let asset_price: I80F48 = fetch_asset_price_for_bank_low_bias(
            &asset_bank_key,
            &asset_bank,
            &clock,
            ctx.remaining_accounts,
        )?;
        check!(asset_price > I80F48::ZERO, MarginfiError::ZeroAssetPrice);

        let mut liab_bank = ctx.accounts.liab_bank.load_mut()?;
        let liab_bank_remaining_accounts_len = get_remaining_accounts_per_bank(&liab_bank)? - 1;
        let liab_price: I80F48 = {
            let oracle_ais = &ctx.remaining_accounts[asset_bank_remaining_accounts_len
                ..(asset_bank_remaining_accounts_len + liab_bank_remaining_accounts_len)];
            let liab_pf = OraclePriceFeedAdapter::try_from_bank(&liab_bank, oracle_ais, &clock)?;
            liab_pf.get_price_of_type(
                OraclePriceType::RealTime,
                Some(PriceBias::High),
                liab_bank.config.oracle_max_confidence,
            )?
        };
        check!(liab_price > I80F48::ZERO, MarginfiError::ZeroLiabilityPrice);
```

**File:** type-crate/src/types/bank_cache.rs (L38-43)
```rust
    /// Oracle price used in the last instruction that consumed an oracle price
    /// * Only updated when instruction uses an oracle price, not updated for operations that don't
    ///   require prices (e.g., deposit, repay)
    /// * Price in USD, with no price bias
    /// * Zero if never updated
    pub last_oracle_price: WrappedI80F48,
```

**File:** programs/marginfi/src/instructions/marginfi_account/liquidate_start.rs (L95-104)
```rust
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

**File:** guides/RISK_AND_LIQUIDATORS/RISK_PARAMETERS.md (L80-83)
```markdown
- **`oracle_max_age`**: Maximum age (in seconds) of an oracle price before it's considered stale.
  Minimum enforced value is 10 seconds. Stale prices will cause transactions to fail.
- **`oracle_max_confidence`**: Maximum allowed confidence interval width. If set to 0, defaults to
  10% (0.10). If the oracle's confidence exceeds this threshold, the price is rejected.
```
