### Title
Legacy staked-collateral oracle pricing reverts on negative stake-value delta instead of handling decrease - ([File: programs/marginfi/src/state/price.rs])

### Summary
The Pods `STETHVault` bug stemmed from a subtraction (`lastRoundAssets - totalAssets` direction) that assumed the underlying LST only ever appreciates, causing an underflow revert once stETH's rebase went negative (slashing). marginfi-v2 has a structurally identical assumption in its legacy Staked-Collateral (SVSP) oracle pricing path: `legacy_staked_pool_delegated_value` subtracts a fixed 1 SOL "bootstrap" amount from the validator stake account's delegated stake using `checked_sub` + `math_error!()`, which hard-errors (rather than degrading gracefully) if the underlying staked value ever drops below that floor.

### Finding Description
`OracleSetup::StakedWithPythPush` pricing branches on `bank.on_ramp_transition()`. For banks still on the legacy path (`OnRampTransition::PreTransition`), the pool NAV is computed by: [1](#0-0) 

```rust
fn legacy_staked_pool_delegated_value(pool_stake_info: &AccountInfo) -> MarginfiResult<u64> {
    ...
    Ok(stake.delegation.stake.checked_sub(1_000_000_000).ok_or_else(math_error!())?)
}
```

This assumes `stake.delegation.stake` (the validator's delegated stake backing the single-pool LST) is always ≥ 1 SOL more than whatever value is actually attributable to depositors — i.e. it assumes the staked value only ever grows or stays comfortably above the fixed non-refundable bootstrap. If the validator's active/delegated stake amount is reduced below 1 SOL (via slashing-equivalent stake reduction, deactivation drain, or any other mechanism dropping the account's delegated stake under the floor), `checked_sub` returns `None`, and `math_error!()` propagates a hard error out of `load_price_feed`/`get_price_of_type`, rather than a saturating/clamped value.

This is called directly from the `StakedWithPythPush` branch of the oracle loader: [2](#0-1) 

Contrast this with the newer/canonical NAV path `staked_pool_net_asset_value`, which correctly uses `saturating_sub` for both the main stake and on-ramp lamports specifically to avoid this class of failure: [3](#0-2) 

The legacy path is not dead code — it is the active pricing formula for any staked bank still in `OnRampTransition::PreTransition`, gated by `StakedSettings`/`Bank.flags` and slated for removal only in a future (~1.10) release: [4](#0-3) 

Because this oracle-load function is invoked by every instruction that needs to price a `StakedWithPythPush` bank — deposit, withdraw, borrow, liquidate, health-pulse, and bankruptcy's health computation — a hard error here does not just mis-value the asset (as in the stETH case), it makes the bank entirely unpriceable.

### Impact Explanation
Once a staked bank's backing validator's delegated stake drops under the 1 SOL floor while the bank is still in the legacy on-ramp transition state, `legacy_staked_pool_delegated_value` errors on every call. Any marginfi account holding that staked-collateral position becomes unpriceable:
- Deposits/withdraws/borrows requiring this bank's price fail.
- Liquidations of undercollateralized accounts holding this collateral fail (liquidators must supply and successfully load this same oracle).
- Health-cache computations that include this balance fail, which can also block `handle_bankruptcy` resolution for affected accounts (health computation for the account is a prerequisite there).

This produces a durable freeze on that collateral type with financial effect: bad debt cannot be cleared via normal liquidation/bankruptcy flows for as long as the condition persists, mirroring the stETH bug's "controller can't end the round" freeze, but now blocking marginfi's liquidation/bankruptcy safety valves instead of a vault round.

### Likelihood Explanation
This is only reachable for banks still on the legacy `PreTransition` NAV formula, which per the roadmap is a temporary state expected to be phased out, so exposure is time-bound and depends on migration status of specific staked banks at any point in time. It also requires the underlying validator's delegated stake to fall below the fixed 1 SOL bootstrap, which is an extreme depletion event (well beyond typical slashing penalties for validators, which are usually small percentages) — but it is not privileged, not admin-gated, and not mocked-only: it depends purely on validator/staking-network behavior outside marginfi's control, matching the "unprivileged, permissionless maintenance/staked collateral" class the analog rules ask to keep.

### Recommendation
Change `legacy_staked_pool_delegated_value` to use `saturating_sub` (matching the canonical `staked_pool_net_asset_value` path) instead of `checked_sub` + `math_error!()`, so that a stake balance at or below the 1 SOL bootstrap floor produces a valid (zero or near-zero) NAV instead of a hard error. This keeps pricing, liquidation, and bankruptcy flows functional even under an extreme negative-value event on the underlying staked asset, consistent with how `STETHVault`'s fix guarded the yield-calculation subtraction with a comparison instead of allowing it to underflow/revert.

### Proof of Concept
1. A `StakedWithPythPush` bank remains in `OnRampTransition::PreTransition` (legacy NAV formula still active for that bank).
2. The validator backing the SVSP single-pool stake account experiences a reduction such that `stake.delegation.stake < 1_000_000_000` (e.g., a slashing-equivalent event or a drained/near-fully-deactivated pool stake account).
3. Any instruction that must price this bank (`lending_account_withdraw`, `lending_account_borrow`, `lending_account_liquidate`, `pulse_health`, or the health check inside `lending_pool_handle_bankruptcy`) calls `load_price_feed` → `legacy_staked_pool_delegated_value`, which computes `checked_sub(1_000_000_000)` on a value below that floor, yielding `None`, triggering `math_error!()` and reverting the entire instruction.
4. As long as the bank remains in this state, no liquidator can seize the position, no depositor holding it can be resolved via bankruptcy, and the bad debt (if any) becomes durably stuck — reproducing the stETH bug's "round can never end" freeze in marginfi's liquidation/bankruptcy machinery instead.

### Citations

**File:** programs/marginfi/src/state/price.rs (L106-122)
```rust
fn staked_pool_net_asset_value(
    pool_stake_info: &AccountInfo,
    pool_onramp_info: &AccountInfo,
    rent: &Rent,
) -> MarginfiResult<u64> {
    let pool_rent_exempt_reserve = rent.minimum_balance(pool_stake_info.data_len());
    let onramp_rent_exempt_reserve = rent.minimum_balance(pool_onramp_info.data_len());

    let main_stake_value = pool_stake_info
        .lamports()
        .saturating_sub(pool_rent_exempt_reserve);
    let onramp_value = pool_onramp_info
        .lamports()
        .saturating_sub(onramp_rent_exempt_reserve);

    Ok(main_stake_value.saturating_add(onramp_value))
}
```

**File:** programs/marginfi/src/state/price.rs (L124-138)
```rust
// To be removed once SVSP update is rolled out (likely in 1.10)
fn legacy_staked_pool_delegated_value(pool_stake_info: &AccountInfo) -> MarginfiResult<u64> {
    let stake_state = try_from_slice_unchecked::<StakeStateV2>(&pool_stake_info.data.borrow())?;
    let (_, stake) = match stake_state {
        StakeStateV2::Stake(meta, stake, _) => (meta, stake),
        _ => return err!(MarginfiError::StakePoolValidationFailed),
    };

    // Legacy pricing subtracts single-pool's initial non-refundable 1 SOL bootstrap stake.
    Ok(stake
        .delegation
        .stake
        .checked_sub(1_000_000_000)
        .ok_or_else(math_error!())?)
}
```

**File:** programs/marginfi/src/state/price.rs (L376-379)
```rust
                    OnRampTransition::PreTransition => {
                        // To be removed once SVSP update is rolled out (likely in 1.10)
                        legacy_staked_pool_delegated_value(&ais[2])?
                    }
```

**File:** patch-note-drafts/patch-notes-0.1.9.md (L14-32)
```markdown
### SVSP Migration Plan

The migration is gated behind two new staked-settings flags that are copied onto staked-bank
`Bank.flags` and will be rolled out in three steps:

1. `disable_staked_oracles` (admin) sets `STAKED_ORACLE_DISABLED` on `StakedSettings` (and clears
   `STAKED_ORACLE_PRICE_USES_ONRAMP`) — once propagated to staked banks, all staked-bank pricing
   temporarily panics while the rollout happens. We intend to set this state just before SVSP
   upgrades, for as little duration as possible.

**_Foundation updates the SVSP program_**

2. Banks are backfilled with their validator vote account (now stored as a fourth oracle key)
   (`lending_pool_backfill_staked_bank_validator_vote_account`).
3. `enable_staked_oracle_onramp` (admin) sets `STAKED_ORACLE_PRICE_USES_ONRAMP` on
   `StakedSettings` (and clears `STAKED_ORACLE_DISABLED`). Once propagated, staked banks switch to
   the new NAV formula.

The whole SVSP-transition surface is temporary and slated for removal once rollout completes (likely 1.10).
```
