## Finding [1](#0-0) [2](#0-1) [3](#0-2) 

### Title
`lending_pool_clone_emode` copies emode entries without re-validating them against the destination bank's own liability weights, enabling emode-derived CW>=LW / leverage-cap bypass exploitable via permissionless borrow - (File: programs/marginfi/src/instructions/marginfi_group/emode_clone.rs)

### Summary
`lending_pool_clone_emode` directly overwrites `destination_bank.emode` with `source_bank.emode` and never calls `EmodeSettingsImpl::validate_entries_with_liability_weights` against `copy_to_bank.config`. Every other emode-mutating path (`lending_pool_configure_bank_emode` and `lending_pool_configure_bank`) invokes this validation, but the clone path does not, so the CW<LW and leverage-cap invariants can be silently violated on the destination bank.

### Finding Description
`lending_pool_configure_bank_emode` and `lending_pool_configure_bank` both re-run `bank.emode.validate_entries_with_liability_weights(&bank.config, group.emode_max_init_leverage, group.emode_max_maint_leverage)` any time emode entries or bank weights change, ensuring `asset_weight_init < liability_weight_init`, `asset_weight_maint < liability_weight_maint`, and the leverage bound `1/(1-CW/LW) <= emode_max_*_leverage` for that specific bank's own `BankConfig`, as seen in `programs/marginfi/src/state/emode.rs:82-137` and `programs/marginfi/src/instructions/marginfi_group/configure_bank.rs:47-52`.

`lending_pool_clone_emode`, however, simply does:
```
destination_bank.emode = source_bank.emode;
```
with no call to `validate_entries_with_liability_weights` against `copy_to_bank.config`. If `source_bank` and `copy_to_bank` have different `liability_weight_init`/`liability_weight_maint`, entries that were valid for `source_bank` (CW<LW there) can fail that same check against `copy_to_bank` — e.g. CW>=LW for `copy_to_bank`, or leverage exceeding `group.emode_max_init_leverage`/`emode_max_maint_leverage`.

At runtime, `Bank::get_asset_weight` (`type-crate/src/types/bank.rs:198-217`) computes `max(bank_weight, emode_weight)` for any collateral bank whose `emode_tag` matches an entry in the reconciled emode config drawn from banks being borrowed (via `reconcile_emode_configs`/`EmodeConfigIterator`, `programs/marginfi/src/state/marginfi_account.rs:644-650`). There is no runtime re-check that this weight stays below the liability weight of the bank being borrowed — that invariant is enforced *only* at configuration time. Since the clone path skips that check entirely for the destination bank, an emode entry with CW>=LW (or excess leverage) can be attached to `copy_to_bank` and then applied by the risk engine on any `lending_account_borrow` against `copy_to_bank`, without any additional gate.

### Impact Explanation
If CW>=LW holds for the applied emode weight vs. `copy_to_bank`'s liability weight, the health equation `collateral_value * asset_weight >= liability_value * liability_weight` can never be violated regardless of borrow size (CW==LW gives flat/neutral health regardless of amount, CW>LW makes health improve with more borrowing). This permits an unprivileged borrower to open an effectively unbounded/undercollateralized borrow position against `copy_to_bank`, directly financially exploitable and bypassing the leverage cap (`emode_max_init_leverage`/`emode_max_maint_leverage`) that the protocol otherwise strictly enforces at every other emode-mutation entrypoint.

