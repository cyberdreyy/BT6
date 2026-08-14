Confirmed: `validate_entries_with_liability_weights` is only invoked from `lending_pool_configure_bank_emode` [1](#0-0)  and `lending_pool_configure_bank` [2](#0-1) , but never from the group-level `configure` function that actually changes `emode_max_init_leverage`/`emode_max_maint_leverage` [3](#0-2) . This is a direct analog of the reported bug class: a top-level parameter update (group emode leverage caps) is not propagated/re-validated against dependent state (existing bank emode entries).

### Title
Group-level emode leverage cap tightening is not propagated/re-validated against existing bank emode entries - ([File: programs/marginfi/src/instructions/marginfi_group/configure.rs])

### Summary
`configure()` allows the group admin to update `emode_max_init_leverage` and `emode_max_maint_leverage` at the `MarginfiGroup` level [3](#0-2) . This top-level parameter change is never propagated to, or re-validated against, banks that already have `emode` entries configured under the previous (looser) leverage caps.

### Finding Description
When a bank's e-mode entries are set via `lending_pool_configure_bank_emode`, the entries are validated against the group's *current* `emode_max_init_leverage`/`emode_max_maint_leverage` at that moment, using `validate_entries_with_liability_weights` [4](#0-3) . The same re-validation is also performed whenever the bank's own liability weights change via `lending_pool_configure_bank` [5](#0-4) .

However, the group-level `configure` instruction, which is the only place `emode_max_init_leverage`/`emode_max_maint_leverage` are actually changed, performs no iteration over the group's banks and no call to `validate_entries_with_liability_weights` [6](#0-5) . Consequently, if the risk/emode admin later tightens the group leverage caps (e.g., from 20x to 10x) to reduce protocol risk, any bank whose e-mode entries were validated and stored under the old, looser cap remains untouched and keeps operating at the old (now disallowed) leverage.

At runtime, `Bank::get_asset_weight` reads the bank's stored e-mode entries directly (via `emode_config.find_with_tag`) with no re-check against the group's current leverage caps [7](#0-6) , and this weight feeds directly into health/risk calculations used for borrowing and liquidation decisions [8](#0-7) . Thus the group's declared risk ceiling silently diverges from the actual effective leverage still exposed via stale bank e-mode configs, matching the reported bug class of "parameter updates not propagated to underlying contracts/state."

### Impact Explanation
This is a durable state inconsistency with financial-risk consequences: the on-chain group parameter (`emode_max_init_leverage`/`emode_max_maint_leverage`) no longer reflects the actual maximum leverage obtainable through e-mode on banks configured before the tightening. Users can continue to borrow/maintain positions at leverage levels the group admin explicitly intended to disallow, undermining the protocol's risk controls (e.g., after a market-stress event prompts admins to lower leverage caps, previously-configured banks remain exposed at the old, riskier leverage until someone manually re-runs `lending_pool_configure_bank_emode` or `lending_pool_configure_bank` on every affected bank).

### Likelihood Explanation
Likelihood is moderate-to-high in practice: tightening `emode_max_init_leverage`/`emode_max_maint_leverage` is a plausible admin action during risk-parameter adjustments, and there is no automatic mechanism (unlike `propagate_staked_settings`/`propagate_fee_state` used elsewhere in this codebase for other cross-account settings) to re-validate or auto-correct already-configured banks. The bug is purely a missing re-validation step, requiring no attacker action — simply an admin operation followed by inaction on stale banks.

### Recommendation
When updating `emode_max_init_leverage`/`emode_max_maint_leverage` in `configure()`, either: (1) require the caller to pass the set of affected banks and re-run `validate_entries_with_liability_weights` against each, failing the transaction if any bank now exceeds the new cap; or (2) add a permissionless "propagate" instruction analogous to `propagate_staked_settings`/`propagate_fee_state` that re-validates (and optionally disables) a bank's e-mode entries against the current group leverage caps, and document/enforce that admins must invoke it for every bank after tightening the group leverage limits.

### Proof of Concept
1. Group admin calls `configure` with `emode_max_init_leverage = 20x`, `emode_max_maint_leverage = 25x` (loose caps).
2. Emode admin calls `lending_pool_configure_bank_emode` on `BANK_A` with entries producing exactly 19x/24x leverage — passes validation against the loose caps [1](#0-0) .
3. Group admin later calls `configure` again, tightening caps to `emode_max_init_leverage = 5x`, `emode_max_maint_leverage = 8x` to reduce protocol risk — this succeeds with no check against `BANK_A`'s existing entries [3](#0-2) .
4. `BANK_A.emode` still contains the 19x/24x entries on-chain; `Bank::get_asset_weight` continues to apply them unchanged [7](#0-6) .
5. Users continue to borrow/maintain positions against `BANK_A` at ~19x/24x leverage — far exceeding the group's newly declared 5x/8x risk ceiling — until an admin manually re-invokes `lending_pool_configure_bank_emode` on `BANK_A` to force re-validation.

### Citations

**File:** programs/marginfi/src/instructions/marginfi_group/config_bank_emode.rs (L25-33)
```rust
    bank.emode.emode_tag = emode_tag;
    bank.emode.emode_config.entries = sorted_entries;
    bank.emode.timestamp = Clock::get()?.unix_timestamp;

    bank.emode.validate_entries_with_liability_weights(
        &bank.config,
        group.emode_max_init_leverage,
        group.emode_max_maint_leverage,
    )?;
```

**File:** programs/marginfi/src/instructions/marginfi_group/configure_bank.rs (L42-52)
```rust
    } else {
        // Settings are not frozen, everything updates
        bank.configure(&bank_config)?;
        msg!("Bank configured!");

        let group = ctx.accounts.group.load()?;
        bank.emode.validate_entries_with_liability_weights(
            &bank.config,
            group.emode_max_init_leverage,
            group.emode_max_maint_leverage,
        )?;
```

**File:** programs/marginfi/src/instructions/marginfi_group/configure.rs (L36-99)
```rust
pub fn configure(
    ctx: Context<MarginfiGroupConfigure>,
    new_admin: Option<Pubkey>,
    new_emode_admin: Option<Pubkey>,
    new_curve_admin: Option<Pubkey>,
    new_limit_admin: Option<Pubkey>,
    new_flow_admin: Option<Pubkey>,
    new_emissions_admin: Option<Pubkey>,
    new_metadata_admin: Option<Pubkey>,
    new_risk_admin: Option<Pubkey>,
    emode_max_init_leverage: Option<WrappedI80F48>,
    emode_max_maint_leverage: Option<WrappedI80F48>,
) -> MarginfiResult {
    let marginfi_group = &mut ctx.accounts.marginfi_group.load_mut()?;
    if let Some(new_admin) = new_admin {
        marginfi_group.update_admin(new_admin);
    }
    if let Some(new_emode_admin) = new_emode_admin {
        marginfi_group.update_emode_admin(new_emode_admin);
    }
    if let Some(new_curve_admin) = new_curve_admin {
        marginfi_group.update_curve_admin(new_curve_admin);
    }
    if let Some(new_limit_admin) = new_limit_admin {
        marginfi_group.update_limit_admin(new_limit_admin);
    }
    if let Some(new_flow_admin) = new_flow_admin {
        marginfi_group.update_flow_admin(new_flow_admin);
    }
    if let Some(new_emissions_admin) = new_emissions_admin {
        marginfi_group.update_emissions_admin(new_emissions_admin);
    }
    if let Some(new_metadata_admin) = new_metadata_admin {
        marginfi_group.update_metadata_admin(new_metadata_admin);
    }
    if let Some(new_risk_admin) = new_risk_admin {
        marginfi_group.update_risk_admin(new_risk_admin);
    }

    validate_and_apply_emode_leverage(
        emode_max_init_leverage,
        &mut marginfi_group.emode_max_init_leverage,
    )?;
    validate_and_apply_emode_leverage(
        emode_max_maint_leverage,
        &mut marginfi_group.emode_max_maint_leverage,
    )?;

    // Validate that init < maint
    if marginfi_group.emode_max_init_leverage >= marginfi_group.emode_max_maint_leverage {
        msg!(
            "emode init leverage ({}) must be < maint leverage ({})",
            marginfi_group.emode_max_init_leverage,
            marginfi_group.emode_max_maint_leverage
        );
        return Err(MarginfiError::BadEmodeConfig.into());
    }
    // The fuzzer should ignore this because the "Clock" mock sysvar doesn't load until after the
    // group is init. Eventually we might fix the fuzzer to load the clock first...
    #[cfg(not(feature = "client"))]
    {
        let clock = Clock::get()?;
        marginfi_group.fee_state_cache.last_update = clock.unix_timestamp;
    }
```

**File:** type-crate/src/types/bank.rs (L198-217)
```rust
    pub fn get_asset_weight(
        &self,
        requirement_type: RequirementType,
        emode_config: &EmodeConfig,
    ) -> I80F48 {
        if let Some(emode_entry) = emode_config.find_with_tag(self.emode.emode_tag) {
            let bank_weight = self
                .config
                .get_weight(requirement_type, BalanceSide::Assets);
            let emode_weight = match requirement_type {
                RequirementType::Initial => I80F48::from(emode_entry.asset_weight_init),
                RequirementType::Maintenance => I80F48::from(emode_entry.asset_weight_maint),
                RequirementType::Equity => I80F48::ONE,
            };
            max(bank_weight, emode_weight)
        } else {
            self.config
                .get_weight(requirement_type, BalanceSide::Assets)
        }
    }
```

**File:** programs/marginfi/src/state/marginfi_account.rs (L1296-1296)
```rust
            let mut asset_weight = bank.get_asset_weight(requirement_type, emode_config);
```
