### Title
Pool Admin Can Set Unbounded Per-Bin Additional Fees, Bypassing Factory Hard Caps - (File: metric-core/contracts/MetricOmmPoolFactory.sol)

### Summary

`setPoolBinAdditionalFees` passes `addFeeBuyE6` and `addFeeSellE6` directly to the pool with no upper-bound check, while every other fee setter in the factory is bounded by hard caps. A pool admin can set per-bin fees to `uint16.max` (65,535 in E6 scale = 6.5535%) on any bin, stacking on top of the already-capped global spread fee and causing traders to pay effective fees that exceed the protocol's hard ceiling.

### Finding Description

The factory enforces a two-layer fee cap system:

- **Hard ceiling**: `HARD_MAX_SPREAD_FEE_E6 = 200_000` (20%) — immutable constant.
- **Soft caps**: `maxAdminSpreadFeeE6` / `maxProtocolSpreadFeeE6` — owner-settable, bounded by the hard ceiling.

Every fee setter respects these caps:

`setPoolAdminFees` checks `if (newAdminSpreadFeeE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();` [1](#0-0) 

`setFeeCaps` checks against `HARD_MAX_SPREAD_FEE_E6`: [2](#0-1) 

But `setPoolBinAdditionalFees` forwards values directly with **no cap check**:

```solidity
function setPoolBinAdditionalFees(address pool, int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external override nonReentrant onlyPoolAdmin(pool)
{
    IMetricOmmPoolFactoryActions(pool).setBinAdditionalFees(bin, addFeeBuyE6, addFeeSellE6);
}
``` [3](#0-2) 

The pool stores these values without any validation either: [4](#0-3) 

During every swap, the per-bin fee is added directly on top of the oracle-derived base fee:

```solidity
params.baseFeeX64 + Math.mulDiv(binState.addFeeBuyE6, ONE_X64, 1e6),
``` [5](#0-4) 

The same additive pattern applies to the sell direction: [6](#0-5) 

`addFeeBuyE6` / `addFeeSellE6` are `uint16`, so the pool admin can set them to `65,535` (6.5535% in E6 scale) with no revert. Combined with a global `spreadFeeE6` already at the 20% hard cap, the effective fee for that bin reaches **26.5535%** — 6.5535 percentage points above the protocol's immutable ceiling.

The `BinState` struct confirms the unconstrained `uint16` fields: [7](#0-6) 

### Impact Explanation

Traders swapping through the affected bin pay effective fees exceeding the factory's hard cap. The excess fee is extracted from the trader's input amount and distributed to LPs/protocol, constituting a direct loss of user principal. The hard cap (`HARD_MAX_SPREAD_FEE_E6 = 200_000`) is the protocol's stated maximum; per-bin fees silently bypass it. [8](#0-7) 

### Likelihood Explanation

The pool admin is a semi-trusted role that is explicitly supposed to be bounded by the factory's caps. The trigger requires only a single call to `setPoolBinAdditionalFees` with `addFeeBuyE6 = 65535` — no special conditions, no timelock, no co-signer. Any pool admin (including one who turns adversarial after deployment) can execute this immediately.

### Recommendation

Add a hard-cap check inside `setPoolBinAdditionalFees` before forwarding to the pool, analogous to the check in `setPoolAdminFees`:

```solidity
function setPoolBinAdditionalFees(address pool, int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external override nonReentrant onlyPoolAdmin(pool)
{
    if (addFeeBuyE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
    if (addFeeSellE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
    IMetricOmmPoolFactoryActions(pool).setBinAdditionalFees(bin, addFeeBuyE6, addFeeSellE6);
}
```

Alternatively, introduce a dedicated `maxBinAdditionalFeeE6` constant and enforce it here and at pool creation time when bin data is unpacked.

### Proof of Concept

```solidity
// Pool admin sets per-bin fee to uint16 max — no revert
factory.setPoolBinAdditionalFees(pool, 0, 65_535, 65_535);

// Effective buy fee for bin 0 during swap:
// baseFeeX64 (oracle spread) + 65_535/1e6 (6.5535%)
// + spreadFeeE6 (up to 20% global cap)
// = up to 26.5535% effective fee — exceeds HARD_MAX_SPREAD_FEE_E6 (20%)

// Trader calling swap() through bin 0 pays 6.5535% more than the
// protocol's immutable hard ceiling permits.
```

The factory test at line 375 confirms `setPoolBinAdditionalFees` accepts arbitrary values with no revert: [9](#0-8) 

No test exists that attempts to set `addFeeBuyE6` above `maxAdminSpreadFeeE6` and expects a revert — confirming the missing guard is untested.

### Citations

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L43-45)
```text
  /// @dev Owner `setFeeCaps` values cannot exceed these (spread: 1e6 = 100%; notional: 1e8 = 100%)
  uint24 internal constant HARD_MAX_SPREAD_FEE_E6 = 200_000;
  uint24 internal constant HARD_MAX_NOTIONAL_FEE_E8 = 1_000_000;
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L290-295)
```text
    if (
      newMaxProtocolSpreadFeeE6 > HARD_MAX_SPREAD_FEE_E6 || newMaxAdminSpreadFeeE6 > HARD_MAX_SPREAD_FEE_E6
        || newMaxProtocolNotionalFeeE8 > HARD_MAX_NOTIONAL_FEE_E8 || newMaxAdminNotionalFeeE8 > HARD_MAX_NOTIONAL_FEE_E8
    ) {
      revert FeeCapsExceedHardLimit();
    }
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L414-415)
```text
    if (newAdminSpreadFeeE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
    if (newAdminNotionalFeeE8 > maxAdminNotionalFeeE8) revert AdminFeeTooHigh();
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L450-457)
```text
  function setPoolBinAdditionalFees(address pool, int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external
    override
    nonReentrant
    onlyPoolAdmin(pool)
  {
    IMetricOmmPoolFactoryActions(pool).setBinAdditionalFees(bin, addFeeBuyE6, addFeeSellE6);
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L464-474)
```text
  function setBinAdditionalFees(int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external
    onlyFactory
    nonReentrant(PoolActions.SET_BIN_ADDITIONAL_FEES)
  {
    if (bin < LOWEST_BIN || bin > HIGHEST_BIN) revert InvalidBinIndex(bin);
    BinState storage s = _binStates[bin];
    s.addFeeBuyE6 = addFeeBuyE6;
    s.addFeeSellE6 = addFeeSellE6;
    emit BinAdditionalFeesUpdated(bin, addFeeBuyE6, addFeeSellE6);
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L994-1004)
```text
          (curPosInBinCache, outToken0AmountScaled, delta0Scaled, delta1Scaled, binLpFeeAmountScaled) =
            SwapMath.buyToken0InBinSpecifiedIn(
              binState,
              curPosInBinCache,
              state,
              params.baseFeeX64 + Math.mulDiv(binState.addFeeBuyE6, ONE_X64, 1e6),
              lowerPriceX64,
              upperPriceX64,
              params.priceLimitX64,
              spreadFeeE6
            );
```

**File:** metric-core/contracts/MetricOmmPool.sol (L1172-1182)
```text
          (curPosInBinCache, outToken1AmountScaled, delta0Scaled, delta1Scaled, binLpFeeAmountScaled) =
            SwapMath.buyToken1InBinSpecifiedIn(
              binState,
              curPosInBinCache,
              state,
              params.baseFeeX64 + Math.mulDiv(binState.addFeeSellE6, ONE_X64, 1e6),
              lowerPriceX64,
              upperPriceX64,
              params.priceLimitX64,
              spreadFeeE6
            );
```

**File:** metric-core/contracts/types/PoolStorage.sol (L19-25)
```text
struct BinState {
  uint104 token0BalanceScaled;
  uint104 token1BalanceScaled;
  uint16 lengthE6;
  uint16 addFeeBuyE6;
  uint16 addFeeSellE6;
}
```

**File:** metric-core/test/MetricOmmPoolFactory.t.sol (L375-400)
```text
  function test_setPoolBinAdditionalFees_updatesStorage_emitsEvent() public {
    address pool = _createPool();
    (,,, uint16 buy0Before, uint16 sell0Before) = PoolStateLibrary._binState(pool, 0);
    assertEq(buy0Before, 0);
    assertEq(sell0Before, 0);

    vm.expectEmit(true, false, false, true, pool);
    emit IMetricOmmPoolFactoryActions.BinAdditionalFeesUpdated(int8(0), uint16(500), uint16(700));

    vm.prank(admin);
    factory.setPoolBinAdditionalFees(pool, 0, 500, 700);

    (,,, uint16 buy0After, uint16 sell0After) = PoolStateLibrary._binState(pool, 0);
    assertEq(buy0After, 500);
    assertEq(sell0After, 700);

    (,,, uint16 buyNeg, uint16 sellNeg) = PoolStateLibrary._binState(pool, -1);
    assertEq(buyNeg, 0);
    assertEq(sellNeg, 0);

    vm.prank(admin);
    factory.setPoolBinAdditionalFees(pool, -1, 10, 20);
    (,,, buyNeg, sellNeg) = PoolStateLibrary._binState(pool, -1);
    assertEq(buyNeg, 10);
    assertEq(sellNeg, 20);
  }
```
