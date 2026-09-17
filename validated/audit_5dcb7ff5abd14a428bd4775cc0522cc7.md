### Title
`updateMultiplier` lets an OPERATOR_ROLE account inflate B20Asset scaled balances/supply with no verification against backing reserves - (File: crates/common/precompiles/src/b20_asset/logic/v1.rs)

### Summary
The external report flags `RewardOracle.supplyReward()` for accepting an operator-supplied amount and increasing supply without checking that the amount is actually backed by the contract's real balance. The B20Asset precompile has the same bug-class shape in its rebasing "multiplier" mechanism: `update_multiplier` lets a role holder set the ERC-8056 scaled-balance multiplier to any non-zero value, instantly and uniformly inflating every holder's `scaledBalanceOf`/`balanceOfUI`/`totalSupplyUI`, with no check that the new multiplier is consistent with any actual backing/collateral increase.

### Finding Description
`update_multiplier` in the V1 logic only requires the operator role and rejects a zero multiplier — it performs no sanity check against reserves, no bound relative to the previous value, and no reconciliation with any external backing signal: [1](#0-0) 

This multiplier is applied wherever the token computes its "scaled" (UI) view of balances and supply — `toScaledBalance`, `scaledBalanceOf`, `balanceOfUI`, `totalSupplyUI` — exposed through the precompile dispatch surface: [2](#0-1) 

Because `set_multiplier` writes the raw storage slot directly with only a non-zero guard, the same role holder that governs mint's `SupplyCapExceeded` check (which correctly caps raw mint amounts) can bypass that cap's intent entirely: instead of minting new raw tokens (bounded by `supply_cap`), it can rescale every existing holder's *reported* balance and total supply upward via the multiplier, which has no cap tied to `supply_cap` or to any external attestation of reserves. This mirrors the `RewardOracle.supplyReward()` flaw: an operator-controlled numeric input inflates the token's reported economic size with no verification that real backing exists to support it.

V2 adds only a `MAX_UI_MULTIPLIER` ceiling and zero check — still no reconciliation against actual reserves: [3](#0-2) 

### Impact Explanation
If the OPERATOR_ROLE key for a B20Asset/B20Stablecoin token is compromised, misconfigured, or if the role is granted more broadly than intended, a single `updateMultiplier`/`updateUIMultiplier` call can multiply every account's `scaledBalanceOf`/`totalSupplyUI` figures without any corresponding increase in real backing. Any external system, redemption flow, or accounting logic that trusts `totalSupplyUI`/`balanceOfUI` as the canonical, backing-verified representation of the asset (as intended by ERC-8056 "Scaled UI Amount" semantics) would treat the token as having more value than it actually has, which is the same "uncontrolled increase of overall supply" class the report calls out for `RewardOracle`. This falls under the accepted "unbacked supply" impact category.

### Likelihood Explanation
This requires the caller to hold `OPERATOR_ROLE` for the specific token (a privileged path), matching the same operational trust model as `RewardOracle.supplyReward()`, which is also presumably restricted to a privileged reporter — the underlying defect in both cases is the *absence of a balance/reserve-verification check* on that privileged input, not the privilege gate itself. Given that `mint()` in the same file enforces a `supply_cap` check while `update_multiplier` enforces none, the asymmetry indicates the missing check is a genuine oversight rather than an intentional design choice.

### Recommendation
Add a check in `update_multiplier`/`update_ui_multiplier` (and their V2 scheduled-update counterparts) bounding how much the multiplier can change per update (e.g., a maximum relative delta or a maximum absolute value tied to `supply_cap`/known reserve data), analogous to bounding `RewardOracle.supplyReward()` deltas against the contract's actual balance. At minimum, enforce that the resulting `totalSupplyUI` cannot exceed a governance-configured ceiling independent of the instant failsafe path, and emit/require a reconciliation record so downstream consumers of `totalSupplyUI` cannot be silently misled about backing.

### Proof of Concept
1. Grant `OPERATOR_ROLE` (or compromise the key holding it) for a deployed `B20Asset` token — the same trust boundary `MINT_ROLE` uses for `mint`, but `update_multiplier` has no analogous `SupplyCapExceeded` check.
2. Call `updateMultiplier(newMultiplier)` with `newMultiplier` set to an arbitrarily large non-zero value (V1: unbounded; V2: bounded only by `MAX_UI_MULTIPLIER`), e.g. `100 * WAD`.
3. Observe `totalSupplyUI()` and every holder's `balanceOfUI(account)` immediately reflect the 100x inflation, per the logic in [4](#0-3)  (test `scaled_reads_use_effective_multiplier` demonstrates the exact mechanism, substituting a benign 2x doubling for what could be an arbitrary large multiplier), with no verification that the token's backing reserves increased by a corresponding amount.

### Citations

**File:** crates/common/precompiles/src/b20_asset/logic/v1.rs (L630-645)
```rust
    fn update_multiplier(
        &self,
        token: &mut B20AssetToken<S, A>,
        caller: Address,
        new_multiplier: U256,
        privileged: bool,
    ) -> Result<()> {
        self.ensure_operator_role(token, caller, privileged)?;
        if new_multiplier.is_zero() {
            return Err(BasePrecompileError::revert(IB20Asset::InvalidMultiplier {}));
        }
        token.accounting_mut().set_multiplier(new_multiplier)?;
        token.accounting_mut().emit_event(
            IB20Asset::MultiplierUpdated { multiplier: new_multiplier }.encode_log_data(),
        )
    }
```

**File:** crates/common/precompiles/src/b20_asset/dispatch.rs (L374-388)
```rust
            // --- Multiplier reads ---
            SC::multiplier(_) => logic.multiplier(self)?.abi_encode().into(),
            SC::uiMultiplier(_) => logic.ui_multiplier(self)?.abi_encode().into(),
            SC::newUIMultiplier(_) => logic.new_ui_multiplier(self)?.abi_encode().into(),
            SC::effectiveAt(_) => logic.effective_at(self)?.abi_encode().into(),
            SC::toScaledBalance(c) => {
                logic.to_scaled_balance(self, c.rawBalance)?.abi_encode().into()
            }
            SC::toRawBalance(c) => logic.to_raw_balance(self, c.scaledBalance)?.abi_encode().into(),
            // ERC-8056 Conversion extension: aliases of `toScaledBalance` / `toRawBalance`.
            SC::toUIAmount(c) => logic.to_scaled_balance(self, c.rawAmount)?.abi_encode().into(),
            SC::fromUIAmount(c) => logic.to_raw_balance(self, c.uiAmount)?.abi_encode().into(),
            SC::scaledBalanceOf(c) => logic.scaled_balance_of(self, c.account)?.abi_encode().into(),
            SC::balanceOfUI(c) => logic.balance_of_ui(self, c.account)?.abi_encode().into(),
            SC::totalSupplyUI(_) => logic.total_supply_ui(self)?.abi_encode().into(),
```

**File:** crates/common/precompiles/src/b20_asset/logic/v2.rs (L725-736)
```rust
    fn update_multiplier(
        &self,
        token: &mut B20AssetToken<S, A>,
        caller: Address,
        new_multiplier: U256,
        privileged: bool,
    ) -> Result<()> {
        let now = token.accounting().timestamp()?;
        self.ensure_operator_role(token, caller, privileged)?;
        if new_multiplier.is_zero() || new_multiplier > Self::MAX_UI_MULTIPLIER {
            return Err(BasePrecompileError::revert(IB20Asset::InvalidMultiplier {}));
        }
```

**File:** crates/common/precompiles/src/b20_asset/logic/v2.rs (L2613-2637)
```rust
    #[test]
    fn scaled_reads_use_effective_multiplier() {
        let mut tok = token();
        tok.accounting_mut().set_balance(ALICE, U256::from(100u64)).unwrap();
        tok.accounting_mut().set_total_supply(U256::from(100u64)).unwrap();
        let target = wad() * U256::from(2u64);
        let effective_at = U256::from(100u64);
        set_now(&mut tok, U256::from(1u64));
        LOGIC.update_ui_multiplier(&mut tok, ALICE, target, effective_at, true).unwrap();

        // Before maturity: 1:1.
        set_now(&mut tok, U256::from(50u64));
        assert_eq!(LOGIC.to_scaled_balance(&tok, U256::from(10u64)).unwrap(), U256::from(10u64));
        assert_eq!(LOGIC.scaled_balance_of(&tok, ALICE).unwrap(), U256::from(100u64));
        assert_eq!(LOGIC.balance_of_ui(&tok, ALICE).unwrap(), U256::from(100u64));
        assert_eq!(LOGIC.total_supply_ui(&tok).unwrap(), U256::from(100u64));

        // After maturity: doubled.
        set_now(&mut tok, U256::from(100u64));
        assert_eq!(LOGIC.to_scaled_balance(&tok, U256::from(10u64)).unwrap(), U256::from(20u64));
        assert_eq!(LOGIC.to_raw_balance(&tok, U256::from(20u64)).unwrap(), U256::from(10u64));
        assert_eq!(LOGIC.scaled_balance_of(&tok, ALICE).unwrap(), U256::from(200u64));
        assert_eq!(LOGIC.balance_of_ui(&tok, ALICE).unwrap(), U256::from(200u64));
        assert_eq!(LOGIC.total_supply_ui(&tok).unwrap(), U256::from(200u64));
    }
```
