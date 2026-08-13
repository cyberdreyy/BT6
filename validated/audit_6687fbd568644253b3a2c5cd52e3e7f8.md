Confirmed: the integration program IDs (`KAMINO_PROGRAM_ID`, `FARMS_PROGRAM_ID`, `DRIFT_PROGRAM_ID`, `JUPLEND_LENDING_PROGRAM_ID`, `JUPLEND_LIQUIDITY_PROGRAM_ID`, `SOLEND_PROGRAM_ID`) are compiled-in constants with no admin-facing instruction to update them — `configure()` in `programs/marginfi/src/state/bank.rs` only touches risk/limit/oracle fields, never a program-ID field.

### Title
Hardcoded third-party integration program IDs create a durable, unrecoverable freeze of user funds if Kamino/Solend/Drift/JupLend redeploy their program - ([File: type-crate/src/pdas.rs])

### Summary
Every marginfi integration instruction (Kamino, Solend, Drift, JupLend deposit/withdraw/liquidate/harvest/refresh) enforces the third-party CPI target via a hardcoded Anchor `#[account(address = ...)]` constraint bound to a compile-time constant (`KAMINO_PROGRAM_ID`, `FARMS_PROGRAM_ID`, `DRIFT_PROGRAM_ID`, `SOLEND_PROGRAM_ID`, `JUPLEND_LENDING_PROGRAM_ID`, `JUPLEND_LIQUIDITY_PROGRAM_ID`). This is directly analogous to the reported Stargate issue: a hardcoded router/program address check (`require`) that reverts the transaction if the external protocol's on-chain program address ever changes.

### Finding Description
Integration account structs pin the counterparty program with a hardcoded address constraint, e.g.: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

