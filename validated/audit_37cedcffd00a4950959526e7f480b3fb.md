### Title
Pool Admin Bypasses `maxAdminSpreadFeeE6` Cap via Uncapped `setPoolBinAdditionalFees` — (`metric-core/contracts/MetricOmmPoolFactory.sol`)

---

### Summary

`setPoolBinAdditionalFees` passes `addFeeBuyE6` / `addFeeSellE6` directly to the pool with no validation against `maxAdminSpreadFeeE6`, while the parallel `setPoolAdminFees` path explicitly enforces that cap. A pool admin can set per-bin additional fees up to `uint16` max (65 535 = 6.5535% in E6 scale), exceeding any `maxAdminSpreadFeeE6` the factory owner has configured, and causing traders to pay more than the protocol-intended ceiling on every swap through the affected bin.

---

### Finding Description

The factory owner enforces a fee ceiling for pool admins through `maxAdminSpreadFeeE6` (set via `setFeeCaps`, hard-capped at `HARD_MAX_SPREAD_FEE_E6 = 200_000`). The `setPoolAdminFees` path correctly enforces this:

```solidity
// MetricOmmPoolFactory.sol:414-415
if (newAdminSpreadFeeE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
if (newAdminNotionalFeeE8 > maxAdminNotionalFeeE8) revert AdminFeeTooHigh();
``` [1](#0-0) 

However, the per-bin additional fee setter has no such guard:

```solidity
// MetricOmmPoolFactory.sol:450-457
function setPoolBinAdditionalFees(address pool, int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external override nonReentrant onlyPoolAdmin(pool)
{
    IMetricOmmPoolFactoryActions(pool).setBinAdditionalFees(bin, addFeeBuyE6, addFeeSellE6);
}
``` [2](#0-1) 

The pool's `setBinAdditionalFees` also performs no cap check — only a bin-index range check:

```solidity
// MetricOmmPool.sol:464-474
function setBinAdditionalFees(int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external onlyFactory nonReentrant(PoolActions.SET_BIN_ADDITIONAL_FEES)
{
    if (bin < LOWEST_BIN || bin > HIGHEST_BIN) revert InvalidBinIndex(bin);
    BinState storage s = _binStates[bin];
    s.addFeeBuyE6 = addFeeBuyE6;
    s.addFeeSellE6 = addFeeSellE6;
    emit BinAdditionalFeesUpdated(bin, addFeeBuyE6, addFeeSellE6);
}
``` [3](#0-2) 

During every swap, the per-bin additional fee is added directly on top of the base spread fee with no ceiling:

```solidity
// MetricOmmPool.sol:910
params.baseFeeX64 + Math.mulDiv(binState.addFeeBuyE6, ONE_X64, 1e6)
``` [4](#0-3) 

The `uint16` type bounds `addFeeBuyE6` / `addFeeSellE6` to a maximum of 65 535 (6.5535% in E6 scale). This is entirely independent of — and can far exceed — any `maxAdminSpreadFeeE6` the factory owner has set (e.g., 1 000 = 0.1%).

---

### Impact Explanation

Every swap through the affected bin pays `baseFee + addFeeBuyE6` (or `addFeeSellE6`). If the pool admin sets `addFeeBuyE6 = 65_535` while `maxAdminSpreadFeeE6 = 1_000`, traders pay up to 6.5535% additional spread fee per swap — 65× the intended cap. The excess spread fee accrues to LPs (not the admin directly), but the trader suffers a direct loss of principal beyond what the protocol's fee ceiling permits. This is an admin-boundary break: the pool admin exceeds the cap the factory owner explicitly configured to bound them.

---

### Likelihood Explanation

The pool admin role is semi-trusted and bounded. Any pool admin who wishes to extract maximum value from traders (e.g., by also holding LP positions) can call `setPoolBinAdditionalFees` with `addFeeBuyE6 = type(uint16).max` at any time with no timelock, no cooldown, and no on-chain guard. The call requires only `onlyPoolAdmin(pool)`.

---

### Recommendation

Add a cap check in `setPoolBinAdditionalFees` (or inside `setBinAdditionalFees`) analogous to the one in `setPoolAdminFees`:

```solidity
function setPoolBinAdditionalFees(address pool, int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external override nonReentrant onlyPoolAdmin(pool)
{
    if (addFeeBuyE6  > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
    if (addFeeSellE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
    IMetricOmmPoolFactoryActions(pool).setBinAdditionalFees(bin, addFeeBuyE6, addFeeSellE6);
}
```

Alternatively, enforce the cap inside `setBinAdditionalFees` on the pool itself by passing the current cap through the factory call.

---

### Proof of Concept

1. Factory owner calls `setFeeCaps(200_000, 1_000, 1_000_000, 1_000_000)` — setting `maxAdminSpreadFeeE6 = 1_000` (0.1%).
2. Pool admin calls `setPoolAdminFees(pool, 1_001, 0)` → reverts with `AdminFeeTooHigh`. Cap is enforced.
3. Pool admin calls `setPoolBinAdditionalFees(pool, 0, 65_535, 65_535)` → **succeeds**. No cap check.
4. A trader swaps through bin 0. The effective buy fee is `baseFeeX64 + mulDiv(65_535, ONE_X64, 1e6)` — 6.5535% additional spread on top of the base fee, far exceeding the 0.1% cap the factory owner intended.
5. The trader loses principal equal to the excess fee (up to 6.5535% of notional per swap through that bin). [5](#0-4) [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L284-295)
```text
  function setFeeCaps(
    uint24 newMaxProtocolSpreadFeeE6,
    uint24 newMaxAdminSpreadFeeE6,
    uint24 newMaxProtocolNotionalFeeE8,
    uint24 newMaxAdminNotionalFeeE8
  ) external override onlyOwner {
    if (
      newMaxProtocolSpreadFeeE6 > HARD_MAX_SPREAD_FEE_E6 || newMaxAdminSpreadFeeE6 > HARD_MAX_SPREAD_FEE_E6
        || newMaxProtocolNotionalFeeE8 > HARD_MAX_NOTIONAL_FEE_E8 || newMaxAdminNotionalFeeE8 > HARD_MAX_NOTIONAL_FEE_E8
    ) {
      revert FeeCapsExceedHardLimit();
    }
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L408-415)
```text
  function setPoolAdminFees(address pool, uint24 newAdminSpreadFeeE6, uint24 newAdminNotionalFeeE8)
    external
    override
    nonReentrant
    onlyPoolAdmin(pool)
  {
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

**File:** metric-core/contracts/MetricOmmPool.sol (L906-915)
```text
          (curPosInBinCache, delta0Scaled, delta1Scaled, binLpFeeAmountScaled) = SwapMath.buyToken0InBinSpecifiedOut(
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