### Likelihood Explanation
This requires only that the group `admin`/`emode_admin` at some point run `lending_pool_configure_bank_emode` on some bank A (valid for A's own weights) and then run the documented `lending_pool_clone_emode` to copy A's emode settings to bank B "useful when applying emode settings from e.g. one LST to another" (per the doc comment in `lib.rs`), where A and B have different liability weights. This is a normal/intended admin workflow (not malicious), and once triggered, any unprivileged user can call the permissionless `lending_account_borrow` instruction against bank B to exploit the resulting undercollateralized setup — no admin cooperation is needed for the exploitation step itself.

### Recommendation
In `lending_pool_clone_emode`, after `destination_bank.emode = source_bank.emode`, call `destination_bank.emode.validate_entries_with_liability_weights(&destination_bank.config, group.emode_max_init_leverage, group.emode_max_maint_leverage)?` (loading `group` fields needed), mirroring the check already performed in `lending_pool_configure_bank_emode` and `lending_pool_configure_bank`, and reject the clone if the destination bank's own weights make the copied entries invalid.

### Proof of Concept
Rust integration test plan (in `programs/marginfi/tests/admin_actions/setup_bank.rs` style):
1. Create bank A with `liability_weight_init = 1.5`, `liability_weight_maint = 1.5`; configure emode entry with `asset_weight_init = 1.4`, `asset_weight_maint = 1.45` (valid for A, leverage within cap) via `try_lending_pool_configure_bank_emode`.
2. Create bank B with tighter weights, e.g. `liability_weight_init = 1.0`, `liability_weight_maint = 1.0` (so A's entry, if applied to B, gives CW(1.4) >= LW(1.0)).
3. Call `try_lending_pool_clone_emode(&bank_A, &bank_B)` as `admin`/`emode_admin` — assert it currently succeeds (`res.is_ok()`), proving no re-validation occurs against B's own config.
4. Assert `bank_B.emode` now holds an entry that would fail `validate_entries_with_liability_weights(&bank_B.config, ...)` if it were called directly (call the function manually in the test and assert `is_err()` / `MaxInitLeverageExceeded`/`BadEmodeConfig`).
5. Set up a depositor with the tagged collateral bank and an attacker account; have the attacker deposit that collateral and call `lending_account_borrow` against bank B for a large amount; assert the health check passes despite liability_value*1.0 >= collateral_value*1.4 (i.e., assert the borrow succeeds even though it should be rejected under the CW<LW invariant), demonstrating the undercollateralized borrow.
6. Expected fix assertion: after adding the validation call in `lending_pool_clone_emode`, step 3 should return `Err` with `MarginfiError::BadEmodeConfig` or `MaxInitLeverageExceeded`, preventing step 5 from ever becoming reachable.

### Citations

**File:** programs/marginfi/src/instructions/marginfi_group/emode_clone.rs (L6-26)
```rust
pub fn lending_pool_clone_emode(ctx: Context<LendingPoolCloneEmode>) -> MarginfiResult {
    let group = ctx.accounts.group.load()?;

    check!(
        ctx.accounts.signer.key() == group.admin || ctx.accounts.signer.key() == group.emode_admin,
        MarginfiError::Unauthorized
    );

    let source_bank = ctx.accounts.copy_from_bank.load()?;
    let mut destination_bank = ctx.accounts.copy_to_bank.load_mut()?;

    destination_bank.emode = source_bank.emode;

    msg!(
        "emode settings copied from {:?} to {:?}",
        ctx.accounts.copy_from_bank.key(),
        ctx.accounts.copy_to_bank.key()
    );

    Ok(())
}
```

**File:** programs/marginfi/src/state/emode.rs (L76-144)
```rust
impl EmodeSettingsImpl for EmodeSettings {
    fn validate_entries_with_liability_weights(
        &self,
        bank_config: &BankConfig,
        emode_max_init_leverage: u32,
        emode_max_maint_leverage: u32,
    ) -> MarginfiResult {
        let liab_init_w: I80F48 = bank_config.get_weight(
            RequirementType::Initial,
            marginfi_type_crate::types::BalanceSide::Liabilities,
        );
        let liab_maint_w: I80F48 = bank_config.get_weight(
            RequirementType::Maintenance,
            marginfi_type_crate::types::BalanceSide::Liabilities,
        );

        let max_allowed_init_leverage: I80F48 = u32_to_basis(emode_max_init_leverage);
        let max_allowed_maint_leverage: I80F48 = u32_to_basis(emode_max_maint_leverage);

        for entry in self.emode_config.entries {
            if entry.is_empty() {
                continue;
            }
            let asset_init_w: I80F48 = I80F48::from(entry.asset_weight_init);
            let asset_maint_w: I80F48 = I80F48::from(entry.asset_weight_maint);

            // Basic sanity checks
            check!(
                asset_init_w >= I80F48::ZERO,
                MarginfiError::BadEmodeConfig,
                "emode entry tag {}: asset_init_w ({}) must be >= 0",
                entry.collateral_bank_emode_tag,
                asset_init_w
            );
            check!(
                asset_maint_w >= asset_init_w,
                MarginfiError::BadEmodeConfig,
                "emode entry tag {}: asset_maint_w ({}) must be >= asset_init_w ({})",
                entry.collateral_bank_emode_tag,
                asset_maint_w,
                asset_init_w
            );

            let max_leverage_init = calculate_max_leverage(asset_init_w, liab_init_w)?;
            check!(
                max_leverage_init <= max_allowed_init_leverage,
                MarginfiError::MaxInitLeverageExceeded,
                "emode entry tag {}: init leverage ({}) exceeds max allowed ({})",
                entry.collateral_bank_emode_tag,
                max_leverage_init,
                max_allowed_init_leverage
            );

            let max_leverage_maint = calculate_max_leverage(asset_maint_w, liab_maint_w)?;
            check!(
                max_leverage_maint <= max_allowed_maint_leverage,
                MarginfiError::MaxMaintLeverageExceeded,
                "emode entry tag {}: maint leverage ({}) exceeds max allowed ({})",
                entry.collateral_bank_emode_tag,
                max_leverage_maint,
                max_allowed_maint_leverage
            );
        }

        // Validate that no duplicates exist (other than EMODE_TAG_EMPTY - 0)
        self.check_dupes()?;

        Ok(())
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
