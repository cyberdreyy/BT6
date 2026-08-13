## Analog Found

### Title
Permissionless `propagate_staked_settings` Bypasses `FREEZE_SETTINGS`, Allowing Anyone To Overwrite a "Frozen" Staked Bank's Oracle, Risk Weights, and Risk Tier - ([File: programs/marginfi/src/instructions/marginfi_group/propagate_staked_settings.rs])

### Summary
The original report describes a `setToken` function that lets an owner reset a critical parameter (the staking token) without checking whether the previous configuration is still in active use, silently locking user funds. The closest reachable analog in marginfi-v2 is `propagate_staked_settings`, an unprivileged/permissionless instruction that overwrites a staked bank's oracle key, asset weights, deposit limit, oracle max age, and risk tier from the group's `StakedSettings` account — without ever checking the bank's `FREEZE_SETTINGS` flag, which is the protocol's documented mechanism for preventing exactly this kind of unchecked reconfiguration.

### Finding Description
`propagate_staked_settings` is explicitly documented as permissionless ("Permissionless ix to propagate a group's staked collateral settings to any bank in that group") and its `Accounts` struct has no `Signer` requirement at all — only a `has_one` constraint tying `staked_settings` to the group, and a constraint that the target `bank` belongs to that group and is tagged `ASSET_TAG_STAKED`. [1](#0-0) 

The handler unconditionally copies the group's current `StakedSettings` onto the bank — oracle key, `asset_weight_init`/`asset_weight_maint`, `deposit_limit`, `total_asset_value_init_limit`, `oracle_max_age`, `risk_tier`, and staked-oracle flags — with no check of `bank.get_flag(FREEZE_SETTINGS)`: [2](#0-1) 

By contrast, every other bank-configuration path that can change these same fields explicitly panics if `FREEZE_SETTINGS` is set, precisely to give users a "credible commitment" that risk parameters, oracle configuration, and limits will not change once frozen: [3](#0-2) 

The admin documentation confirms this is a deliberate, relied-upon guarantee: "Once frozen, the admin can still adjust capacity limits, but cannot change anything that affects the risk profile of the bank (such as weights, oracle setup, interest rate curves, init limit, etc)." [4](#0-3) 

`propagate_staked_settings` is the sole exception to this rule — the same category of bug as the report's `setToken`: a state-mutating entry point that resets a security-relevant parameter without verifying that doing so is still safe (i.e., without checking that the bank hasn't been intentionally locked/frozen against such changes).

### Impact Explanation
Because this instruction is permissionless (no signer check at all) and freely callable by anyone, any user can force a "frozen" staked-collateral bank to silently re-sync its oracle key, collateral weights, deposit limit, and risk tier to whatever the group's `StakedSettings` currently holds — even though the group admin explicitly froze that bank's settings to guarantee stability to depositors/borrowers. This can:
- Change `asset_weight_init`/`asset_weight_maint` or `risk_tier` for a bank that users are actively borrowing against as collateral, potentially pushing previously healthy accounts into an unhealthy/liquidatable state without warning.
- Swap the oracle key underneath the bank to a different value from the group settings, changing price computation for existing depositors' collateral.
- Reduce `deposit_limit`/`total_asset_value_init_limit`, restricting further deposits inconsistently with the frozen commitment.

This mirrors the report's "temporary loss of funds" pattern: an unchecked reset of a critical parameter after users have already taken positions relying on the old (frozen) configuration.

### Likelihood Explanation
High reachability: the instruction requires no privileged signer, no admin key, and can be invoked by anyone as soon as the group admin updates `StakedSettings` (a normal, expected admin operation) for any staked bank that happens to have `FREEZE_SETTINGS` set. No attacker-controlled special conditions are needed beyond calling the existing permissionless instruction.

### Recommendation
Add a `check!(!bank.get_flag(FREEZE_SETTINGS), MarginfiError::...)` guard in `propagate_staked_settings`, mirroring the guard already present in `lending_pool_configure_bank_oracle` and `lending_pool_set_fixed_oracle_price`, so that frozen staked banks are excluded from permissionless settings propagation.

### Proof of Concept
1. Group admin creates a staked bank via `lending_pool_add_bank_permissionless`, then sets `FREEZE_SETTINGS` on it via `lending_pool_configure_bank` (as documented, to give a credible commitment to depositors/borrowers).
2. Users deposit/borrow against the frozen staked bank.
3. Group admin later updates `StakedSettings` for the group (e.g., via `edit_staked_settings`) for a legitimate, unrelated reason (different validator/bank in the same group).
4. Any unprivileged actor calls `propagate_staked_settings` on the "frozen" bank — no signer needed. The instruction succeeds and overwrites the bank's oracle, weights, limits, and risk tier from the updated `StakedSettings`, despite `FREEZE_SETTINGS` being set — as shown by the lack of any flag check in [2](#0-1)  and confirmed by the permissionless test flow where only the fee payer signs: [5](#0-4) .

### Citations

**File:** programs/marginfi/src/instructions/marginfi_group/propagate_staked_settings.rs (L1-35)
```rust
// Permissionless ix to propagate a group's staked collateral settings to any bank in that group.
use crate::state::bank_config::BankConfigImpl;
use crate::MarginfiError;
use anchor_lang::prelude::*;
use marginfi_type_crate::{
    constants::{ASSET_TAG_STAKED, STAKED_ORACLE_FLAGS},
    types::{Bank, MarginfiGroup, StakedSettings},
};

pub fn propagate_staked_settings(ctx: Context<PropagateStakedSettings>) -> Result<()> {
    let settings = ctx.accounts.staked_settings.load()?;
    let mut bank = ctx.accounts.bank.load_mut()?;

    let (oracle_before, oracle_after) = (bank.config.oracle_keys[0], settings.oracle);

    bank.config.oracle_keys[0] = settings.oracle;
    bank.config.asset_weight_init = settings.asset_weight_init;
    bank.config.asset_weight_maint = settings.asset_weight_maint;
    bank.config.deposit_limit = settings.deposit_limit;
    bank.config.total_asset_value_init_limit = settings.total_asset_value_init_limit;
    bank.config.oracle_max_age = settings.oracle_max_age;
    bank.config.risk_tier = settings.risk_tier;
    bank.flags &= !STAKED_ORACLE_FLAGS;
    bank.flags |= settings.flags & STAKED_ORACLE_FLAGS;

    // Only validate the oracle info if it has changed
    if oracle_before != oracle_after {
        bank.config
            .validate_oracle_setup(ctx.remaining_accounts, None, None, None)?;
    }

    bank.config.validate()?;

    Ok(())
}
```

**File:** programs/marginfi/src/instructions/marginfi_group/config_bank_oracle.rs (L16-19)
```rust
    // If settings are frozen, you can only update the deposit and borrow limits, so this ix will fail
    if bank.get_flag(FREEZE_SETTINGS) {
        panic!("cannot change oracle settings on frozen banks");
    } else {
```

**File:** guides/ADMIN/BANK_STATE.md (L105-109)
```markdown
This flag provides a credible commitment that the bank's risk parameters, oracle configuration,
interest rate curves, and other settings will not change. It can only be set through the
`configure_bank` instruction by the group admin. Once frozen, the admin can still adjust capacity
limits, but cannot change anything that affects the risk profile of the bank (such as weights,
oracle setup, interest rate curves, init limit, etc).
```

**File:** tests/specs/staked/s06_propagateSets.spec.ts (L92-116)
```typescript
  it("(permissionless) Propagate staked settings to a bank - happy path", async () => {
    let tx = new Transaction();
    tx.add(
      await propagateStakedSettings(bankrunProgram, {
        settings: settingsKey,
        bank: bankKey,
        oracle: oracles.usdcOracle.publicKey,
      })
    );
    tx.recentBlockhash = await getBankrunBlockhash(bankrunContext);
    tx.sign(groupAdmin.wallet); // just to the pay the fee
    await banksClient.tryProcessTransaction(tx);

    const bank = await bankrunProgram.account.bank.fetch(bankKey);
    const config = bank.config;
    assertKeysEqual(config.oracleKeys[0], oracles.usdcOracle.publicKey);
    assertI80F48Approx(config.assetWeightInit, 0.2);
    assertI80F48Approx(config.assetWeightMaint, 0.3);
    assertBNEqual(config.depositLimit, 42);
    assertBNEqual(config.totalAssetValueInitLimit, 43);
    assert.equal(config.oracleMaxAge, 44);
    assert.deepEqual(config.riskTier, { collateral: {} });
    // Propagation always set the pyth migration flag on the first call
    assert.equal(config.configFlags, PYTH_PULL_MIGRATED);
  });
```
