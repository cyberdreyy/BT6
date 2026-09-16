## Title
Missing upper-bound validation on `update_multiplier` in the B-20 Asset precompile V1 (Beryl) logic — unbounded multiplier can break the WAD-scaled balance accounting - (File: crates/common/precompiles/src/b20_asset/logic/v1.rs)

## Summary
The Sherlock finding flags `setSlippageThreshold` for lacking an upper-bound (`MAX_BPS`) check, letting downstream math over/underflow or degenerate to zero. The same class of bug — a privileged setter that validates only the lower bound (non-zero) and omits any maximum-cap check that a *later* version of the same code explicitly added — exists in the B-20 Asset precompile's `update_multiplier` function at the V1 (Beryl) fork.

## Finding Description
`update_multiplier` in the V1 logic implementation only guards against a zero multiplier: [1](#0-0) 

It performs the operator-role check, rejects `new_multiplier == 0`, and then unconditionally stores the caller-supplied `U256` value via `token.accounting_mut().set_multiplier(new_multiplier)?`, with no upper-bound comparison.

By contrast, the V2 (Cobalt) implementation of the same asset logic introduces and enforces an explicit maximum: `MAX_UI_MULTIPLIER` equal to `type(uint128).max`, and the error definition documents that this guard exists specifically because `updateMultiplier` (and its scheduled-update analog) must not accept a multiplier "of zero or above the `type(uint128).max` overflow guard": [2](#0-1) [3](#0-2) 

The multiplier is a fixed-point value scaled by `WAD_PRECISION` (`1e18`) and is used to convert between raw internal shares and externally reported balances/transfers for the asset token: [4](#0-3) 

Because V1's setter accepts any nonzero `U256` up to `U256::MAX`, an operator-privileged call can set a multiplier far beyond `type(uint128).max`. Any subsequent multiplication of a share balance by this multiplier (as V2's own guard comment confirms is the overflow risk being defended against) can overflow the intermediate computation in balance/transfer paths that were sized assuming the V2 cap holds.

## Impact Explanation
An out-of-range multiplier set through the unguarded V1 setter can cause balance-scaling arithmetic to overflow or wrap, corrupting reported balances for all holders of the asset token. Depending on how the overflow manifests downstream (revert vs. wrap), this can either permanently freeze all transfers/balance reads for the token (denial of service on the asset) or produce incorrect balances that misrepresent holder funds — both fall within the "permanent freezing of funds" / unbacked-supply-adjacent impact categories. This mirrors exactly why the V2 fork subsequently hardened the same setter with the `MAX_UI_MULTIPLIER` cap and dedicated `InvalidMultiplier` guard.

## Likelihood Explanation
The call requires the `OPERATOR_ROLE` (or the `privileged` internal-call path), so it is not reachable by an arbitrary unprivileged address, but it is reachable by any authorized operator through a normal signed transaction to the precompile's dispatch table — no protocol-level or off-chain gating prevents an operator from supplying a multiplier above `type(uint128).max` on a V1-forked token. The fact that the exact same code path was tightened in the next version (with an explicit test pinning the cap) is strong evidence the omission in V1 is a genuine, exploitable gap rather than an intentional design choice.

## Recommendation
Add the same `new_multiplier > MAX_UI_MULTIPLIER` (i.e., `type(uint128).max`) check to V1's `update_multiplier` that V2 already enforces, reverting with `IB20Asset::InvalidMultiplier` when the supplied value exceeds the safe bound, matching the precedent set at `crates/common/precompiles/src/b20_asset/logic/v2.rs`.

## Proof of Concept
1. Deploy/route a V1 (Beryl) `B20Asset` token via the precompile dispatcher.
2. As an address holding `OPERATOR_ROLE`, call `updateMultiplier(U256::MAX)` (or any value `> type(uint128).max`).
3. `update_multiplier` in `logic/v1.rs` only checks `is_zero()`, so the call succeeds and `set_multiplier(U256::MAX)` is persisted.
4. Any balance/transfer calculation that scales raw shares by the multiplier (as guarded against by V2's `MAX_UI_MULTIPLIER` check) can now overflow, corrupting or freezing balances for all holders of the token — reproducing the same class of impact the original Sherlock report described for the unbounded `slippageThreshold`. [1](#0-0) [2](#0-1)

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

**File:** crates/common/precompiles/src/b20_asset/abi/v1.rs (L20-22)
```rust
        /// A multiplier setter (`updateMultiplier`) was called with a multiplier of zero or above
        /// the `type(uint128).max` overflow guard.
        error InvalidMultiplier();
```

**File:** crates/common/precompiles/src/b20_asset/abi/v1.rs (L56-60)
```rust
        /// `keccak256("OPERATOR_ROLE")` — required for `announce` and `updateMultiplier`.
        function OPERATOR_ROLE() external view returns (bytes32);

        /// Fixed-point precision for `multiplier`: `1e18` (one WAD).
        function WAD_PRECISION() external view returns (uint256);
```

**File:** crates/common/precompiles/src/b20_asset/logic/v2.rs (L2670-2679)
```rust
    #[test]
    fn max_ui_multiplier_getter_returns_uint128_max() {
        // Pins the const definition (the U256 limbs must equal type(uint128).max, the value the
        // setter guards enforce) and the getter that exposes it.
        assert_eq!(AssetV2::MAX_UI_MULTIPLIER, U256::from(u128::MAX));
        let value =
            <AssetV2 as Asset<FakeAccounting, FakePolicyAccounting>>::max_ui_multiplier(&LOGIC)
                .unwrap();
        assert_eq!(value, U256::from(u128::MAX));
    }
```
