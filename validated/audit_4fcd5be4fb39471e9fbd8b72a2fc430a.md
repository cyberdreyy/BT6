### Title
Staked-collateral (SVSP) LST oracle price can be inflated via direct lamport donation, enabling over-borrowing/bad debt - (File: `programs/marginfi/src/state/price.rs`)

### Summary
For `OracleSetup::StakedWithPythPush` banks, the LST price multiplier used to value collateral is derived directly from raw account balances (`AccountInfo::lamports()`) of the underlying stake/on-ramp accounts divided by the LST mint supply, rather than from an internally-accounted, donation-resistant exchange rate. This is structurally the same bug class as the `VaderPool` finding: a price/exchange-rate is computed from a manipulable "reserve" balance instead of a protected value, so a permissionless direct transfer can skew it and be used to over-collateralize a loan atomically within one transaction.

### Finding Description
The price for `StakedWithPythPush` banks is computed as `wsol_price * (pool_nav / lst_supply)`, where `pool_nav` comes from `staked_pool_net_asset_value`, which sums the raw lamport balances of the stake account and (once enabled) the on-ramp account, adjusted only for rent-exempt reserve: [1](#0-0) 

This NAV is then divided by `lst_mint.supply` to form the price multiplier and applied to the Pyth SOL price to compute the bank's oracle price: [2](#0-1) 

Both the stake account and the on-ramp account are plain SOL-holding accounts (a native stake account and an SPL single-pool on-ramp account). Since SOL/lamport transfers via `SystemProgram.transfer` require no authorization from the target account, **anyone** can permissionlessly increase `pool_stake_info.lamports()` or `pool_onramp_info.lamports()` without minting any new LST, which directly inflates the numerator of the NAV/supply ratio and thus the reported LST price — with no corresponding increase in `lst_mint.supply`. This is explicitly demonstrated as an intended appreciation mechanism (e.g., MEV rewards) in the test suite, which simulates it with a raw `SystemProgram.transfer` to the pool account and observes the price multiplier increase: [3](#0-2) 

This mirrors the `VaderPool.mintSynth` bug exactly: a price derived from a spot/raw balance ratio (`nativeAsset`/synth reserves in VaderPool; `stake_lamports`/`lst_supply` here) can be moved by an unprivileged party via a simple value transfer, and then used within the *same transaction* to value collateral before it is reverted (or simply abandoned, since the LST supply is small for newly-created validator banks — see `add_pool_permissionless`/`lending_pool_add_bank_permissionless`). An attacker who already holds most of the outstanding LST supply for a given validator bank (realistic for a freshly created staked bank, which anyone can permissionlessly add) can, in one atomic transaction: (1) hold LST collateral already deposited in the bank, (2) donate lamports directly to the stake/on-ramp account to spike `pool_nav`, (3) borrow against the now-inflated collateral valuation in the same transaction, and (4) withdraw/redeem the LST (recovering most of the donated capital back, proportional to their supply share) — leaving the marginfi pool with bad debt from the over-extended borrow.

### Impact Explanation
This allows extraction of value/creation of bad debt in `mrgnlend` by artificially inflating the USD value assigned to staked-collateral positions, which directly increases borrowing power beyond the true backing of the collateral. Because pricing directly reads mutable account lamport balances rather than a protected internal accounting value, the "reserve manipulation → over-borrow → unwind" pattern from the `VaderPool` report is reachable here. The severity is somewhat bounded by `total_asset_value_init_limit` (a USD cap on collateral value counted per bank) and the requirement that the attacker fund the donation, but for banks with small `total_asset_value_init_limit`/small LST supply (e.g., a newly permissionlessly-added validator bank) the donation cost relative to attacker's own supply share can make the attack close to capital-efficient, and no mechanism in the pricing path rejects or dampens a sudden lamport-balance spike the way, e.g., an EMA/TWAP would.

### Likelihood Explanation
Likelihood is moderate-to-low compared to the original AMM case, because: (1) unlike an AMM pool that anyone can freely flashloan-swap in and out of reversibly, a direct lamport donation to a stake/on-ramp account is a one-way transfer the attacker cannot reclaim except through their own proportional share of the LST supply upon redemption; (2) `total_asset_value_init_limit` caps the exploitable USD value per bank, which the guide explicitly calls out as an "oracle attack" mitigation; (3) the attack is most profitable only when the attacker controls a large fraction of a specific validator bank's LST supply, which is a realistic but not universal condition (e.g., freshly created or low-TVL staked banks via `lending_pool_add_bank_permissionless`). Nonetheless, the underlying mechanism (raw-balance-derived price with no manipulation resistance) is a genuine structural weakness analogous to the reported bug class, reachable without any privileged access, using only a permissionless SOL transfer.

### Recommendation
Avoid deriving the LST price multiplier from instantaneous raw lamport balances of externally-transferable accounts. Consider: (a) tracking NAV via an internally-accounted value that only changes through validated deposit/withdraw/reward-accrual instructions rather than `AccountInfo::lamports()`; (b) applying a time-weighted/EMA smoothing to the NAV component so a single-transaction donation cannot immediately affect borrowing power; (c) tightening/mandating a conservative `total_asset_value_init_limit` for all `StakedWithPythPush` banks, especially newly added ones, and validating it is non-zero at bank creation; (d) rate-limiting how much the multiplier can move between price reads within a single transaction/slot.

### Proof of Concept
1. Attacker permissionlessly adds (or targets an existing) `StakedWithPythPush` validator bank with low LST supply, via `lending_pool_add_bank_permissionless`.
2. Attacker legitimately deposits SOL into the SVSP pool to acquire a large fraction of that validator's LST supply, then deposits the LST as collateral into the marginfi bank.
3. In the same transaction, attacker issues a `SystemProgram.transfer` of a large SOL amount directly to the pool's stake account or on-ramp account (as demonstrated functionally in `s05_solAppreciates.spec.ts`, lines 94-115, which performs exactly this transfer and confirms the price multiplier increases), inflating `staked_pool_net_asset_value` (`programs/marginfi/src/state/price.rs` lines 106-122) without minting new LST.
4. Still within the same transaction, attacker calls `lending_account_borrow` against the now-inflated collateral valuation computed by the `StakedWithPythPush` branch in `try_from_bank`/`load_oracle_context_with_max_age` (`programs/marginfi/src/state/price.rs` lines 341-395), receiving loan proceeds larger than the LST's real backing.
5. Attacker withdraws/redeems the LST, recovering their proportional share of the donated SOL, and abandons or under-collateralizes the borrowed position, leaving bad debt for the pool.

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

**File:** programs/marginfi/src/state/price.rs (L341-395)
```rust
            OracleSetup::StakedWithPythPush => {
                check!(ais.len() == 4, MarginfiError::WrongNumberOfOracleAccounts);

                if ais[1].key != &bank_config.oracle_keys[1]
                    || ais[2].key != &bank_config.oracle_keys[2]
                {
                    msg!(
                        "Expected oracle keys: [1] {:?}, [2] {:?}, got: [1] {:?}, [2] {:?}",
                        bank_config.oracle_keys[1],
                        bank_config.oracle_keys[2],
                        ais[1].key,
                        ais[2].key,
                    );
                    return Err(error!(MarginfiError::WrongOracleAccountKeys));
                }

                let lst_mint = Account::<'info, Mint>::try_from(&ais[1]).unwrap();
                let lst_supply = lst_mint.supply;
                check!(lst_supply > 0, MarginfiError::ZeroSupplyInStakePool);

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
