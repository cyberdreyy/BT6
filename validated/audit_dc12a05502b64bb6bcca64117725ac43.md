### Title
Missing upper bound on `AssetV1::update_multiplier` allows the operator role to set an overflow-inducing multiplier, permanently breaking scaled-balance reads and conversions - ([File: crates/common/precompiles/src/b20_asset/logic/v1.rs])

### Summary
The B20 asset precompile's V1 logic exposes `update_multiplier`, callable by any account holding `OPERATOR_ROLE`, which only rejects a zero value and has no upper bound check. The V2 logic (`AssetV2::update_multiplier` / `update_ui_multiplier`) explicitly guards against this by capping the multiplier at `MAX_UI_MULTIPLIER` (`type(uint128).max`), but the V1 path never received the same fix.

### Finding Description
`AssetV1::update_multiplier` performs only a zero check before writing the new multiplier: [1](#0-0) 

Compare this to `AssetV2::update_multiplier`, which enforces both a zero-check *and* an upper-bound check against `MAX_UI_MULTIPLIER = type(uint128).max`: [2](#0-1) 

The rationale for `MAX_UI_MULTIPLIER` is documented directly in V2: keeping the multiplier within `uint128` guarantees `balance * multiplier` never overflows `uint256` in the scaled-balance conversions: [3](#0-2) 

V1 defines the same overflow-sensitive conversions (`to_scaled_balance`, `to_raw_balance`, `scaled_balance_of`, etc.) that multiply `balance * multiplier` via `checked_mul`, which reverts with an overflow panic once the product exceeds `U256::MAX`: [4](#0-3) 

Because `update_multiplier` in V1 has no analogous `MAX_UI_MULTIPLIER` guard, an `OPERATOR_ROLE` holder can set an arbitrarily large multiplier (up to `U256::MAX`), causing every subsequent scaled-balance computation for that token to revert with an overflow error.

### Impact Explanation
This is the direct structural analog of the reported bug class: a privileged, non-owner role (`OPERATOR_ROLE`, analogous to "management") can set a numeric parameter (the multiplier) with a minimum guard but no maximum guard, and that unbounded value causes the strategy's core accounting to permanently break. Here, any code path relying on the scaled-balance conversions (`toScaledBalance`, `toRawBalance`, `balanceOfUI`, `totalSupplyUI`, and any downstream mint/burn/transfer logic that goes through UI-amount conversion) will revert with an overflow panic once the multiplier is set high enough, freezing user-facing balance/UI operations on that B-20 asset until an operator corrects the value again — an availability/DoS impact directly tied to a missing bound on an admin-settable parameter, reachable purely via a signed precompile call.

### Likelihood Explanation
Likelihood depends on whether `OPERATOR_ROLE` is treated as a trusted-but-fallible role (matching the original report's "management" trust assumption) or is more widely distributed to token deployers/creators of B20 assets. Since `update_multiplier` requires only `ensure_operator_role`, any account holding that role for a given B20 asset can trigger it in a single transaction, with no additional preconditions, making exploitation (accidental or deliberate) straightforward once the role is held.

### Recommendation
Apply V2's `MAX_UI_MULTIPLIER` upper-bound check to `AssetV1::update_multiplier`, rejecting any `new_multiplier` above `type(uint128).max` (or another safe ceiling that guarantees `balance * multiplier` cannot overflow `U256`), matching the guard already present in `AssetV2::update_multiplier` / `update_ui_multiplier`.

### Proof of Concept
1. Grant an account `OPERATOR_ROLE` on a V1 B20 asset (or use an account that already holds it).
2. Call `updateMultiplier` with `new_multiplier` close to `U256::MAX` (e.g., `U256::MAX / 2 + 1`, as used in the existing overflow test) — this succeeds because V1's check is only `new_multiplier.is_zero()`.
3. Any subsequent call to a scaled-balance function (`toScaledBalance`, `balanceOfUI`, `totalSupplyUI`, `toRawBalance`) for a non-trivial balance now reverts with an overflow panic, as demonstrated by the existing unit test pattern: [4](#0-3)

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

**File:** crates/common/precompiles/src/b20_asset/logic/v1.rs (L1456-1464)
```rust
    #[test]
    fn to_scaled_balance_overflows_when_product_exceeds_u256_max() {
        let mut tok = token();
        tok.accounting_mut().set_multiplier(U256::MAX / U256::from(2u64) + U256::ONE).unwrap();
        assert_eq!(
            LOGIC.to_scaled_balance(&tok, U256::from(2u64)).unwrap_err(),
            BasePrecompileError::under_overflow()
        );
    }
```

**File:** crates/common/precompiles/src/b20_asset/logic/v2.rs (L55-59)
```rust
    /// Upper bound the multiplier setters accept: `type(uint128).max`. With supply capped at
    /// `type(uint128).max`, a `uint128` multiplier keeps `balance * multiplier` inside `uint256`,
    /// so balance-derived reads never overflow. Single source of truth for the setter guards and
    /// the `MAX_UI_MULTIPLIER()` getter.
    pub const MAX_UI_MULTIPLIER: U256 = U256::from_limbs([u64::MAX, u64::MAX, 0, 0]);
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
