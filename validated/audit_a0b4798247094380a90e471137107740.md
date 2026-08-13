No vulnerability found for this question.

**Reasoning:** `config_flags` on Solend banks is only ever set inside `lending_pool_add_bank_solend`, which takes `bank_config: SolendConfigCompact` as caller-supplied instruction data and converts it via `to_bank_config()` [1](#0-0) . However, that instruction's account context requires the `group` account to satisfy `has_one = admin @ MarginfiError::Unauthorized` and requires an `admin: Signer<'info>` to co-sign [2](#0-1) , so this call path is admin-gated, not permissionless.

The only permissionless bank-creation path in the codebase, `lending_pool_add_bank_permissionless`, is for `ASSET_TAG_STAKED` banks (not Solend) and hardcodes `config_flags: PYTH_PUSH_MIGRATED_DEPRECATED` itself rather than accepting caller input [3](#0-2) .

Additionally, the config-update struct used for post-creation configuration changes, `BankConfigOpt`, does not even contain a `config_flags` field [4](#0-3) , meaning no update/backfill/migration instruction built on `BankConfigOpt` can touch this byte at all, whether admin or permissionless.

Since there is no reachable non-admin instruction that reads or writes `config_flags` on a Solend (or any) bank, the described smuggling path does not exist in this codebase.

### Citations

**File:** programs/marginfi/src/instructions/solend/add_pool.rs (L52-52)
```rust
    let config = bank_config.to_bank_config(reserve_key);
```

**File:** programs/marginfi/src/instructions/solend/add_pool.rs (L110-116)
```rust
    #[account(
        mut,
        has_one = admin @ MarginfiError::Unauthorized
    )]
    pub group: AccountLoader<'info, MarginfiGroup>,

    pub admin: Signer<'info>,
```

**File:** programs/marginfi/src/instructions/marginfi_group/add_pool_permissionless.rs (L72-89)
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
```

**File:** type-crate/src/types/bank_config.rs (L163-192)
```rust
#[cfg_attr(feature = "anchor", derive(AnchorDeserialize, AnchorSerialize))]
#[derive(Default, Clone, PartialEq, Eq)]
pub struct BankConfigOpt {
    pub asset_weight_init: Option<WrappedI80F48>,
    pub asset_weight_maint: Option<WrappedI80F48>,

    pub liability_weight_init: Option<WrappedI80F48>,
    pub liability_weight_maint: Option<WrappedI80F48>,

    pub deposit_limit: Option<u64>,
    pub borrow_limit: Option<u64>,

    pub operational_state: Option<BankOperationalState>,

    pub interest_rate_config: Option<InterestRateConfigOpt>,

    pub risk_tier: Option<RiskTier>,

    pub asset_tag: Option<u8>,

    pub total_asset_value_init_limit: Option<u64>,

    pub oracle_max_confidence: Option<u32>,

    pub oracle_max_age: Option<u16>,

    pub permissionless_bad_debt_settlement: Option<bool>,
    pub freeze_settings: Option<bool>,
    pub tokenless_repayments_allowed: Option<bool>,
}
```
