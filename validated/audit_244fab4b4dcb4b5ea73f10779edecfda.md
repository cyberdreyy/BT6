### #Title
Spot-balance (non-TWAP) SVSP on-ramp NAV used to price staked collateral — permissionlessly inflatable within a single transaction - (File: `programs/marginfi/src/state/price.rs`)

### Summary
The `StakedWithPythPush` oracle path prices SVSP-staked collateral by reading the *instantaneous* lamport balances of the underlying SPL single-pool stake account and its "on-ramp" account and dividing by the LST mint's current supply, exactly the same class of bug flagged in the Predy report: using a spot, unprotected on-chain value (`slot0`) instead of a manipulation-resistant TWAP/oracle value. Here the "spot price" is `AccountInfo::lamports()` on the on-ramp/stake accounts, which — unlike a Pyth/Switchboard feed — has no staleness check, no confidence interval, and no deviation guard, and can be inflated by anyone in the same transaction via a plain SOL transfer.

### Finding Description
For `OracleSetup::StakedWithPythPush`, the bank's price multiplier is computed from `staked_pool_net_asset_value`: [1](#0-0) 

This function does nothing more than read the current lamport balances of the stake account and on-ramp account: [2](#0-1) 

The resulting `sol_pool_adjusted_balance` is divided by `lst_supply` to form the `multiplier`, which directly scales the Pyth SOL price used to value the staked LST collateral. Crucially, the "on-ramp" account is a plain lamport-holding account that anyone can top up with a normal `SystemProgram::transfer`, as demonstrated in the test suite itself: [3](#0-2) 

Because `staked_pool_net_asset_value` is evaluated live, in the same instruction that consumes the price (via `lending_account_borrow`/`pulse_health`/liquidation risk checks), an attacker can transfer SOL into the on-ramp account and immediately consume the inflated price in the very same transaction — there is no TWAP, no minimum holding period, and no confidence/deviation check comparable to what is applied to the Pyth/Switchboard feeds elsewhere in this file (e.g. `get_confidence_interval`, `PriceBias`). This mirrors the Predy `Trade::getSqrtPrice()` -> `UniHelper.getSqrtPrice()` -> `slot0` pattern: an unprotected spot value feeding directly into a trade/borrow decision.

### Impact Explanation
The inflated multiplier increases the USD value assigned to a user's staked-LST collateral balance for the duration of the attacker's transaction. Since staked collateral is explicitly usable to back borrows of other assets (SOL/USDC, etc., as shown in `tests/specs/staked/s05_solAppreciates.spec.ts`), an attacker can:
1. Deposit LST as collateral in a validator's staked bank.
2. In the same (or an adjacent, well-timed) transaction, transfer SOL to that validator's on-ramp account — funds for this can even come from marginfi's own permissionless `lending_account_start_flashloan` facility, requiring no attacker capital.
3. Borrow against the inflated collateral valuation before the health check re-reads a normal price.

Because the multiplier is `NAV / lst_supply`, this is most exploitable on small/young stake pools with low LST supply (e.g., right after a validator bank is created, when `lst_supply` is small), where a modest donation produces a large multiplier swing and a disproportionate borrow-power gain relative to the donation cost. This is a genuine exploitable misvaluation with a direct financial effect (over-collateralized borrow / bad debt risk to the pool), not merely theoretical.

### Likelihood Explanation
The on-ramp/stake account balances are read with no staleness or deviation protection, and depositing lamports into an arbitrary account via `SystemProgram::transfer` is a completely permissionless, unprivileged, single-instruction operation requiring no special access. Any unprivileged user monitoring or targeting a low-liquidity staked bank can perform this in one transaction. The likelihood is Medium: it requires a validator bank with a small LST supply to make the attack profitable relative to donation cost, but no privileged or validator access is needed to execute it.

### Recommendation
- Do not price staked collateral from the raw, instantaneous lamport balance of the on-ramp/stake accounts. Use a time-weighted or checkpointed NAV (e.g., snapshot at the start of the slot/epoch, or a minimum look-back window) similar to how Pyth EMA/TWAP prices are already used elsewhere in this file (`OraclePriceType::TimeWeighted`).
- Alternatively, bound the multiplier's per-transaction/per-slot delta (a deviation check, analogous to `max_twap_divergence_bps` already used by Kamino integrations in this codebase) so a single donation cannot instantaneously swing collateral valuation.
- Require the on-ramp NAV used for pricing to be refreshed/committed by a separate, rate-limited instruction rather than read live in the same transaction that consumes it for a borrow/health decision (mirroring the "refresh" pattern already used for Kamino reserves).

### Proof of Concept
1. Create a `StakedWithPythPush` bank for validator V (small LST supply, e.g. 1 initial bootstrap SOL + a modest amount of user deposits), as done in `tests/specs/staked/s02_addBank.spec.ts`.
2. Attacker deposits a small amount of V's LST as collateral into their marginfi account.
3. In the same transaction:
   a. Attacker (optionally using marginfi's own flash loan, `lending_account_start_flashloan`) sends a large amount of SOL via `SystemProgram::transfer` to V's SVSP on-ramp account (as done for legitimate NAV growth in `tests/specs/staked/s02_addBank.spec.ts:978-995`, but performed by an unprivileged attacker instead of a legitimate depositor).
   b. `staked_pool_net_asset_value` (`programs/marginfi/src/state/price.rs:106-122`) now returns an inflated NAV because `pool_onramp_info.lamports()` includes the just-transferred SOL.
   c. Attacker calls `lending_account_borrow` against another bank, whose risk check consumes the now-inflated LST price/multiplier via `OracleSetup::StakedWithPythPush` handling (`programs/marginfi/src/state/price.rs:341-437`), unlocking more borrow power than the attacker's real collateral justifies.
   d. Attacker repays the flash loan within the same transaction, keeping the extra borrowed assets.
4. Compare the borrow limit computed before vs. after the donation to show the valuation delta is attacker-controlled and immediate, with no TWAP/deviation safeguard applied (unlike the Pyth EMA/confidence-interval logic used for the primary SOL price component).

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

**File:** programs/marginfi/src/state/price.rs (L361-395)
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
