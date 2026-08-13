Important: SECURITY.md explicitly states "This does not exclude oracle manipulation/flash-loan attacks," so this class of finding is in scope. However, "Incorrect data supplied by third-party oracles" and "Impacts requiring basic economic and governance attacks" are out of scope, which is a relevant boundary to weigh against this finding.

### Title
Staked-collateral bank price can be inflated via permissionless lamport donation to the SPL single-pool stake/on-ramp accounts - (File: programs/marginfi/src/state/price.rs)

### Summary
For `OracleSetup::StakedWithPythPush` banks, the LST/SOL exchange-rate multiplier used to price staked collateral is derived from the *live lamport balance* of the underlying native stake account and its "on-ramp" account, not from any oracle-attested or otherwise access-controlled value. Because Solana's System Program allows any unprivileged account to send lamports to *any* pubkey, an attacker can permissionlessly inflate this "NAV" figure to increase the reported price of the staked collateral bank, without there being any sanity check on the return value.

### Finding Description
`staked_pool_net_asset_value` computes the pool's net asset value purely from `AccountInfo::lamports()` on the main stake account and the on-ramp account, subtracting their rent-exempt reserves: [1](#0-0) 

This NAV is then used directly, with no independent validation, as the numerator of the price multiplier applied to the Pyth SOL price to derive the staked-collateral bank's price: [2](#0-1) 

Because lamports can be sent to any account address via a plain System Program transfer (the receiving account does not need to sign, and its owner program has no say in whether it can *receive* lamports), any unprivileged user can top up the pool's on-ramp account (or, less practically, the stake account) to raise `sol_pool_adjusted_balance`, and thus raise `multiplier = NAV / lst_supply`, and thus raise the adjusted Pyth price used for that bank. The test suite itself demonstrates this exact mechanic operationally (albeit as a functional test rather than an attack): [3](#0-2) 

This price feeds into `lending_pool_pulse_bank_price_cache` (permissionless) and into every deposit/withdraw/borrow/liquidate/health-check flow that reads the bank's cached or live oracle price: [4](#0-3) 

Unlike the CRV-pool `balanceOfy3CRVinWant()` bug cited in the external report — where an unvalidated pool balance read is used to price a share token and can be pushed up/down within a single transaction via a large deposit/withdraw — this staked-collateral pricing path has the same root cause: an instantaneous, unauthenticated on-chain balance is trusted as a price input with no plausibility/sanity check (e.g., no bound relative to the last cached price, no rate-of-change limit, no minimum NAV floor beyond the rent-exempt subtraction).

The `total_asset_value_init_limit` mechanism mitigates initial-borrow-power impact somewhat by capping USD counted at deposit/borrow time, but it does not protect the maintenance-weight valuation used for liquidation/health checks, so an inflated price can still keep an otherwise-unhealthy account artificially healthy and block/avoid liquidation, or let a liquidator/liquidatee mis-price an under-collateralized position.

