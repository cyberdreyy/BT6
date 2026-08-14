## Finding

### Title
Permissionless SOL donation to a Single-Validator Stake Pool inflates `StakedWithPythPush` collateral price, enabling over-borrowing against self-controlled collateral - (File: programs/marginfi/src/state/price.rs)

### Summary
The `StakedWithPythPush` oracle path prices staked-collateral banks as `pyth_price * (pool_NAV / lst_supply)`, where `pool_NAV` is simply the raw lamport balance of the underlying stake account (and, post-SVSP-upgrade, the on-ramp account), less rent-exempt reserve. Because that lamport balance can be inflated by anyone with a plain `SystemProgram::transfer`, an attacker who controls (or is the dominant holder of) a validator's single-validator stake pool can permissionlessly and instantly inflate the price used to value their own staked collateral, then borrow against the inflated value — the same "dump value into the pool that backs the price" pattern described in the external LP-TVL report.

### Finding Description
The relevant pricing logic lives in `load_oracle_context_with_max_age` for `OracleSetup::StakedWithPythPush`: [1](#0-0) 

`sol_pool_adjusted_balance` (the NAV) comes from `staked_pool_net_asset_value`, which is computed from the raw lamport balances of the stake account and on-ramp account minus rent-exempt reserves — i.e., it directly reflects however much SOL happens to sit in those accounts: [2](#0-1) 

`multiplier = NAV / lst_supply` is then multiplied into both the raw and EMA Pyth price: [3](#0-2) 

The test suite explicitly demonstrates that anyone can move the price this way with a bare system transfer, treating it purely as "yield appreciation": [4](#0-3) 

This is structurally identical to the reported bug class: a value used to price a collateral position (`amt0*price0 + amt1*price1` for LP tokens; here `stake_lamports + onramp_lamports` for staked SOL) is directly attacker-controllable via a simple deposit/donation into the underlying account, with no TWAP, no minimum liquidity/anti-manipulation floor, and no check that the NAV increase came from a legitimate staking/rewards flow rather than an arbitrary transfer.

### Impact Explanation
Any user can permissionlessly deploy their own validator/vote account and back a `StakedWithPythPush` bank for it via `lending_pool_add_bank_permissionless` (per `patch-note-drafts/patch-notes-0.1.9.md` referencing permissionless staked-bank creation flows). If that user is the dominant/sole holder of the resulting LST:
1. Deposit LST as collateral in the marginfi staked bank.
2. Transfer additional SOL directly into the underlying stake account / on-ramp account (`splSolPool`/`splOnRampPool`), instantly increasing `sol_pool_adjusted_balance` and therefore `multiplier`/`adjusted_price` for the bank on the very next price pulse or risk check — no cooldown, no epoch boundary required.
3. Borrow against the now-inflated collateral value up to `asset_weight_init * inflated_value`.
4. Abandon the position; the extra donated SOL is only recoverable by unstaking (which takes epochs), while the borrowed funds are already extracted — leaving the protocol/insurance fund to absorb bad debt from the undercollateralized position.

This is a durable, financially damaging state (uncollateralized borrow / bad debt), reachable by an unprivileged user with only a system-program transfer, matching the "value redirection / exploitable misvaluation" criteria. `total_asset_value_init_limit` can bound (but not eliminate) the blast radius per bank. Note `SECURITY.md` explicitly states oracle manipulation/flash-loan-style attacks are **not** excluded from scope.

### Likelihood Explanation
The action requires no special privileges — creating a validator and a single-validator stake pool, and becoming (or arranging to be) its dominant LST holder, is permissionless, as is transferring SOL to any account. The only real cost to the attacker is the donated SOL, which is recoverable over time via unstaking since they control/dominate the pool's LST supply. This makes the attack economically viable specifically for a validator the attacker controls, i.e., not applicable to third-party, broadly-held validators/pools — likelihood is therefore moderate (requires the attacker to set up or dominate a specific staked bank) but the primitive itself is unrestricted and instantaneous once that precondition is met.

### Recommendation
- Do not derive `pool_NAV`/price multiplier purely from instantaneous lamport balances of accounts that accept arbitrary permissionless transfers; use a time-weighted or epoch-boundary-anchored NAV (e.g., value as of last epoch boundary) so a single-transaction donation cannot immediately move the price used for borrowing.
- Alternatively/in addition, cap the per-transaction/per-slot delta the multiplier may move, or require validator/stake-pool allowlisting with minimum diversified-holder thresholds before a `StakedWithPythPush` bank can be added, reducing the ability for a single Sybil-controlled pool to dominate NAV.
- Ensure `total_asset_value_init_limit` is mandatorily set (non-zero) for any permissionlessly-added staked bank so a single manipulated bank cannot support unbounded borrow power.

### Proof of Concept
Conceptual reproduction (mirrors `tests/specs/staked/s05_solAppreciates.spec.ts` mechanics):
1. Attacker creates/controls validator `V` and its SVSP; mints LST `L` to themselves as sole holder.
2. Attacker adds a `StakedWithPythPush` marginfi bank for `V` (permissionless bank-add flow) and deposits `L` as collateral.
3. Attacker sends a `SystemProgram::transfer` of `X` SOL directly to `splSolPool` (the stake account) as shown at [5](#0-4) , instantly raising `sol_pool_adjusted_balance` and thus `multiplier` in `programs/marginfi/src/state/price.rs` (`OracleSetup::StakedWithPythPush` branch, lines 361-437).
4. Attacker calls `pulse_bank_price`/borrow using the now-inflated cached price, borrowing another asset up to `asset_weight_init * inflated_collateral_value`.
5. Attacker keeps the borrowed proceeds; the staked position (including the donated SOL) is left to be liquidated, realizing bad debt for the protocol equal to the artificially added borrowing power.

### Citations

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

**File:** programs/marginfi/src/state/price.rs (L402-417)
```rust
                let adjusted_price = (feed.price.price as i128)
                    .checked_mul(sol_pool_adjusted_balance as i128)
                    .ok_or_else(math_error!())?
                    .checked_div(lst_supply as i128)
                    .ok_or_else(math_error!())?;
                feed.price.price = adjusted_price.try_into().ok().ok_or_else(math_error!())?;

                let adjusted_ema_price = (feed.ema_price.price as i128)
                    .checked_mul(sol_pool_adjusted_balance as i128)
                    .ok_or_else(math_error!())?
                    .checked_div(lst_supply as i128)
                    .ok_or_else(math_error!())?;
                feed.ema_price.price = adjusted_ema_price
                    .try_into()
                    .ok()
                    .ok_or_else(math_error!())?;
```

**File:** programs/marginfi/src/state/price.rs (L1874-1898)
```rust
    #[test]
    fn staked_pool_nav_includes_onramp_lamports_less_rent() {
        let rent = Rent::default();
        let owner = NATIVE_STAKE_ID;
        let stake_key = Pubkey::new_unique();
        let onramp_key = Pubkey::new_unique();
        let mut stake_data = vec![0; 200];
        let mut onramp_data = vec![0; 200];
        let mut stake_lamports = rent.minimum_balance(stake_data.len()) + 10_000;
        let mut onramp_lamports = rent.minimum_balance(onramp_data.len()) + 7_000;

        let stake_ai =
            test_account_info(&stake_key, &mut stake_lamports, &mut stake_data[..], &owner);
        let onramp_ai = test_account_info(
            &onramp_key,
            &mut onramp_lamports,
            &mut onramp_data[..],
            &owner,
        );

        assert_eq!(
            staked_pool_net_asset_value(&stake_ai, &onramp_ai, &rent).unwrap(),
            17_000
        );
    }
```

**File:** tests/specs/staked/s05_solAppreciates.spec.ts (L94-115)
```typescript
  // Note: there is also some natural appreciation here because a few epochs have elapsed...
  it(
    "v0 stake sol pool grows by " +
      stakeSolAppreciation +
      " SOL (e.g. MEV rewards) - LST price grows",
    async () => {
      let tx = new Transaction();
      tx.add(
        SystemProgram.transfer({
          fromPubkey: wallet.publicKey,
          toPubkey: validators[0].splSolPool,
          lamports: stakeSolAppreciation * LAMPORTS_PER_SOL,
        }),
      );
      tx.recentBlockhash = await getBankrunBlockhash(bankrunContext);
      tx.sign(wallet.payer);
      await banksClient.processTransaction(tx);

      const priceMultiplierAfterAppreciation = await fetchLstPriceMultiplier();
      assert.approximately(priceMultiplierAfterAppreciation, 2.0, 0.000001); // (50 + 30) / 40 = 2
    },
  );
```
