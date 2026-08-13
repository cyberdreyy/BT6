### Title
`lending_pool_clone_emode` copies `EmodeEntry` weights without re-validating against `destination_bank`'s own liability weights, enabling a phantom collateral state usable by unprivileged borrowers - (File: `programs/marginfi/src/instructions/marginfi_group/emode_clone.rs`)

### Summary
`lending_pool_clone_emode` performs `destination_bank.emode = source_bank.emode` and returns without calling `validate_entries_with_liability_weights` against `destination_bank.config`, unlike its sibling instructions `lending_pool_configure_bank_emode` and `lending_pool_configure_bank`, both of which always re-check the invariant after any change. If `copy_to_bank`'s own liability weights differ from `copy_from_bank`'s (a realistic case, since the guide explicitly recommends cloning "from e.g. one LST to another"), the copied `asset_weight_init`/`asset_weight_maint` can exceed `copy_to_bank`'s own liability weight immediately at clone time, breaking the CW < LW invariant for that bank.

### Finding Description
`lending_pool_clone_emode` in [1](#0-0)  loads `source_bank` and `destination_bank` and does a raw struct copy of the `emode` field with no subsequent validation call. Compare this to `lending_pool_configure_bank_emode`, which always calls `bank.emode.validate_entries_with_liability_weights(&bank.config, group.emode_max_init_leverage, group.emode_max_maint_leverage)` after mutating emode entries, at [2](#0-1) , and `lending_pool_configure_bank`, which re-runs the same validation whenever `bank.config` changes, at [3](#0-2) .

`validate_entries_with_liability_weights` in [4](#0-3)  checks each entry's `asset_weight_init`/`asset_weight_maint` against `bank_config.get_weight(..., BalanceSide::Liabilities)` — i.e., the **liability weight of the bank the entries live on** — and enforces `CW < LW` plus the group's `emode_max_init_leverage`/`emode_max_maint_leverage` caps. This is exactly the invariant the question describes ("PRICE_CONSERVATISM: CW < LW"). Because `lending_pool_clone_emode` never invokes this check against `destination_bank.config`, entries validated only against `source_bank`'s liability weight get installed onto `destination_bank` unchecked.

The copied emode entries have immediate, permissionless runtime effect. `get_health_components` reconciles all counterparty banks' emode configs via `EmodeConfigIterator`/`reconcile_emode_configs` and then calls `bank.get_asset_weight(requirement_type, emode_config)` at [5](#0-4) . `Bank::get_asset_weight` in [6](#0-5)  does `max(bank_weight, emode_weight)` — it only guards against emode being *worse* than the bank's own base weight, it does **not** clamp emode weight to be below the *liability* bank's liability weight. So if `destination_bank`'s stale/uninvalidated `asset_weight_maint` (inherited from `source_bank`) is ≥ `destination_bank.config.liability_weight_maint`, any unprivileged depositor collateralizing a matching-tag bank and borrowing from `destination_bank` gets an overstated collateral valuation / understated leverage cap, with no further admin action required — the vulnerable state exists the instant the clone completes.

Note: the precondition in the question that specifically requires *later* tightening of `copy_from_bank`'s liability weight is not the actual trigger — since the emode struct is copied by value at clone time, subsequent changes to `copy_from_bank.config` have no effect on the already-cloned `copy_to_bank.emode`. The real, simpler trigger is a liability-weight mismatch between `copy_from_bank` and `copy_to_bank` that already exists (or is introduced) at/before the moment of cloning.

### Impact Explanation
This allows an unprivileged user to obtain understated liability / overstated collateral valuation when borrowing against `copy_to_bank`, effectively exceeding the group's configured `emode_max_init_leverage`/`emode_max_maint_leverage` and potentially violating `asset_weight < liability_weight` for that bank — a direct financial-impact misvaluation of collateral/borrowing power, not a pure liquidity or oracle issue, and reachable via ordinary deposit/borrow instructions with no elevated privileges.

### Likelihood Explanation
Requires: (1) an emode_admin/admin performing the documented, intended `lending_pool_clone_emode` action between two banks in the same group whose liability weights differ (an explicitly encouraged use-case per `EMODE_ADMIN.md`, e.g., cloning LST-to-LST settings), and (2) an ordinary user depositing/borrowing afterward. No malicious admin behavior is needed — a legitimate, expected admin action combined with the missing invariant check is sufficient, making this readily reproducible.

### Recommendation
Add the same `bank.emode.validate_entries_with_liability_weights(&bank.config, group.emode_max_init_leverage, group.emode_max_maint_leverage)` call on `destination_bank` (using `destination_bank.config`) after the copy in `lending_pool_clone_emode`, mirroring `lending_pool_configure_bank_emode`/`lending_pool_configure_bank`, and fail the instruction if validation fails.

### Proof of Concept
Rust integration test (extend `programs/marginfi/tests/admin_actions/setup_bank.rs`):
1. Create two banks in the same group, `bank_a` (liability_weight_init=1.0) and `bank_b` (liability_weight_init=1.5).
2. Call `try_lending_pool_configure_bank_emode(&bank_a, tag, [entry with asset_weight_init = 0.95])` — passes validation against `bank_a`'s LW=1.0 (CW<LW, leverage under cap).
3. Call `try_lending_pool_clone_emode(&bank_a, &bank_b)` (clone `bank_a`'s emode onto `bank_b`).
4. Load `bank_b` and assert `bank_b.emode.emode_config.entries[0].asset_weight_init (0.95) < bank_b.config.liability_weight_init (1.5)` trivially holds here — instead pick source/destination LWs such that source LW is *lower* than destination's originally-intended safe margin (e.g., destination has LW=1.0 too but with a different, near-cap leverage entry) to directly demonstrate `MaxInitLeverageExceeded`/`BadEmodeConfig` would trigger if `validate_entries_with_liability_weights` were called on `bank_b`, but currently `try_lending_pool_clone_emode` succeeds (`res.is_ok()`) with no validation performed.
5. Assert the current code returns `Ok(())` even though calling `bank_b.emode.validate_entries_with_liability_weights(&bank_b.config, group.emode_max_init_leverage, group.emode_max_maint_leverage)` manually on the loaded state returns `Err(MaxInitLeverageExceeded)`/`Err(BadEmodeConfig)`, proving the missing check.
6. Follow up with a permissionless deposit/borrow flow against `bank_b` and assert the health cache uses the excess `asset_weight`, confirming exploitable overstated collateral.

### Citations

**File:** programs/marginfi/src/instructions/marginfi_group/emode_clone.rs (L14-17)
```rust
    let source_bank = ctx.accounts.copy_from_bank.load()?;
    let mut destination_bank = ctx.accounts.copy_to_bank.load_mut()?;

    destination_bank.emode = source_bank.emode;
```

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

**File:** programs/marginfi/src/instructions/marginfi_group/configure_bank.rs (L44-52)
```rust
        bank.configure(&bank_config)?;
        msg!("Bank configured!");

        let group = ctx.accounts.group.load()?;
        bank.emode.validate_entries_with_liability_weights(
            &bank.config,
            group.emode_max_init_leverage,
            group.emode_max_maint_leverage,
        )?;
```

**File:** programs/marginfi/src/state/emode.rs (L77-137)
```rust
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
```

**File:** programs/marginfi/src/state/marginfi_account.rs (L1296-1296)
```rust
            let mut asset_weight = bank.get_asset_weight(requirement_type, emode_config);
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