### Impact Explanation
An attacker who is the dominant (or sole) depositor for a given permissionlessly-created per-validator staked bank can donate lamports to that validator's on-ramp account to inflate the bank's reported LST price. Because staked banks are permissionless and each validator gets its own low-TVL bank, the cost of manipulation (donated, unrecoverable lamports since the attacker has no withdrawal authority over the pool's stake/on-ramp accounts) can be made arbitrarily small relative to a large existing staked position, letting the attacker:
- Inflate maintenance-weight collateral value to avoid liquidation of an otherwise unhealthy position.
- Inflate initial-weight collateral value beyond what `total_asset_value_init_limit` was intended to bound, if that limit is set loosely for a low-TVL bank.

This is a real, on-chain, financially-relevant misvaluation of collateral, not merely "incorrect data from a third-party oracle" (the manipulated input is the raw on-chain stake/on-ramp account balance controlled by the attacker's own donation, not the Pyth SOL price itself).

### Likelihood Explanation
Likelihood is moderate: it requires the attacker to hold a large share of a specific, likely low-liquidity, permissionlessly-created staked bank (an economic precondition, not a privileged-access one), and the donated lamports are not recoverable (unlike a true single-transaction flash loan), so the attack is a cost/benefit trade rather than a free/reversible exploit. This differs from a "no-cost" flashloan reentrant manipulation, which somewhat lowers likelihood/severity relative to the referenced report, but the underlying pattern — trusting an unauthenticated live balance for pricing — is unambiguously present and reachable by any unprivileged user via a plain `SystemProgram.transfer`.

### Recommendation
Do not treat raw `AccountInfo::lamports()` deltas as a trusted NAV input without validation. Options:
- Bound the multiplier's rate of change against the last cached price (`bank.cache.price_multiplier`), rejecting or clamping updates that jump beyond a configured tolerance within a short window, similar to `oracle_max_confidence`/staleness checks used elsewhere.
- Derive NAV from delegated-stake/activation data from the stake account (as `legacy_staked_pool_delegated_value` does for the main stake account) rather than raw lamports, and require the on-ramp balance to be reconciled against the SVSP program's own accounting rather than trusted directly.
- Consider requiring `total_asset_value_init_limit` to also gate maintenance-weight valuation for staked banks, or add a bank-level cap on the on-ramp/stake account's contribution to NAV as a fraction of total delegated stake.

### Proof of Concept
1. Group admin permissionlessly creates a `StakedWithPythPush` bank for validator `V` (low TVL).
2. User A deposits a large amount of validator `V`'s LST as collateral and borrows near the maintenance threshold.
3. Attacker (User A or a colluding party) sends a `SystemProgram.transfer` of lamports directly to validator `V`'s SPL single-pool on-ramp account (`splOnRampPool`), exactly as demonstrated functionally in [3](#0-2) .
4. Anyone permissionlessly calls `lending_pool_pulse_bank_price_cache` (or the price is read live during a health check), which recomputes `staked_pool_net_asset_value` including the newly-donated lamports, inflating `multiplier` and hence the bank's adjusted Pyth price, per [5](#0-4) .
5. User A's position, now valued higher at maintenance weight, evades liquidation despite being economically under-collateralized (or borrows more than intended), while the donated SOL is permanently lost to the stake pool (not recoverable by the attacker).

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

**File:** programs/marginfi/src/state/price.rs (L361-406)
```rust
                let sol_pool_adjusted_balance = match bank.on_ramp_transition() {
                    OnRampTransition::OnRampEnabled => {
                        let expected_onramp = expected_staked_onramp(bank)?;
                        if ais[3].key != &expected_onramp {
                            msg!(
                                "Expected staked on-ramp key: {:?}, got: {:?}",
                                expected_onramp,
                                ais[3].key
                            );
                            return Err(error!(MarginfiError::WrongOracleAccountKeys));
                        }

                        let rent = Rent::get()?;
                        staked_pool_net_asset_value(&ais[2], &ais[3], &rent)?
                    }
                    OnRampTransition::PreTransition => {
                        // To be removed once SVSP update is rolled out (likely in 1.10)
                        legacy_staked_pool_delegated_value(&ais[2])?
                    }
                    OnRampTransition::StakeOraclesDisabled => {
                        return Err(error!(MarginfiError::StakeOraclesDisabled));
                    }
                };

                // Note: exchange rate is `pool_nav / lst_supply`, but we will do the
                // division last to avoid precision loss. Division does not need to be
                // decimal-adjusted because both SOL and stake positions use 9 decimals

                let account_info = &ais[0];
                check_primary_oracle_key(bank_config, account_info)?;

                let mut feed = PythPushOraclePriceFeed::load_checked(account_info, clock, max_age)?;
                let multiplier = I80F48::from_num(sol_pool_adjusted_balance)
                    .checked_div(I80F48::from_num(lst_supply))
                    .ok_or_else(math_error!())?;
                let cache_raw_price = if let Some(price_type) = cache_price_type {
                    Some(feed.get_price_and_confidence_of_type(price_type, u32::MAX)?)
                } else {
                    None
                };

                let adjusted_price = (feed.price.price as i128)
                    .checked_mul(sol_pool_adjusted_balance as i128)
                    .ok_or_else(math_error!())?
                    .checked_div(lst_supply as i128)
                    .ok_or_else(math_error!())?;
```

**File:** tests/specs/staked/s02_addBank.spec.ts (L978-995)
```typescript
  it("(user 0) Adds 9 SOL to the validator 0's on-ramp pool - multiplier changes again", async () => {
    let tx = new Transaction();
    tx.add(
      SystemProgram.transfer({
        fromPubkey: users[0].wallet.publicKey,
        toPubkey: validators[0].splOnRampPool,
        lamports: 9 * LAMPORTS_PER_SOL, // Total canonical NAV now becomes 50
      }),
    );
    tx.recentBlockhash = await getBankrunBlockhash(bankrunContext);
    tx.sign(users[0].wallet);
    await banksClient.processTransaction(tx);

    const priceMultiplierWithOnRamp = await fetchLstPriceMultiplier();

    // (41 + 9) / 40 = 1.25
    assert.approximately(priceMultiplierWithOnRamp, 1.25, 0.000001);
  });
```

**File:** programs/marginfi/src/instructions/marginfi_group/pulse_bank_price_cache.rs (L1-24)
```rust
use crate::state::bank::BankImpl;
use crate::state::price::OraclePriceFeedAdapter;
use crate::{MarginfiError, MarginfiResult};
use anchor_lang::prelude::*;
use marginfi_type_crate::types::{Bank, MarginfiGroup};

/// (permissionless) Refresh the cached oracle price for a bank.
pub fn lending_pool_pulse_bank_price_cache<'info>(
    ctx: Context<'info, LendingPoolPulseBankPriceCache<'info>>,
) -> MarginfiResult {
    let clock = Clock::get()?;

    let mut bank = ctx.accounts.bank.load_mut()?;

    let price_for_cache = OraclePriceFeedAdapter::get_price_and_confidence_for_cache(
        &bank,
        ctx.remaining_accounts,
        &clock,
    )?;

    bank.update_cache_price(Some(price_for_cache))?;

    Ok(())
}
```
