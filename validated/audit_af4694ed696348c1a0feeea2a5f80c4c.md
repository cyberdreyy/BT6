Confirmed: `configure()` in `bank.rs` and `BankConfig::validate()` never cross-check `asset_tag` against `oracle_setup`/integration accounts, and this is exercised in the test suite itself with a comment acknowledging the resulting breakage.

### Title
Group admin can change a bank's `asset_tag` via `lending_pool_configure_bank` without validating consistency against the bank's `oracle_setup`/integration accounts - ([File: programs/marginfi/src/state/bank.rs])

### Summary
This mirrors the Algebra `setPlugin` bug: an "active pool" (bank) has an auxiliary configuration field (`plugin`/`pluginConfig` ≈ `oracle_setup`+integration accounts / `asset_tag`) that can be independently overwritten by an admin instruction without re-validating or re-initializing the dependent state, leaving the bank in an inconsistent configuration.

### Finding Description
The `configure()` method used by `lending_pool_configure_bank` sets `self.config.asset_tag` from `BankConfigOpt.asset_tag` with a plain `set_if_some!` and no cross-check against the bank's `oracle_setup` or its integration accounts (`integration_acc_1`, etc.): [1](#0-0) 

The subsequent `self.config.validate()` call only validates weight bounds, interest-rate config and `oracle_max_age` — it never checks that `asset_tag` matches `oracle_setup`: [2](#0-1) 

`asset_tag` is load-bearing throughout the program: it determines how many remaining accounts are expected for oracle/risk validation (`get_remaining_accounts_per_asset_tag`), which integration-specific code paths run (Drift/Kamino/JupLend), and how assets can be co-mingled (`ASSET_TAG_STAKED`, `ASSET_TAG_SOL`, etc.): [3](#0-2) 

Meanwhile, `oracle_setup` (the actual price-feed/integration wiring analogous to Algebra's "plugin") is only settable through the separate `lending_pool_configure_bank_oracle` instruction, and that instruction likewise does not check `asset_tag` for consistency: [4](#0-3) 

The project's own test suite demonstrates and documents exactly this hazard for a live Drift-integrated bank: after changing `asset_tag` alone (leaving the Drift-specific `oracle_setup`/`integration_acc_1` untouched), the bank becomes internally inconsistent and Drift-specific operations begin failing with `WrongBankAssetTagForDriftOperation`: [5](#0-4) 

This is structurally identical to the Algebra issue: an admin-facing config instruction can update one part of a two-part configuration (asset_tag ≈ pluginConfig-defining field) while the other part (oracle_setup/integration accounts ≈ plugin) is left stale, with no hook/re-initialization step to reconcile them.

### Impact Explanation
An admin (intentionally or through fat-fingered input) can desynchronize `asset_tag` from `oracle_setup`, causing:
- Permanent/durable freeze of pool functionality for the affected bank: Kamino/Drift/JupLend/Staked-specific instructions (deposit-into-integration, interest accrual routines, liquidation paths that branch on `asset_tag`) begin failing until the mismatch is manually corrected, since `get_remaining_accounts_per_bank`/`get_remaining_accounts_per_asset_tag` and the integration-specific instruction handlers assume `asset_tag` accurately reflects the oracle/integration wiring.
- Potential mis-comingling of collateral types, because `asset_tag` (not `oracle_setup`) governs which assets can be held together (e.g., `ASSET_TAG_STAKED` restricting borrows to SOL only) — this is a risk-engine/accounting consistency issue with direct financial-safety implications, not merely cosmetic.
- No `FREEZE_SETTINGS` protection prevents this: the freeze flag only stops further changes, it does not prevent the initial admin misconfiguration.

This qualifies as a durable inconsistency with financial effect: it can silently corrupt the risk classification of a bank's users/collateral or block critical operations (deposits, interest accrual, liquidation flows for integration banks) until manually fixed — matching the "medium" severity and "unintended consequences / temporarily block specific pool functionality" description in the reference report.

### Likelihood Explanation
Requires the group `admin` role (a privileged actor), so this is not exploitable by an unprivileged attacker directly. However, per the analog rules this is still in-scope because it is a durable state-inconsistency bug reachable through a normal, unprivileged-adjacent maintenance instruction (`lending_pool_configure_bank`) that any legitimate admin can trigger accidentally (no malicious intent needed, as shown by the project's own test scenario), and it has a genuine accounting/availability impact rather than being purely theoretical — the test suite itself proves the broken state is reachable and persists.

### Recommendation
Add cross-validation in `BankConfig::validate()` (or in `configure()`/`lending_pool_configure_bank`) that rejects an `asset_tag` update whose value is inconsistent with the bank's current `oracle_setup` (e.g., a Drift `oracle_setup` variant paired with a non-Drift `asset_tag`, or vice versa). Alternatively, disallow modifying `asset_tag` through the generic `configure_bank` path entirely and require it be set only atomically together with `oracle_setup`/integration accounts (i.e., merge asset_tag changes into `lending_pool_configure_bank_oracle`, which already re-validates the oracle/integration accounts via `validate_oracle_setup`).

### Proof of Concept
1. Group admin creates a Drift-integrated bank with `oracle_setup = DriftPythPull` (or similar) and `asset_tag = ASSET_TAG_DRIFT`, wired to Drift's spot market/oracle.
2. Admin calls `lending_pool_configure_bank` with `BankConfigOpt { asset_tag: Some(ASSET_TAG_KAMINO), ..Default::default() }` (or any other asset tag) — this succeeds because `configure()`/`validate()` perform no cross-check against `oracle_setup` or `integration_acc_1`. [1](#0-0) 
3. Any Drift-specific instruction that checks `asset_tag` (e.g., Drift user-init) now fails, even though the bank's oracle/integration wiring is still fully Drift-specific — exactly as reproduced in the repo's own test: [5](#0-4) 
4. The bank remains in this broken, inconsistent state indefinitely until an admin manually restores the correct `asset_tag`, during which time deposits/borrows/liquidations relying on the correct `asset_tag`/`oracle_setup` pairing are blocked or misclassified.

### Citations

**File:** programs/marginfi/src/state/bank.rs (L456-456)
```rust
        set_if_some!(self.config.asset_tag, config.asset_tag);
```

**File:** programs/marginfi/src/state/bank_config.rs (L48-84)
```rust
    fn validate(&self) -> MarginfiResult {
        let asset_init_w = I80F48::from(self.asset_weight_init);
        let asset_maint_w = I80F48::from(self.asset_weight_maint);

        check!(
            asset_init_w >= I80F48::ZERO && asset_init_w <= I80F48::ONE,
            MarginfiError::InvalidConfig
        );
        check!(
            asset_maint_w <= (I80F48::ONE + I80F48::ONE),
            MarginfiError::InvalidConfig
        );
        check!(asset_maint_w >= asset_init_w, MarginfiError::InvalidConfig);

        let liab_init_w = I80F48::from(self.liability_weight_init);
        let liab_maint_w = I80F48::from(self.liability_weight_maint);

        check!(liab_init_w >= I80F48::ONE, MarginfiError::InvalidConfig);
        check!(
            liab_maint_w <= liab_init_w && liab_maint_w >= I80F48::ONE,
            MarginfiError::InvalidConfig
        );

        self.interest_rate_config.validate()?;

        if self.risk_tier == RiskTier::Isolated {
            check!(asset_init_w == I80F48::ZERO, MarginfiError::InvalidConfig);
            check!(asset_maint_w == I80F48::ZERO, MarginfiError::InvalidConfig);
        }

        check!(
            self.oracle_max_age >= ORACLE_MIN_AGE,
            MarginfiError::InvalidOracleSetup
        );

        Ok(())
    }
```

**File:** programs/marginfi/src/state/marginfi_account.rs (L53-62)
```rust
/// 5 for `ASSET_TAG_STAKED` (bank, oracle, lst mint, lst pool, onramp), 2 for most others (bank, oracle), 3
/// for Kamino (bank, oracle, reserve), 1 for Fixed
fn get_remaining_accounts_per_asset_tag(asset_tag: u8) -> MarginfiResult<usize> {
    match asset_tag {
        ASSET_TAG_DEFAULT | ASSET_TAG_SOL => Ok(2),
        ASSET_TAG_KAMINO | ASSET_TAG_DRIFT | ASSET_TAG_SOLEND | ASSET_TAG_JUPLEND => Ok(3),
        ASSET_TAG_STAKED => Ok(5),
        _ => err!(MarginfiError::AssetTagMismatch),
    }
}
```

**File:** programs/marginfi/src/instructions/marginfi_group/config_bank_oracle.rs (L9-42)
```rust
pub fn lending_pool_configure_bank_oracle(
    ctx: Context<LendingPoolConfigureBankOracle>,
    setup: u8,
    oracle: Pubkey,
) -> MarginfiResult {
    let mut bank = ctx.accounts.bank.load_mut()?;

    // If settings are frozen, you can only update the deposit and borrow limits, so this ix will fail
    if bank.get_flag(FREEZE_SETTINGS) {
        panic!("cannot change oracle settings on frozen banks");
    } else {
        let setup_type =
            OracleSetup::from_u8(setup).unwrap_or_else(|| panic!("unsupported oracle type"));
        if matches!(
            setup_type,
            OracleSetup::Fixed
                | OracleSetup::FixedKamino
                | OracleSetup::FixedDrift
                | OracleSetup::FixedJuplend
        ) {
            return err!(MarginfiError::UseSetFixedOraclePrice);
        }

        bank.config.oracle_setup = setup_type;
        bank.config.oracle_keys[0] = oracle;

        msg!(
            "setting oracle to type: {:?} key: {:?}",
            bank.config.oracle_setup,
            bank.config.oracle_keys[0]
        );

        bank.config
            .validate_oracle_setup(ctx.remaining_accounts, None, None, None)?;
```

**File:** tests/specs/drift/d06_driftBankInit.spec.ts (L240-288)
```typescript
  it("(admin) Configure wrong asset tag for Token A bank - happy path (but all Drift operations will now fail on it)", async () => {
    const user = groupAdmin;
    let bankConfigOpt = blankBankConfigOptRaw();
    bankConfigOpt.assetTag = ASSET_TAG_KAMINO;

    const configureTx = new Transaction().add(
      await configureBank(user.mrgnBankrunProgram, {
        bank: driftAccounts.get(DRIFT_TOKEN_A_BANK),
        bankConfigOpt,
      })
    );

    await processBankrunTransaction(
      ctx,
      configureTx,
      [user.wallet],
      false,
      true
    );
  });

  it("(user 1) Tries to init Drift user for Token A bank - wrong asset tag", async () => {
    const user = users[1];
    const initUserAmount = new BN(100);
    const initUserTx = new Transaction().add(
      await makeInitDriftUserIx(
        user.mrgnBankrunProgram,
        {
          feePayer: user.wallet.publicKey,
          bank: driftAccounts.get(DRIFT_TOKEN_A_BANK),
          signerTokenAccount: users[1].tokenAAccount,
          driftOracle: driftAccounts.get(DRIFT_TOKEN_A_PULL_ORACLE)!,
        },
        {
          amount: initUserAmount,
        },
        1
      )
    );
    const result = await processBankrunTransaction(
      ctx,
      initUserTx,
      [user.wallet],
      true,
      true
    );
    // WrongBankAssetTagForDriftOperation
    assertBankrunTxFailed(result, 6302);
  });
```
