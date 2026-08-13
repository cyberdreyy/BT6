### Title
Under-constrained `oracle_max_age` in `StakedSettings` bypasses `BankConfig::validate()` and can permanently DoS staked-collateral banks created via the permissionless `lending_pool_add_bank_permissionless` instruction - (File: `programs/marginfi/src/instructions/marginfi_group/add_pool_permissionless.rs`)

### Summary
The report describes unbounded/under-constrained config variables (`daStartTime`, `daPriceCurveLength`, etc.) that can be set without cross-field validation, leading to a permanent revert/DoS of a pricing function. The marginfi analog is `StakedSettings.oracle_max_age`, which is never bound-checked (unlike the equivalent `BankConfig.oracle_max_age`), and is consumed unchecked by the permissionless bank-creation path.

### Finding Description
`BankConfig::validate()` enforces a minimum oracle staleness window: `check!(self.oracle_max_age >= ORACLE_MIN_AGE, MarginfiError::InvalidOracleSetup)` [1](#0-0) . This check is invoked whenever a bank is configured via `configure()` [2](#0-1)  or `propagate_staked_settings` [3](#0-2) .

However, `StakedSettings::validate()` has no equivalent check on `oracle_max_age` at all — it only validates asset weights: [4](#0-3) . This same unvalidated `StakedSettingsImpl::validate()` is used by both `init_staked_settings` (admin) [5](#0-4)  and `edit_staked_settings` (admin) [6](#0-5) , meaning `oracle_max_age` (including `0`, the struct default) can persist in `StakedSettings` without ever being bound-checked.

The critical gap is `lending_pool_add_bank_permissionless`, an instruction explicitly callable by any unprivileged user (any validator/user "to add their stake pool to a group"). It copies `settings.oracle_max_age` straight into the new bank's `BankConfigCompact` and constructs the `Bank` — with **no subsequent call to `bank.config.validate()`** anywhere in the function, unlike every other bank-mutation path in the codebase: [7](#0-6) .

Separately, `get_oracle_max_age()` only special-cases a `0` value for `OracleSetup::PythPushOracle` (mapping it to `MAX_PYTH_ORACLE_AGE`); for `OracleSetup::StakedWithPythPush` (which is exactly what `lending_pool_add_bank_permissionless` sets, `bank.config.oracle_setup = OracleSetup::StakedWithPythPush` [8](#0-7) ), a `0` value is passed through literally: [9](#0-8) .

If `StakedSettings.oracle_max_age` is ever `0` (its zero-value default, or set via `init_staked_settings`/`edit_staked_settings` without the admin realizing there is no lower-bound enforcement), any unprivileged caller invoking `lending_pool_add_bank_permissionless` creates a permanently broken staked-collateral bank whose effective oracle max age is `0` — every oracle price load will immediately treat the price as stale.

### Impact Explanation
A bank with `get_oracle_max_age() == 0` will fail all price-freshness checks used in deposit/withdraw/borrow/liquidate/health-check flows for that bank, since `current_timestamp - price_timestamp` will essentially always exceed `0`. This durably freezes any staked-collateral bank created through this path: users cannot deposit meaningfully (asset is unusable as collateral), and if any funds are already deposited to that specific bank instance, borrow/liquidation flows requiring pricing of that bank become permanently unavailable. This is a durable freeze/DoS with financial effect on the affected bank, matching the report's "cause the price function to revert" impact class.

### Likelihood Explanation
The trigger requires only that the group's shared `StakedSettings.oracle_max_age` be `0` at the time `lending_pool_add_bank_permissionless` is called — a state reachable either by admin oversight (default is `0` and there's no validation to catch it) or simply before an admin has explicitly set a non-zero value. The bank-creation call itself is permissionless and requires no special authority, so any user can trigger bank creation once this condition holds, without needing any admin cooperation for the exploit step itself.

### Recommendation
Add the same `oracle_max_age >= ORACLE_MIN_AGE` bound check to `StakedSettingsImpl::validate()` as exists in `BankConfigImpl::validate()`, and additionally call `bank.config.validate()` at the end of `lending_pool_add_bank_permissionless` (as is done in every other config-mutating path) so that a defensive check exists at bank-creation time regardless of the state of `StakedSettings`.

### Proof of Concept
1. Admin calls `initialize_staked_settings` without setting `oracle_max_age` (or explicitly sets it to `0`); `StakedSettings::validate()` at `programs/marginfi/src/state/staked_settings.rs:14-33` passes since it never inspects `oracle_max_age`.
2. Any unprivileged user calls `lending_pool_add_bank_permissionless` (`programs/marginfi/src/instructions/marginfi_group/add_pool_permissionless.rs:28-120`), which sets `default_config.oracle_max_age = settings.oracle_max_age` (`= 0`) and `bank.config.oracle_setup = OracleSetup::StakedWithPythPush`, with no `bank.config.validate()` call anywhere in the instruction.
3. The new bank is created with an effective `get_oracle_max_age() == 0` (per the match arm at `programs/marginfi/src/state/bank_config.rs:122-128`, which only rescues `PythPushOracle`, not `StakedWithPythPush`).
4. Any subsequent operation requiring a fresh oracle price for this bank (deposit, borrow, withdraw, liquidate, health checks) fails, permanently freezing the bank's usability.

### Citations

**File:** programs/marginfi/src/state/bank_config.rs (L78-81)
```rust
        check!(
            self.oracle_max_age >= ORACLE_MIN_AGE,
            MarginfiError::InvalidOracleSetup
        );
```

**File:** programs/marginfi/src/state/bank_config.rs (L122-128)
```rust
    #[inline]
    fn get_oracle_max_age(&self) -> u64 {
        match (self.oracle_max_age, self.oracle_setup) {
            (0, OracleSetup::PythPushOracle) => MAX_PYTH_ORACLE_AGE,
            (n, _) => n as u64,
        }
    }
```

**File:** programs/marginfi/src/state/bank.rs (L494-494)
```rust
        self.config.validate()?;
```

**File:** programs/marginfi/src/instructions/marginfi_group/propagate_staked_settings.rs (L32-32)
```rust
    bank.config.validate()?;
```

**File:** programs/marginfi/src/state/staked_settings.rs (L14-33)
```rust
    fn validate(&self) -> MarginfiResult {
        let asset_init_w = I80F48::from(self.asset_weight_init);
        let asset_maint_w = I80F48::from(self.asset_weight_maint);

        check!(
            asset_init_w >= I80F48::ZERO && asset_init_w <= I80F48::ONE,
            MarginfiError::InvalidConfig
        );
        check!(asset_maint_w >= asset_init_w, MarginfiError::InvalidConfig);
        check!(
            asset_maint_w <= (I80F48::ONE + I80F48::ONE),
            MarginfiError::InvalidConfig
        );
        if self.risk_tier == RiskTier::Isolated {
            check!(asset_init_w == I80F48::ZERO, MarginfiError::InvalidConfig);
            check!(asset_maint_w == I80F48::ZERO, MarginfiError::InvalidConfig);
        }

        Ok(())
    }
```

**File:** programs/marginfi/src/instructions/marginfi_group/init_staked_settings.rs (L11-47)
```rust
pub fn initialize_staked_settings(
    ctx: Context<InitStakedSettings>,
    settings: StakedSettingsConfig,
) -> Result<()> {
    let mut staked_settings = ctx.accounts.staked_settings.load_init()?;

    *staked_settings = StakedSettings::new(
        ctx.accounts.staked_settings.key(),
        ctx.accounts.marginfi_group.key(),
        settings.oracle,
        settings.asset_weight_init,
        settings.asset_weight_maint,
        settings.deposit_limit,
        settings.total_asset_value_init_limit,
        settings.oracle_max_age,
        settings.risk_tier,
    );

    msg!(
        "oracle: {:?} max age: {:?}",
        staked_settings.oracle,
        staked_settings.oracle_max_age
    );
    let init_f64: f64 = wrapped_i80f48_to_f64(staked_settings.asset_weight_init);
    let maint_f64: f64 = wrapped_i80f48_to_f64(staked_settings.asset_weight_maint);
    msg!("asset weight init: {:?} maint: {:?}", init_f64, maint_f64);
    msg!(
        "deposit limit: {:?} value limit: {:?} risk tier: {:?}",
        staked_settings.deposit_limit,
        staked_settings.total_asset_value_init_limit,
        staked_settings.risk_tier as u8
    );

    staked_settings.validate()?;

    Ok(())
}
```

**File:** programs/marginfi/src/instructions/marginfi_group/edit_stake_settings.rs (L10-44)
```rust
pub fn edit_staked_settings(
    ctx: Context<EditStakedSettings>,
    settings: StakedSettingsEditConfig,
) -> Result<()> {
    // let group = ctx.accounts.marginfi_group.load()?;
    let mut staked_settings = ctx.accounts.staked_settings.load_mut()?;
    // require_keys_eq!(group.admin, ctx.accounts.admin.key());

    set_if_some!(staked_settings.oracle, settings.oracle);

    set_if_some!(
        staked_settings.asset_weight_init,
        settings.asset_weight_init
    );
    set_if_some!(
        staked_settings.asset_weight_maint,
        settings.asset_weight_maint
    );
    set_if_some!(staked_settings.deposit_limit, settings.deposit_limit);
    set_if_some!(
        staked_settings.total_asset_value_init_limit,
        settings.total_asset_value_init_limit
    );
    set_if_some!(staked_settings.oracle_max_age, settings.oracle_max_age);
    set_if_some!(staked_settings.risk_tier, settings.risk_tier);

    staked_settings.validate()?;

    emit!(EditStakedSettingsEvent {
        group: ctx.accounts.marginfi_group.key(),
        settings
    });

    Ok(())
}
```

**File:** programs/marginfi/src/instructions/marginfi_group/add_pool_permissionless.rs (L72-120)
```rust
    let default_config: BankConfigCompact = BankConfigCompact {
        asset_weight_init: settings.asset_weight_init,
        asset_weight_maint: settings.asset_weight_maint,
        liability_weight_init: I80F48!(1.5).into(), // placeholder
        liability_weight_maint: I80F48!(1.25).into(), // placeholder
        deposit_limit: settings.deposit_limit,
        interest_rate_config: default_ir_config.into(), // placeholder
        operational_state: BankOperationalState::Operational,
        borrow_limit: 0,
        risk_tier: settings.risk_tier,
        asset_tag: ASSET_TAG_STAKED,
        config_flags: PYTH_PUSH_MIGRATED_DEPRECATED,
        _pad0: [0; 5],
        total_asset_value_init_limit: settings.total_asset_value_init_limit,
        oracle_max_age: settings.oracle_max_age,
        // Note: this will use the default of 10%. SOL oracle confidence is generally fine.
        oracle_max_confidence: 0,
    };

    let now = Clock::get().unwrap().unix_timestamp;

    *bank = Bank::new(
        ctx.accounts.marginfi_group.key(),
        default_config.into(),
        bank_mint.key(),
        bank_mint.decimals,
        liquidity_vault.key(),
        insurance_vault.key(),
        fee_vault.key(),
        now,
        liquidity_vault_bump,
        liquidity_vault_authority_bump,
        insurance_vault_bump,
        insurance_vault_authority_bump,
        fee_vault_bump,
        fee_vault_authority_bump,
        bank_seed,
    );
    bank.flags |= BANK_SEED_KNOWN;
    bank.flags |= settings.flags & STAKED_ORACLE_FLAGS;
    if bank_mint.to_account_info().owner == &anchor_spl::token_2022::ID {
        bank.flags |= IS_T22;
    }
    bank.config.oracle_setup = OracleSetup::StakedWithPythPush;
    bank.config.oracle_keys[0] = settings.oracle;

    log_pool_info(&bank);

    group.add_bank()?;
```