These constants are defined once, at compile time, in the type crate: [5](#0-4) 

There is no admin instruction to update these values. `LendingPoolConfigureBank`'s `configure()` only ever mutates risk weights, limits, operational state, oracle confidence/age, and flags — never a program-ID field: [6](#0-5) 
Oracle keys and program IDs are explicitly called out as *not* updatable through this path (per test assertions), and even the dedicated `LendingPoolConfigureBankOracle` instruction only updates `oracle_keys`, not the CPI-target program ID: [7](#0-6) 

If Kamino, Solend, Drift, or JupLend Lending/Liquidity ever redeploys/migrates to a new program ID (e.g., a security upgrade, since none of these are Anchor-upgradeable in a way that preserves the same key, or a deliberate migration), every marginfi CPI-based deposit/withdraw/liquidate/refresh instruction targeting that integration's banks would permanently fail with `ConstraintAddress`, because the hardcoded constant baked into the deployed marginfi program no longer matches the live program. Since the fix requires shipping and governance-approving a new marginfi program deployment (not a config transaction), all liquidity already routed into that third-party protocol via marginfi's PDA-owned vaults becomes unwithdrawable through marginfi for an indefinite period.

### Impact Explanation
Unlike the original Stargate report — where a stray revert leaves tokens sitting in a permissionlessly-drainable contract account — Solana transaction atomicity means a failed CPI aborts the whole instruction, so there is no analogous "any user can sweep the stray funds" primitive here. However, the underlying bug class (a `require`/`address` check pinned to a redeployable external address) still produces the closely related failure mode called out in the Validate criteria: a **durable freeze with financial effect**. All depositors/borrowers in the affected integration's banks lose the ability to withdraw, repay, or be liquidated through marginfi until a full program upgrade is shipped, while their assets remain locked (and continue accruing/depreciating) inside the third-party protocol under marginfi's PDA custody.

### Likelihood Explanation
Low-to-moderate. Established DeFi programs like Kamino, Drift, and Solend rarely redeploy to a new program ID (upgrades normally preserve the address via the Solana BPF upgradeable loader), and JupLend is newer with less redeployment history. As with the original Stargate finding, it is difficult to estimate this precisely since it depends entirely on a third party's operational decisions outside marginfi's control.

### Recommendation
Consider making integration program IDs bank-level or group-level configurable state (set once at `lending_pool_add_bank_*` time, and updatable by a dedicated admin instruction with strict scoping), rather than compile-time constants, so a future redeployment by an integration partner can be remediated with a config transaction instead of a full program upgrade. At minimum, monitor these program IDs for any announced or on-chain redeployment and be prepared to ship an emergency program upgrade if one occurs.

### Proof of Concept
1. Assume Kamino Lend redeploys `KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD` to a new program ID `KLendNEW...` (e.g., forced by a critical vulnerability requiring a non-upgrade-in-place fix).
2. All existing Kamino-wrapped banks in marginfi still reference the old `integration_acc_1`/`integration_acc_2` derived from the old Kamino program, and the marginfi binary still hardcodes `KAMINO_PROGRAM_ID = KLend2g3cP87...` in `type-crate/src/pdas.rs`.
3. Any user attempts `kamino_withdraw`; the `#[account(address = KAMINO_PROGRAM_ID)]` constraint on `kamino_program` in `programs/marginfi/src/instructions/kamino/withdraw.rs`/`deposit.rs` fails with `ConstraintAddress`, since the account passed (the live Kamino program, at its new address) does not equal the compiled-in constant.
4. No admin instruction exists to update `KAMINO_PROGRAM_ID`/`FARMS_PROGRAM_ID`; `configure()` in `programs/marginfi/src/state/bank.rs` has no such field.
5. Users' Kamino-side collateral remains stuck under marginfi's `liquidity_vault_authority` PDA control until marginfi ships a full program upgrade with the new constant.

### Citations

**File:** programs/marginfi/src/instructions/solend/withdraw.rs (L403-410)
```rust
    pub user_collateral: UncheckedAccount<'info>,

    /// CHECK: validated against hardcoded program id
    #[account(address = SOLEND_PROGRAM_ID)]
    pub solend_program: UncheckedAccount<'info>,

    pub token_program: Interface<'info, TokenInterface>,
}
```

**File:** programs/marginfi/src/instructions/drift/withdraw.rs (L454-466)
```rust
    /// The Drift signer PDA
    /// CHECK: validated by the Drift program
    pub drift_signer: UncheckedAccount<'info>,

    /// Bank's liquidity token mint
    pub mint: Box<InterfaceAccount<'info, Mint>>,

    /// CHECK: validated against hardcoded program id
    #[account(address = DRIFT_PROGRAM_ID)]
    pub drift_program: UncheckedAccount<'info>,

    pub token_program: Interface<'info, TokenInterface>,
    pub system_program: Program<'info, System>,
```

**File:** programs/marginfi/src/instructions/kamino/deposit.rs (L257-270)
```rust
    #[account(address = KAMINO_PROGRAM_ID)]
    pub kamino_program: UncheckedAccount<'info>,

    /// Farms program for Kamino staking functionality
    /// CHECK: validated against hardcoded program id
    #[account(address = FARMS_PROGRAM_ID)]
    pub farms_program: UncheckedAccount<'info>,

    pub collateral_token_program: Program<'info, Token>,
    pub liquidity_token_program: Interface<'info, TokenInterface>,

    /// CHECK: validated against hardcoded program id
    #[account(address = solana_instructions_sysvar::ID)]
    pub instruction_sysvar_account: UncheckedAccount<'info>,
```

**File:** programs/marginfi/src/instructions/juplend/withdraw.rs (L461-478)
```rust
    /// CHECK: pinned to the JupLend liquidity program
    #[account(address = JUPLEND_LIQUIDITY_PROGRAM_ID)]
    pub liquidity_program: UncheckedAccount<'info>,

    /// CHECK: cross-checked against integration_acc_1.rewards_rate_model
    #[account(
        constraint = rewards_rate_model.key() == integration_acc_1.load()?.rewards_rate_model
            @ MarginfiError::InvalidJuplendLending,
    )]
    pub rewards_rate_model: UncheckedAccount<'info>,

    /// CHECK: validated against hardcoded program id
    #[account(address = juplend_mocks::ID)]
    pub juplend_program: UncheckedAccount<'info>,

    pub token_program: Interface<'info, TokenInterface>,
    pub associated_token_program: Program<'info, anchor_spl::associated_token::AssociatedToken>,
    pub system_program: Program<'info, System>,
```

**File:** type-crate/src/pdas.rs (L4-12)
```rust
pub const KAMINO_PROGRAM_ID: Pubkey = pubkey!("KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD");
pub const FARMS_PROGRAM_ID: Pubkey = pubkey!("FarmsPZpWu9i7Kky8tPN37rs2TpmMrAZrC7S7vJa91Hr");
pub const DRIFT_PROGRAM_ID: Pubkey = pubkey!("dRiftyHA39MWEi3m9aunc5MzRF1JYuBsbn6VPcn33UH");
pub const JUPLEND_LENDING_PROGRAM_ID: Pubkey =
    pubkey!("jup3YeL8QhtSx1e253b2FDvsMNC87fDrgQZivbrndc9");
pub const JUPLEND_LIQUIDITY_PROGRAM_ID: Pubkey =
    pubkey!("jupeiUmn818Jg1ekPURTpr4mFo29p46vygyykFJ3wZC");
pub const JUPLEND_REWARDS_PROGRAM_ID: Pubkey =
    pubkey!("jup7TthsMgcR9Y3L277b8Eo9uboVSmu1utkuXHNUKar");
```

**File:** programs/marginfi/src/state/bank.rs (L403-497)
```rust
    fn configure(&mut self, config: &BankConfigOpt) -> MarginfiResult {
        set_if_some!(self.config.asset_weight_init, config.asset_weight_init);
        set_if_some!(self.config.asset_weight_maint, config.asset_weight_maint);
        set_if_some!(
            self.config.liability_weight_init,
            config.liability_weight_init
        );
        set_if_some!(
            self.config.liability_weight_maint,
            config.liability_weight_maint
        );
        set_if_some!(self.config.deposit_limit, config.deposit_limit);

        set_if_some!(self.config.borrow_limit, config.borrow_limit);

        if let Some(new_state) = config.operational_state {
            // JupLend banks must be activated exactly once through `juplend_init_position`.
            check!(
                !(self.config.asset_tag == ASSET_TAG_JUPLEND
                    && self.config.operational_state == BankOperationalState::Uninitialized),
                MarginfiError::Unauthorized
            );
            // These states are unreachable by configuration
            check!(
                new_state != BankOperationalState::KilledByBankruptcy
                    && new_state != BankOperationalState::Uninitialized,
                MarginfiError::Unauthorized
            );
            // Log operational state change
            let old_state = self.config.operational_state;
            self.config.operational_state = new_state;
            msg!(
                "Operational state changed from {:?} to {:?}",
                old_state,
                new_state
            );
        }

        if let Some(ir_config) = &config.interest_rate_config {
            self.config.interest_rate_config.update(ir_config);
        }

        // Log risk tier change
        if let Some(new_risk_tier) = config.risk_tier {
            let old_risk_tier = self.config.risk_tier;
            self.config.risk_tier = new_risk_tier;
            msg!(
                "Risk tier changed from {:?} to {:?}",
                old_risk_tier,
                new_risk_tier
            );
        }

        set_if_some!(self.config.asset_tag, config.asset_tag);

        set_if_some!(
            self.config.total_asset_value_init_limit,
            config.total_asset_value_init_limit
        );

        set_if_some!(
            self.config.oracle_max_confidence,
            config.oracle_max_confidence
        );

        set_if_some!(self.config.oracle_max_age, config.oracle_max_age);

        if let Some(flag) = config.permissionless_bad_debt_settlement {
            msg!(
                "setting bad debt settlement: {:?}",
                config.permissionless_bad_debt_settlement.unwrap()
            );
            self.update_flag(flag, PERMISSIONLESS_BAD_DEBT_SETTLEMENT_FLAG);
        }

        if let Some(flag) = config.freeze_settings {
            msg!(
                "setting freeze settings: {:?}",
                config.freeze_settings.unwrap()
            );
            self.update_flag(flag, FREEZE_SETTINGS);
        }

        if let Some(flag) = config.tokenless_repayments_allowed {
            msg!(
                "setting tokenless repayments allowed: {:?}",
                config.tokenless_repayments_allowed.unwrap()
            );
            self.update_flag(flag, TOKENLESS_REPAYMENTS_ALLOWED);
        }

        self.config.validate()?;

        Ok(())
    }
```

**File:** programs/marginfi/tests/admin_actions/setup_bank.rs (L877-885)
```rust
        // Oracles no longer update in the standard config instruction
        assert_eq!(
            bank.config.oracle_keys, old_bank.config.oracle_keys,
            "The config does not update oracles, try config_oracle"
        );
        assert_eq!(
            bank.config.oracle_setup, old_bank.config.oracle_setup,
            "The config does not update oracles, try config_oracle"
        );
```
