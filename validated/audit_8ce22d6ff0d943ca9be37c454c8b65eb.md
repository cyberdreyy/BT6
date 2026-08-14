## Title
No grace period after protocol/bank unpause allows liquidators to front-run and instantly liquidate positions that became unhealthy during the pause - ([File: programs/marginfi/src/instructions/marginfi_group/panic_unpause.rs], [File: programs/marginfi/src/instructions/marginfi_account/liquidate.rs])

### Summary
marginfi has both a global "panic pause" (`FeeState.panic_state`, controlled by the `global_fee_admin`) and a per-bank `Paused` operational state (controlled by the group admin). Both block deposits, borrows, withdrawals, repayments, and liquidations while active. However, when either pause is lifted, liquidation eligibility is re-enabled instantly, with no grace period, using live oracle prices at the moment of liquidation. Any user whose position became under-collateralized (by real market price movement) during the pause — and who was blocked from depositing collateral or repaying debt to fix it — can be liquidated the instant the pause ends, before they get a chance to react. This mirrors the Perennial finding referenced in the report (Sherlock M-15/#190), where liquidation eligibility was evaluated without any buffer for users to remediate after an unpause.

### Finding Description
Global pause and unpause: `PanicState::pause`/`unpause` toggle a simple flag with a bounded duration (`PAUSE_DURATION_SECONDS`, up to 30 minutes) and daily/consecutive limits [1](#0-0) . The admin can end a pause immediately via `panic_unpause`, which simply clears the paused flag and resets counters with no delay before user operations (including liquidation) resume [2](#0-1) . A permissionless variant does the same once the pause naturally expires [3](#0-2) .

During the pause, remediation actions (`deposit`, `repay`, `withdraw`) are all blocked by pause checks in their respective instruction handlers (confirmed present in `deposit.rs`, `repay.rs`, `withdraw.rs`), while liquidation is also blocked by the same mechanism, as confirmed by the test `liquidation_still_blocked_during_pause` [4](#0-3) .

Per-bank pause works analogously: a bank in the `Paused` operational state blocks deposit/withdraw/borrow/repay/liquidate entirely, and can remain paused indefinitely (admin-controlled, no auto-expiry) per `guides/ADMIN/BANK_STATE.md` [5](#0-4)  and the state-machine table [6](#0-5) . The `validate_bank_state` helper enforces `FailsInPausedState` for liquidation [7](#0-6) , and `lending_account_liquidate` calls this check against both the asset and liability banks before proceeding [8](#0-7) .

Once the bank is set back to `Operational` (or the global pause ends), liquidation eligibility is evaluated using `HealthPriceMode::Live` (current oracle prices) via `check_pre_liquidation_condition_and_get_account_health` [9](#0-8) . There is no mechanism anywhere in the unpause path (`panic_unpause`, `panic_unpause_permissionless`, or `lending_pool_configure_bank` restoring `Operational`) that delays liquidation relative to other operations, or gives users a window to react before liquidators can act. Since liquidation is permissionless and open to third parties/bots [10](#0-9) , a bot can submit a liquidation transaction in the very same slot the unpause transaction lands (or immediately after), while the affected user — who could not deposit collateral or repay debt during the pause — has no chance to remediate first.

### Impact Explanation
Users whose accounts drift below the maintenance requirement due to real price movement during a pause window are deprived of the ability to self-correct (deposit/repay/withdraw are all blocked), yet the instant the pause is lifted, MEV/liquidation bots can capture the liquidation premium before the user can act. This causes a direct, unfair loss of funds to affected users (liquidation fee + insurance fee taken from their collateral) purely as a side effect of a privileged pause action, not user negligence. Because the global pause is a single group-wide flag, an unpause can simultaneously expose *every* undercollateralized account across the whole group to instant liquidation, amplifying the loss.

### Likelihood Explanation
The global pause has a bounded duration (up to 30 minutes, and per the README "has never been used once as of November 2025"), limiting exposure, but bank-level pauses have no auto-expiry and are admin-discretionary, so this window can be arbitrarily long. Any real market volatility during a pause of meaningful length combined with a subsequent unpause creates the exact race condition described. This requires no privileged access to exploit — it only requires a liquidator watching for the unpause transaction and racing it, which is realistic given MEV infrastructure on Solana.

### Recommendation
Introduce a brief grace period after a pause ends (global or per-bank) during which liquidation remains blocked while deposits/repayments remain allowed, giving affected users a fair chance to restore their account health before liquidators can act. This could be implemented by recording an `unpause_timestamp` in `PanicState`/bank config and having `check_pre_liquidation_condition_and_get_account_health` (or `validate_bank_state`) reject liquidation attempts until a configurable delay has elapsed since the last unpause.

### Proof of Concept
1. Group admin (or global fee admin) pauses a bank (or the whole protocol) via `configure_bank`/`panic_pause`.
2. During the pause, the real market price of a user's collateral drops such that their account would be liquidatable under live prices, but they cannot `deposit`, `repay`, or `withdraw` to fix it because all of these instructions revert with `BankPaused`/`ProtocolPaused`.
3. Admin calls `panic_unpause` (or `panic_unpause_permissionless` after expiry) / sets bank `operational_state` back to `Operational` — this takes effect immediately with no delay.
4. A liquidator bot, monitoring for this transaction, submits `lending_account_liquidate` in the same or next slot using live prices, successfully liquidating the account and collecting the liquidator/insurance fee before the user can react.

### Citations

**File:** programs/marginfi/src/state/panic_state.rs (L11-57)
```rust
impl PanicStateImpl for PanicState {
    fn pause(&mut self, current_timestamp: i64) -> MarginfiResult<()> {
        // Clear existing pause if expired
        self.unpause_if_expired(current_timestamp);

        // Reset daily count if needed
        if current_timestamp.saturating_sub(self.last_daily_reset_timestamp) >= DAILY_RESET_INTERVAL
        {
            self.daily_pause_count = 0;
            self.last_daily_reset_timestamp = current_timestamp;
        }

        require!(
            self.can_pause(current_timestamp),
            MarginfiError::PauseLimitExceeded
        );

        // If already paused and not expired, treats this as an "extend" operation.
        if self.is_paused_flag() && !self.is_expired(current_timestamp) {
            self.pause_start_timestamp = self
                .pause_start_timestamp
                .saturating_add(Self::PAUSE_DURATION_SECONDS);
        } else {
            // Otherwise, we just start a new pause here
            self.pause_start_timestamp = current_timestamp;
        }

        self.pause_flags |= Self::FLAG_PAUSED;
        self.daily_pause_count = self.daily_pause_count.saturating_add(1);
        self.consecutive_pause_count = self.consecutive_pause_count.saturating_add(1);

        Ok(())
    }

    fn unpause(&mut self) {
        self.pause_flags &= !Self::FLAG_PAUSED;
        self.pause_start_timestamp = 0;
        self.consecutive_pause_count = 0;
    }

    /// No-op if not paused, or paused but time has not yet expired.
    fn unpause_if_expired(&mut self, current_timestamp: i64) {
        if self.is_paused_flag() && self.is_expired(current_timestamp) {
            self.unpause();
        }
    }
}
```

**File:** programs/marginfi/src/instructions/marginfi_group/panic_unpause.rs (L7-37)
```rust
pub fn panic_unpause(ctx: Context<PanicUnpause>) -> Result<()> {
    let mut fee_state = ctx.accounts.fee_state.load_mut()?;
    let current_timestamp = Clock::get()?.unix_timestamp;

    require!(
        fee_state.panic_state.is_paused_flag(),
        crate::errors::MarginfiError::ProtocolNotPaused
    );

    fee_state.panic_state.unpause_if_expired(current_timestamp);

    if fee_state.panic_state.is_paused_flag() {
        fee_state.panic_state.unpause();
        msg!(
            "Protocol manually unpaused by admin at timestamp: {}",
            current_timestamp
        );
    } else {
        msg!(
            "Protocol was already auto-unpaused due to expiration at timestamp: {}",
            current_timestamp
        );
    }

    msg!(
        "Consecutive pause count reset to: {}",
        fee_state.panic_state.consecutive_pause_count
    );

    Ok(())
}
```

**File:** programs/marginfi/src/instructions/marginfi_group/panic_unpause_permissionless.rs (L6-29)
```rust
pub fn panic_unpause_permissionless(ctx: Context<PanicUnpausePermissionless>) -> Result<()> {
    let mut fee_state = ctx.accounts.fee_state.load_mut()?;
    let current_timestamp = Clock::get()?.unix_timestamp;

    require!(
        fee_state.panic_state.is_paused_flag(),
        crate::errors::MarginfiError::ProtocolNotPaused
    );

    require!(
        fee_state.panic_state.is_expired(current_timestamp),
        crate::errors::MarginfiError::PauseLimitExceeded
    );

    msg!(
        "Permissionlessly unpaused at: {} (expired {}s)",
        current_timestamp,
        current_timestamp - fee_state.panic_state.pause_start_timestamp
    );

    fee_state.panic_state.unpause();

    Ok(())
}
```

**File:** programs/marginfi/tests/admin_actions/actions_during_pause.rs (L221-283)
```rust
/// Permissionless liquidation still fails during pause.
#[tokio::test]
async fn liquidation_still_blocked_during_pause() -> anyhow::Result<()> {
    let test_f = TestFixture::new(Some(TestSettings::all_banks_payer_not_admin())).await;
    let authority = Keypair::new();
    let risk_admin = test_f.payer().clone();

    let lp = test_f.create_marginfi_account().await;
    let liquidatee = MarginfiAccountFixture::new_with_authority(
        test_f.context.clone(),
        &test_f.marginfi_group.key,
        &authority,
    )
    .await;

    let sol_bank = test_f.get_bank(&BankMint::Sol);
    let usdc_bank = test_f.get_bank(&BankMint::Usdc);

    // LP provides liquidity
    let lp_usdc_acc = test_f.usdc_mint.create_token_account_and_mint_to(200).await;
    lp.try_bank_deposit(lp_usdc_acc.key, usdc_bank, 100, None)
        .await?;

    // Setup liquidatee: deposit SOL, borrow USDC
    let user_token_sol = test_f
        .sol_mint
        .create_token_account_and_mint_to_with_owner(&authority.pubkey(), 10)
        .await;
    let user_token_usdc = test_f
        .usdc_mint
        .create_empty_token_account_with_owner(&authority.pubkey())
        .await;

    liquidatee
        .try_bank_deposit_with_authority(user_token_sol.key, sol_bank, 3.0, None, &authority)
        .await?;
    liquidatee
        .try_bank_borrow_with_authority(user_token_usdc.key, usdc_bank, 20.0, 0, &authority)
        .await?;

    // Make account unhealthy
    sol_bank
        .update_config(
            BankConfigOpt {
                asset_weight_init: Some(I80F48!(0.5).into()),
                asset_weight_maint: Some(I80F48!(0.6).into()),
                ..Default::default()
            },
            None,
        )
        .await?;

    // Pause
    test_f.marginfi_group.try_panic_pause().await?;
    test_f.marginfi_group.try_propagate_fee_state().await?;

    // Attempt liquidation — start_liquidation itself doesn't check pause,
    // but the withdraw/repay inside the tx will fail.
    // Actually, start_liquidation has no pause check, but the key question is
    // whether a liquidator can execute a full liquidation.
    // Since withdraw checks pause and liquidation sets ACCOUNT_IN_RECEIVERSHIP
    // (not ACCOUNT_IN_DELEVERAGE), the withdraw inside should fail.
    let (record_pk, _) = Pubkey::find_program_address(
```

**File:** guides/ADMIN/BANK_STATE.md (L18-26)
```markdown
### Paused

All operations are halted. Users cannot deposit, borrow, withdraw, repay, or be liquidated. This is
the default state for newly created banks.

Use cases:
- Initial setup: configure the bank before allowing users to interact with it.
- Emergency: halt all activity on a bank while investigating an issue.

```

**File:** guides/ADMIN/BANK_STATE.md (L51-58)
```markdown
## Summary Table

| State | Deposit | Borrow | Withdraw | Repay | Liquidate | Initial Margin | Maintenance Margin |
|-------|---------|--------|----------|-------|-----------|----------------|--------------------|
| **Paused** | No | No | No | No | No | N/A | N/A |
| **Operational** | Yes | Yes | Yes | Yes | Yes | Full value | Full value |
| **ReduceOnly** | No | No | Yes | Yes | Yes | $0 | Full value |
| **KilledByBankruptcy** | No | No | No | No | No | N/A | N/A |
```

**File:** programs/marginfi/src/utils/general.rs (L276-298)
```rust
    match kind {
        InstructionKind::FailsInReduceState if bank.config.operational_state.is_reduce_only() => {
            return err!(MarginfiError::BankReduceOnly);
        }

        InstructionKind::FailsInPausedState
            if bank.config.operational_state == BankOperationalState::Paused =>
        {
            return err!(MarginfiError::BankPaused);
        }

        InstructionKind::FailsIfPausedOrReduceState
            if matches!(
                bank.config.operational_state,
                BankOperationalState::Paused
                    | BankOperationalState::ReduceOnly
                    | BankOperationalState::ReduceOnlyWithBorrowingPower
            ) =>
        {
            return match bank.config.operational_state {
                BankOperationalState::Paused => {
                    err!(MarginfiError::BankPaused)
                }
```

**File:** programs/marginfi/src/instructions/marginfi_account/liquidate.rs (L118-123)
```rust
    {
        let asset_bank = ctx.accounts.asset_bank.load()?;
        let liab_bank = ctx.accounts.liab_bank.load()?;
        validate_bank_asset_tags(&asset_bank, &liab_bank)?;
        validate_bank_state(&asset_bank, InstructionKind::FailsInPausedState)?;
        validate_bank_state(&liab_bank, InstructionKind::FailsInPausedState)?;
```

**File:** programs/marginfi/src/state/marginfi_account.rs (L903-961)
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

    let account_health = assets.checked_sub(liabs).ok_or_else(math_error!())?;
    let healthy = account_health > I80F48::ZERO;

    if let Some(cache) = health_cache.as_mut() {
        cache.set_healthy(healthy);
    }

    if healthy && !ignore_healthy {
        msg!(
            "pre_liquidation_health: {} ({} - {})",
            account_health,
            assets,
            liabs
        );
        return err!(MarginfiError::HealthyAccount);
    }

    Ok((account_health, assets, liabs))
```

**File:** guides/RISK_AND_LIQUIDATORS/GETTING_STARTED_RISK.md (L278-282)
```markdown

```
