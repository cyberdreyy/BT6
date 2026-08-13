### Title
Zero-price oracle values are not rejected in the core health-check valuation path, allowing debt/assets to be mispriced as worthless - (File: `programs/marginfi/src/state/marginfi_account.rs`)

### Summary
Marginfi enforces non-zero oracle prices only in a few specific code paths (`liquidate.rs`, and the `in_receivership`/deleverage branches of the Kamino/Solend/Drift/JupLend withdraw handlers), but the generic weighted asset/liability valuation logic used for ordinary deposits, borrows, and withdrawals — `calc_weighted_asset_value_standalone` and `calc_weighted_liab_value_standalone` — does not verify that the price returned by the oracle adapter is non-zero before it is fed into `calc_value`.

### Finding Description
`calc_weighted_liab_value_standalone` fetches `higher_price` directly from `price_feed.get_price_of_type(...)` and immediately passes it into `calc_value` with no zero-value guard: [1](#0-0) 

Similarly, the asset-side computation `calc_weighted_asset_value_standalone` uses `lower_price` from the oracle feed without checking it is greater than zero, only special-casing `ReduceOnly` banks and stale-oracle errors: [2](#0-1) 

`calc_value` itself performs no validation on the `price` argument — a zero price simply yields a value of zero: [3](#0-2) 

The underlying oracle adapters (`PythPushOraclePriceFeed::load_checked`, `SwitchboardPullPriceFeed::load_checked`) only validate account ownership, discriminator, verification level, and staleness — they do not reject a decoded price of zero: [4](#0-3) [5](#0-4) 

By contrast, the protocol clearly recognizes the risk and already added `MarginfiError::ZeroAssetPrice` / `ZeroLiabilityPrice` checks, but only in `liquidate.rs`: [6](#0-5) 

and in the `in_receivership`/deleverage paths of the integration withdraw instructions (e.g. Kamino): [7](#0-6) 

The regular health-check flow that gates every borrow, withdraw, and general risk-engine evaluation (`calc_weighted_asset_value_standalone` / `calc_weighted_liab_value_standalone`) is not covered by this same guard.

### Impact Explanation
If a bank's configured oracle transiently or erroneously returns a price of zero (feed misconfiguration, corrupted/zeroed Pyth price message that still passes the verification-level/staleness checks, or a Switchboard feed with `result.value == 0`), then:
- A liability position priced at zero contributes **zero** to `total_liabilities` in the health calculation, causing an otherwise-unhealthy account to appear healthy. This lets a user borrow or withdraw more than they should be permitted to (bypassing `check_account_init_health`), since the debt is not counted.
- An asset position priced at zero contributes zero to `total_assets`, incorrectly reducing borrowing power, but more importantly the liability-side zero-price scenario provides a path to durable insolvency: users can accumulate real, uncollateralized debt that is invisible to the risk engine for the maintenance/initial-margin computation, unlike in `liquidate.rs` where a hard `ZeroLiabilityPrice` revert protects the equivalent liquidation-time computation.

This directly parallels the M-9 report's core defect: oracle price is consumed for critical valuation (health/margin/liquidation-equivalent logic) without a non-zero check, and an incorrect zero price causes affected assets/liabilities to be treated as worthless, resulting in financial-effect state corruption (unauthorized borrow/withdraw due to a falsely-healthy account).

### Likelihood Explanation
This requires the configured oracle account to actually surface a price of `0` while still passing marginfi's existing checks (owner, discriminator, `MIN_PYTH_PUSH_VERIFICATION_LEVEL`, and max-age/staleness). This is a lower-likelihood, oracle-dependent edge case (Pyth/Switchboard feeds do not normally publish exact zero for actively-traded assets), but it is a legitimate and previously-acknowledged risk within this same codebase (evidenced by the presence of `ZeroAssetPrice`/`ZeroLiabilityPrice` and their partial enforcement, plus explicit fuzz coverage exercising "the 0 endpoint exercises marginfi's zero-price guards"): [8](#0-7) 

The fuzz comment implies the zero-price guard is expected to be comprehensive, but the guard is not actually present in the general health-check valuation path, only in liquidation and a subset of receivership withdraw paths — this is an unprivileged-user-reachable inconsistency (any user's borrow/withdraw/health check goes through the unguarded path).

### Recommendation
Add an explicit non-zero (and non-negative) price check inside `calc_weighted_asset_value_standalone` and `calc_weighted_liab_value_standalone` (or centrally inside `calc_value`/`get_price_of_type` consumers) analogous to the `ZeroAssetPrice`/`ZeroLiabilityPrice` checks already used in `liquidate.rs`, so that a zero price returned by any oracle adapter causes the health computation to fail closed (propagate an error) rather than silently valuing the position at zero.

### Proof of Concept
Conceptual PoC (cannot be executed without a way to force a zero-price oracle account on-chain, which is why this remains a design-gap analog rather than a demonstrated live exploit):
1. Configure/compromise a bank's Pyth-push or Switchboard-pull oracle account so that the decoded `price.price` / `result.value` is `0`, while `publish_time`/`last_update_timestamp` remain within `max_age`, and (for Pyth) the verification level satisfies `MIN_PYTH_PUSH_VERIFICATION_LEVEL`.
2. Have an account borrow against collateral in another bank, then let this bank's price go to zero as its liability.
3. Call any instruction that runs `check_account_init_health`/`get_health_components` (e.g., `withdraw`, `borrow`) — `calc_weighted_liab_value_standalone` computes `higher_price = 0`, hence `calc_value(...) == 0` for that liability, and the account's total weighted liabilities are undercounted, letting operations succeed that should be rejected as unhealthy. [9](#0-8)

### Citations

**File:** programs/marginfi/src/state/marginfi_account.rs (L450-481)
```rust
pub fn calc_value(
    amount: I80F48,
    price: I80F48,
    mint_decimals: u8,
    weight: Option<I80F48>,
) -> MarginfiResult<I80F48> {
    if amount == I80F48::ZERO {
        return Ok(I80F48::ZERO);
    }

    let scaling_factor = EXP_10_I80F48[mint_decimals as usize];

    let weighted_asset_amount = if let Some(weight) = weight {
        amount.checked_mul(weight).unwrap()
    } else {
        amount
    };

    #[cfg(target_os = "solana")]
    debug!(
        "weighted_asset_qt: {}, price: {}, expo: {}",
        weighted_asset_amount, price, mint_decimals
    );

    let value = weighted_asset_amount
        .checked_mul(price)
        .ok_or_else(math_error!())?
        .checked_div(scaling_factor)
        .ok_or_else(math_error!())?;

    Ok(value)
}
```

**File:** programs/marginfi/src/state/marginfi_account.rs (L1292-1330)
```rust
            let price_feed = price_adapter_result
                .as_ref()
                .map_err(|_| error!(MarginfiError::from(err_code)))?;

            let mut asset_weight = bank.get_asset_weight(requirement_type, emode_config);

            let lower_price = if let Some(cache) = liq_cache.as_mut() {
                let price_with_confidence = price_feed.get_price_and_confidence_of_type(
                    requirement_type.get_oracle_price_type(),
                    bank.config.oracle_max_confidence,
                )?;
                cache.record(requirement_type, position_index, price_with_confidence);
                apply_price_bias(price_with_confidence, PriceBias::Low)?
            } else {
                price_feed.get_price_of_type(
                    requirement_type.get_oracle_price_type(),
                    Some(PriceBias::Low),
                    bank.config.oracle_max_confidence,
                )?
            };

            // Apply initial discount if applicable
            if matches!(requirement_type, RequirementType::Initial) {
                if let Some(discount) = bank.maybe_get_asset_weight_init_discount(lower_price)? {
                    asset_weight = asset_weight
                        .checked_mul(discount)
                        .ok_or_else(math_error!())?;
                }
            }

            let value = calc_value(
                bank.get_asset_amount(balance.asset_shares.into())?,
                lower_price,
                bank.get_balance_decimals(),
                Some(asset_weight),
            )?;

            Ok((value, lower_price, 0))
        }
```

**File:** programs/marginfi/src/state/marginfi_account.rs (L1367-1389)
```rust
    let higher_price = if let Some(cache) = liq_cache.as_mut() {
        let price_with_confidence = price_feed.get_price_and_confidence_of_type(
            requirement_type.get_oracle_price_type(),
            bank.config.oracle_max_confidence,
        )?;
        cache.record(requirement_type, position_index, price_with_confidence);
        apply_price_bias(price_with_confidence, PriceBias::High)?
    } else {
        price_feed.get_price_of_type(
            requirement_type.get_oracle_price_type(),
            Some(PriceBias::High),
            bank.config.oracle_max_confidence,
        )?
    };

    let value = calc_value(
        bank.get_liability_amount(balance.liability_shares.into())?,
        higher_price,
        bank.get_balance_decimals(),
        Some(liability_weight),
    )?;

    Ok((value, higher_price))
```

**File:** programs/marginfi/src/state/price.rs (L1348-1375)
```rust
    pub fn load_checked(
        ai: &AccountInfo,
        current_timestamp: i64,
        max_age: u64,
    ) -> MarginfiResult<Self> {
        let ai_data = ai.data.borrow();

        check!(
            ai.owner.eq(&SWITCHBOARD_PULL_ID),
            MarginfiError::SwitchboardWrongAccountOwner
        );

        let feed: PullFeedAccountData = parse_swb_ignore_alignment(ai_data)?;
        let lite_feed = LitePullFeedAccountData::from(&feed);
        // TODO restore when swb fixes alignment issue in crate.
        // let feed = PullFeedAccountData::parse(ai_data)
        //     .map_err(|_| MarginfiError::SwitchboardInvalidAccount)?;

        // Check staleness
        let last_updated = feed.last_update_timestamp;
        if current_timestamp.saturating_sub(last_updated) > max_age as i64 {
            return err!(MarginfiError::SwitchboardStalePrice);
        }

        Ok(Self {
            feed: Box::new(lite_feed),
        })
    }
```

**File:** programs/marginfi/src/state/price.rs (L1547-1585)
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

        let ema_price = {
            let price_update::PriceFeedMessage {
                exponent,
                publish_time,
                ema_price,
                ema_conf,
                ..
            } = price_feed_account.price_message;

            price_update::Price {
                price: ema_price,
                conf: ema_conf,
                exponent,
                publish_time,
            }
        };

        Ok(Self {
            price: Box::new(price),
            ema_price: Box::new(ema_price),
        })
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

**File:** programs/marginfi/src/instructions/kamino/withdraw.rs (L110-124)
```rust
        // Fetch oracle price for rate limiting and deleverage tracking
        // When group rate limiter is enabled, oracle is required
        let group_rate_limit_enabled = group.rate_limiter.is_enabled();
        let price = if in_receivership_or_order_execution || group_rate_limit_enabled {
            let price = fetch_asset_price_for_bank_low_bias(
                &bank_key,
                &bank,
                &clock,
                ctx.remaining_accounts,
            )?;

            // Validate price is non-zero during liquidation/deleverage to prevent exploits with stale oracles
            if in_receivership_or_order_execution {
                check!(price > I80F48::ZERO, MarginfiError::ZeroAssetPrice);
            }
```

**File:** trident-tests/fuzz_0/test_fuzz.rs (L658-674)
```rust
    #[flow(weight = 3)]
    fn flow_oracle_move(&mut self) {
        let oracle = match self.trident.random_from_range(0u8..=2) {
            0 => constants::USDC_PYTH_PUSH,
            1 => constants::WETH_PYTH_PUSH,
            _ => constants::BTC_PYTH_PUSH,
        };
        // Numerator in [0, 1_000_000], denominator = 100  →
        //   scale ∈ {0×, ~0.01×, …, ~0.5×, 1×, 2×, …, ~10000×}.
        // The 0 endpoint exercises marginfi's zero-price guards
        // (`ZeroAssetPrice` / `ZeroLiabilityPrice`); the high end pushes
        // health checks against extreme valuations without permanently
        // crashing the test (oracle scale is per-flow, never reverted).
        let numerator: i64 = self.trident.random_from_range(0i64..=1_000_000);
        let denominator: i64 = 100;
        self.scale_pyth_push_oracle_prices(&oracle, numerator, denominator);
    }
```
