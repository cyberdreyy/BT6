Confirmed: `STAKED_ORACLE_DISABLED = 1 << 9` is copied onto `bank.flags` at creation time in `lending_pool_add_bank_permissionless` (`bank.flags |= settings.flags & STAKED_ORACLE_FLAGS`) [1](#0-0) , but the function never checks whether this "disabled" state is currently set on `StakedSettings` before proceeding to fully create and initialize the new bank (vaults, oracle wiring, event emission). This is a direct analog to the reported bug class.

### Title
Permissionless staked-collateral bank creation ignores admin-set `STAKED_ORACLE_DISABLED` blacklist flag - (File: `programs/marginfi/src/instructions/marginfi_group/add_pool_permissionless.rs`)

### Summary
`lending_pool_add_bank_permissionless` lets anyone create a new `ASSET_TAG_STAKED` bank for a group, using the group's `StakedSettings` as a template [2](#0-1) . The admin can set `STAKED_ORACLE_DISABLED` on `StakedSettings` via `disable_staked_oracles` to signal that the staked-collateral feature/oracle path is temporarily unsafe during SVSP migration [3](#0-2) . However, `lending_pool_add_bank_permissionless` never checks this flag before creating the new bank; it only copies `settings.flags & STAKED_ORACLE_FLAGS` onto the freshly created bank's flags [1](#0-0) . This mirrors the `DelegatorFactory::create` bug: an entity-creation path fails to check a governance-set "blacklist"/disabled state before instantiating a new object governed by that state.

### Finding Description
`StakedSettings.flags` bit 9 (`STAKED_ORACLE_DISABLED`) is documented as meaning "staked oracle pricing is temporarily disabled" during the SVSP on-ramp migration [4](#0-3) , and the patch notes explicitly describe it as gating "all operations involving stake banks" and causing "staked-bank pricing [to] temporarily panic while the rollout happens" [5](#0-4) . Despite this, `lending_pool_add_bank_permissionless` (a fully permissionless instruction, callable by any validator/user) does not gate on `settings.load()?.flags & STAKED_ORACLE_DISABLED != 0` anywhere in its body before performing `group.add_bank()`, `bank.config.validate()`, `bank.config.validate_oracle_setup(...)`, initializing three token vaults, and emitting `LendingPoolBankCreateEvent` [6](#0-5) . The instruction is registered as `(permissionless)` in `lib.rs` [7](#0-6) , and its `Accounts` struct places no constraint on `staked_settings` other than `has_one = marginfi_group` [8](#0-7) .

The consequence is that during the exact window the admin disables staked oracles specifically to prevent unsafe staked-bank activity during the SVSP migration, an unprivileged caller can still create brand-new staked banks that inherit the `STAKED_ORACLE_DISABLED` flag from the moment of creation. The test suite confirms that once this flag is propagated to a bank, price pulses on that bank revert with `StakeOraclesDisabled` [9](#0-8) , meaning any newly created bank in this state is immediately frozen/unusable for its intended purpose (its collateral cannot be priced), but it still consumes group bank-slot capacity (`group.add_bank()`), and both `bank_seed` and the `(marginfi_group, bank_mint, bank_seed)` PDA space are permanently occupied. Since `bank_seed` is caller-chosen and `add_pool_permissionless` is permissionless, an attacker can also front-run/exhaust the mint+seed keyspace for a given validator's LST during the disabled window, or simply create garbage banks that must later be cleaned up by an admin (bank closing requires `CLOSE_ENABLED` flag per the permissions guide) [10](#0-9) .

### Impact Explanation
This is a durable state-inconsistency / griefing issue rather than a direct fund-loss bug: it allows unprivileged creation of state (new `Bank` accounts, vault accounts) explicitly during a window the protocol admin flagged as unsafe/disabled, bypassing the intended governance control, consuming bank-array slots, and creating banks that are immediately unusable (all staked pricing reverts while `STAKED_ORACLE_DISABLED` is set) yet still occupy permanent PDA space and require admin cleanup. It does not directly redirect value or bypass borrow/withdraw authorization, so severity is lower than the original Solidity finding, but it is a concrete authorization-bypass of a documented "blacklist" gate on a permissionless creation path.

### Likelihood Explanation
Likelihood is limited to the narrow, admin-controlled time window between calling `disable_staked_oracles` and `enable_staked_oracle_onramp`/propagation completing — a window the team describes as intentionally short ("for as little duration as possible") [11](#0-10) . Within that window, exploitation requires only a permissionless call with a valid SPL single-pool stake account setup — no special privilege is needed, matching the "unprivileged-user analog" requirement.

### Recommendation
Add an explicit check in `lending_pool_add_bank_permissionless` that reverts if `settings.flags & STAKED_ORACLE_DISABLED != 0`, analogous to the recommended `create` fix in the original report:

```rust
let settings = ctx.accounts.staked_settings.load()?;
check!(
    settings.flags & STAKED_ORACLE_DISABLED == 0,
    MarginfiError::StakedOraclesDisabled // or a new dedicated error
);
```

### Proof of Concept
1. Admin calls `disable_staked_oracles` on a group's `StakedSettings`, setting `STAKED_ORACLE_DISABLED` (as exercised in `tests/specs/staked/s02_addBank.spec.ts` "(admin) Disables stakes oracles - happy path") [12](#0-11) .
2. Any unprivileged user calls `lending_pool_add_bank_permissionless` with a fresh, valid SPL single-pool stake account/mint/vote-account chain for a new validator (satisfying all `check!`/`check_eq!` PDA validations in the instruction) [13](#0-12) .
3. The instruction succeeds and creates a new `Bank` with `bank.flags` including `STAKED_ORACLE_DISABLED` copied from settings, despite the group being in the explicitly disabled state — no `check!`/`constraint` anywhere blocks creation based on this flag.
4. Any subsequent `pulse_bank_price` (or deposit/borrow) call against this newly created bank reverts with the `StakeOraclesDisabled` error, confirming the bank was created in a broken/unusable state that the admin's blacklist flag was meant to prevent from being created in the first place [9](#0-8) .

### Citations

**File:** programs/marginfi/src/instructions/marginfi_group/add_pool_permissionless.rs (L28-47)
```rust
pub fn lending_pool_add_bank_permissionless(
    ctx: Context<LendingPoolAddBankPermissionless>,
    bank_seed: u64,
) -> MarginfiResult {
    let LendingPoolAddBankPermissionless {
        bank_mint,
        liquidity_vault,
        insurance_vault,
        fee_vault,
        bank: bank_loader,
        stake_pool,
        sol_pool,
        pool_onramp,
        validator_vote_account,
        ..
    } = ctx.accounts;

    let mut bank = bank_loader.load_init()?;
    let settings = ctx.accounts.staked_settings.load()?;
    let mut group = ctx.accounts.marginfi_group.load_mut()?;
```

**File:** programs/marginfi/src/instructions/marginfi_group/add_pool_permissionless.rs (L110-116)
```rust
    bank.flags |= BANK_SEED_KNOWN;
    bank.flags |= settings.flags & STAKED_ORACLE_FLAGS;
    if bank_mint.to_account_info().owner == &anchor_spl::token_2022::ID {
        bank.flags |= IS_T22;
    }
    bank.config.oracle_setup = OracleSetup::StakedWithPythPush;
    bank.config.oracle_keys[0] = settings.oracle;
```

**File:** programs/marginfi/src/instructions/marginfi_group/add_pool_permissionless.rs (L118-181)
```rust
    log_pool_info(&bank);

    group.add_bank()?;

    bank.config.validate()?;

    check!(
        stake_pool.owner == &SPL_SINGLE_POOL_ID,
        MarginfiError::StakePoolValidationFailed
    );
    let validator_vote_account = validator_vote_account.key();
    let lst_mint = bank_mint.key();
    let stake_pool = stake_pool.key();
    let sol_pool = sol_pool.key();

    // Validate the validator vote account by proving it derives this stake pool, and in turn
    // this mint + SOL stake pool + on-ramp PDA.
    let (exp_stake_pool, exp_mint, exp_sol_pool, exp_onramp) =
        derive_single_pool_keys_from_vote_and_validate_owner(
            &ctx.accounts.validator_vote_account.to_account_info(),
        )?;
    check_eq!(
        exp_stake_pool,
        stake_pool,
        MarginfiError::StakePoolValidationFailed
    );
    check_eq!(exp_mint, lst_mint, MarginfiError::StakePoolValidationFailed);
    check_eq!(
        exp_sol_pool,
        sol_pool,
        MarginfiError::StakePoolValidationFailed
    );
    check_eq!(
        exp_onramp,
        pool_onramp.key(),
        MarginfiError::StakePoolValidationFailed
    );
    check!(
        pool_onramp.owner == &NATIVE_STAKE_ID,
        MarginfiError::StakePoolValidationFailed
    );

    // Track the validator vote account for staked-collateral metadata.
    bank.integration_acc_1 = validator_vote_account;

    // The mint, stake pool, and validated on-ramp are recorded for price calculation.
    bank.config.oracle_keys[1] = lst_mint;
    bank.config.oracle_keys[2] = sol_pool;
    bank.config.oracle_keys[3] = exp_onramp;
    bank.config.validate_oracle_setup(
        ctx.remaining_accounts,
        Some(lst_mint),
        Some(stake_pool),
        Some(sol_pool),
    )?;

    emit!(LendingPoolBankCreateEvent {
        header: GroupEventHeader {
            marginfi_group: ctx.accounts.marginfi_group.key(),
            signer: Some(ctx.accounts.fee_payer.key())
        },
        bank: bank_loader.key(),
        mint: bank_mint.key(),
    });
```

**File:** programs/marginfi/src/instructions/marginfi_group/add_pool_permissionless.rs (L192-195)
```rust
    #[account(
        has_one = marginfi_group @ MarginfiError::InvalidGroup
    )]
    pub staked_settings: AccountLoader<'info, StakedSettings>,
```

**File:** programs/marginfi/src/instructions/marginfi_group/on_ramp_transition.rs (L8-16)
```rust
// To be removed once SVSP update is rolled out (likely in 1.10)
pub fn disable_staked_oracles(ctx: Context<DisableStakedOracles>) -> MarginfiResult {
    let mut staked_settings = ctx.accounts.staked_settings.load_mut()?;

    staked_settings.flags &= !STAKED_ORACLE_PRICE_USES_ONRAMP;
    staked_settings.flags |= STAKED_ORACLE_DISABLED;

    Ok(())
}
```

**File:** type-crate/src/types/staked_settings.rs (L49-54)
```rust
    /// Desired bitmask for staked-bank transition flags. These bits are copied to `Bank.flags`
    /// when staked settings are propagated or when a new staked bank is created.
    /// * Bit 9 (512): `STAKED_ORACLE_DISABLED` — staked oracle pricing is temporarily disabled.
    /// * Bit 10 (1024): `STAKED_ORACLE_PRICE_USES_ONRAMP` — staked oracle pricing includes the SPL
    ///   single-pool on-ramp account in NAV.
    pub flags: u64,
```

**File:** patch-note-drafts/patch-notes-0.1.9.md (L16-22)
```markdown
The migration is gated behind two new staked-settings flags that are copied onto staked-bank
`Bank.flags` and will be rolled out in three steps:

1. `disable_staked_oracles` (admin) sets `STAKED_ORACLE_DISABLED` on `StakedSettings` (and clears
   `STAKED_ORACLE_PRICE_USES_ONRAMP`) — once propagated to staked banks, all staked-bank pricing
   temporarily panics while the rollout happens. We intend to set this state just before SVSP
   upgrades, for as little duration as possible.
```

**File:** programs/marginfi/src/lib.rs (L94-100)
```rust
    /// (permissionless) Add a staked collateral bank. Requires a valid SPL single-pool LST mint.
    pub fn lending_pool_add_bank_permissionless(
        ctx: Context<LendingPoolAddBankPermissionless>,
        bank_seed: u64,
    ) -> MarginfiResult {
        marginfi_group::lending_pool_add_bank_permissionless(ctx, bank_seed)
    }
```

**File:** tests/specs/staked/s02_addBank.spec.ts (L809-822)
```typescript
  it("(admin) Disables stakes oracles - happy path", async () => {
    let tx = new Transaction();
    tx.add(
      await disableStakedOracles(
        groupAdmin.mrgnBankrunProgram,
        marginfiGroup.publicKey,
      ),
    );
    tx.recentBlockhash = await getBankrunBlockhash(bankrunContext);
    tx.sign(groupAdmin.wallet);
    await banksClient.processTransaction(tx);

    const settingsAcc = await fetchStakedSettings();
    assertBNEqual(settingsAcc.flags, STAKED_ORACLE_DISABLED);
```

**File:** tests/specs/staked/s02_addBank.spec.ts (L848-868)
```typescript
  it("(permissionless) Pulse any staked bank with stake oracles disabled - should fail", async () => {
    for (let i = 0; i < numValidators; i++) {
      const tx = new Transaction().add(
        await pulseBankPrice(groupAdmin.mrgnBankrunProgram, {
          bank: validators[i].bank,
          remaining: [
            oracles.wsolOracle.publicKey,
            validators[i].splMint,
            validators[i].splSolPool,
            validators[i].splOnRampPool,
          ],
        }),
      );
      tx.recentBlockhash = await getBankrunBlockhash(bankrunContext);
      tx.sign(groupAdmin.wallet);

      const result = await banksClient.tryProcessTransaction(tx);
      // StakeOraclesDisabled
      assertBankrunTxFailed(result, 6053);
    }
  });
```

**File:** guides/ADMIN/PERMISSIONS_AND_ROLES.md (L30-33)
```markdown
- Configure the group itself
- Freeze and unfreeze individual user accounts
- Handle bankruptcy (in addition to `risk_admin`)
- Close banks (when `CLOSE_ENABLED` flag is set)
```
