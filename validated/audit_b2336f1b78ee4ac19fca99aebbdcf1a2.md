### Title
Zero-initialized `confidenceParam` silently discards oracle spread in `PriceProvider` and `ProtectedPriceProvider`, allowing unclamped tight quotes to reach pool swaps — (`File: smart-contracts-poc/contracts/PriceProvider.sol`, `smart-contracts-poc/contracts/ProtectedPriceProvider.sol`)

---

### Summary

`PriceProvider` and `ProtectedPriceProvider` both zero-initialize `confidenceParam` at deployment. With `confidenceParam = 0`, the oracle's reported spread (uncertainty) is silently multiplied to zero and completely discarded. The bid/ask spread delivered to the pool is computed solely from the immutable `marginStep`, regardless of how wide the oracle's live confidence interval is. Every newly deployed provider is in this broken state until the factory admin explicitly calls `setConfidenceParam`. There is no guard that blocks swaps while `confidenceParam == 0`.

---

### Finding Description

In `PriceProvider._getBidAndAskPrice()`:

```solidity
// 5. Compute bid/ask from mid + confidence-adjusted spread
//    confidenceParam multiplies oracle spread; 0 means no spread
uint256 adjustedSpread = spread * confidenceParam;   // ← always 0 at deployment
(uint256 bid, uint256 ask) = _getBidAskFrom(mid, adjustedSpread);
``` [1](#0-0) 

`_getBidAskFrom` with `confidence = 0` produces `delta = 0`, so `bid = ask = mid`. The only spread that survives is the immutable `marginStep` applied by `_applyBidAdjustments` / `_applyAskAdjustments`:

```solidity
function _getBidAskFrom(uint256 midPrice, uint256 confidence) internal pure returns (uint256 bid, uint256 ask) {
    uint256 delta = midPrice * confidence / CONFIDENCE_BASE;  // = 0
    bid = delta >= midPrice ? 0 : midPrice - delta;           // = midPrice
    ask = midPrice + delta;                                   // = midPrice
}
``` [2](#0-1) 

The identical pattern exists in `ProtectedPriceProvider._computeBidAsk()`:

```solidity
uint256 adjustedSpread = spread * confidenceParam;
(uint256 bid, uint256 ask) = _getBidAskFrom(price, adjustedSpread);
``` [3](#0-2) 

`confidenceParam` is a plain storage slot, zero-initialized by Solidity, and never set in the constructor. The only setter is `setConfidenceParam`, which is gated to the factory admin:

```solidity
function setConfidenceParam(uint256 newValue) external {
    require(msg.sender == factory, OnlyFactory());
    ...
    confidenceParam = newValue;
``` [4](#0-3) 

There is no constructor parameter for `confidenceParam`, no deployment-time validation that it is non-zero, and no swap-time guard that halts the pool when `confidenceParam == 0`. [5](#0-4) 

Contrast with `AnchoredPriceProvider`, which does **not** have this defect: it builds the reference band directly from the oracle's `spreadBps` without multiplying by any mutable param, so the oracle uncertainty is always reflected:

```solidity
uint256 half = spreadBps * ONE_BPS_E18 + minMargin;
uint256 refBid = _bandEdge(mid, BPS_BASE_U - half, Math.Rounding.Floor);
uint256 refAsk = _bandEdge(mid, BPS_BASE_U + half, Math.Rounding.Ceil);
``` [6](#0-5) 

---

### Impact Explanation

When `confidenceParam == 0` and `marginStep > 0`, the pool receives a bid/ask spread of exactly `marginStep` basis-points regardless of the oracle's live confidence interval. If the oracle is reporting, say, 400 bps of uncertainty (a volatile market, thin liquidity, or a Pyth feed with a wide confidence band), the pool still quotes a spread of only `marginStep` bps. Informed traders can immediately arbitrage the mispriced pool, buying or selling at a price that is inside the oracle's true uncertainty band. LPs bear the full adverse-selection loss on every such swap. Repeated exploitation drains LP principal and can render the pool insolvent relative to LP claims.

This satisfies the allowed impact gate: **bad-price execution** (unclamped bid/ask quote reaches a pool swap) and **direct loss of LP assets**.

---

### Likelihood Explanation

The trigger is automatic and unprivileged:

1. A pool is deployed using `PriceProvider` or `ProtectedPriceProvider` (permissionless via the factory).
2. The factory admin does not call `setConfidenceParam` before the pool opens (or delays due to the 1-minute cooldown between updates).
3. Any trader calls `exactInputSingle` or equivalent on the pool.

The pool is live and accepting swaps from block 0 of deployment. The admin has no atomic way to set `confidenceParam` in the same transaction as pool creation. The 1-minute `CONFIDENCE_COOLDOWN` further delays any correction. During this window — and for any pool whose admin never sets the param — every swap executes against an oracle-spread-blind quote. [7](#0-6) 

---

### Recommendation

1. **Add `_confidenceParam` as a constructor argument** and validate it is non-zero (or document explicitly that zero is a deliberate "marginStep-only" mode and add a swap-time revert when `confidenceParam == 0` and `marginStep` is below a safe floor).
2. **Alternatively**, apply a minimum effective spread equal to `max(adjustedSpread, minSpreadBps)` so that even with `confidenceParam == 0` the oracle's raw `spread` is used as a floor.
3. **At minimum**, emit a deployment-time event or revert if `confidenceParam == 0` and `marginStep` is below a protocol-defined safe threshold, so integrators cannot silently deploy a spread-blind pool.

---

### Proof of Concept

```
Setup:
  PriceProvider deployed with marginStep = 1e14 (1 bps), maxTimeDelta = 1 hour
  confidenceParam = 0 (default, never set)

Oracle state (Pyth volatile market):
  mid      = 2000_00000000  (2000 USD, 8 decimals)
  spread   = 400            (400 bps confidence interval)
  refTime  = block.timestamp

Execution in PriceProvider._getBidAndAskPrice():
  adjustedSpread = 400 * 0 = 0
  delta          = 2000e8 * 0 / 1e10 = 0
  bid8 = ask8    = 2000e8

After marginStep (1 bps):
  stepBidFactor = 1e18 - 1e14 = 999900000000000000
  stepAskFactor = 1e18 + 1e14 = 1000100000000000000
  bidOut (Q64)  ≈ 2000e8 * Q64 * 0.9999 / 1e26
  askOut (Q64)  ≈ 2000e8 * Q64 * 1.0001 / 1e26
  → effective spread delivered to pool: 2 bps

Expected spread (with confidenceParam = 1, i.e., 1× oracle spread):
  adjustedSpread = 400 * 1 = 400
  delta          = 2000e8 * 400 / 1e10 = 8e8
  bid8 = 1992e8, ask8 = 2008e8
  → effective spread: ~80 bps

Attacker action:
  Swap token0→token1 at the 2 bps ask price while the true market is 40 bps above mid.
  Pool pays out token1 at a price 38 bps below fair value.
  LP suffers the loss on every such swap until confidenceParam is set.
```

### Citations

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L61-89)
```text
    constructor(
        address _factory,
        address _oracle,
        bytes32 _offchainFeedId,
        int256  _marginStep,
        uint256 _maxTimeDelta,
        address _baseToken,
        address _quoteToken
    ) {
        require(_factory != address(0));
        factory = _factory;

        offchainOracle = IOffchainOracle(_oracle);
        offchainFeedId = _offchainFeedId;

        require(_baseToken != address(0) && _quoteToken != address(0) && _baseToken != _quoteToken);
        baseToken = _baseToken;
        quoteToken = _quoteToken;

        if (_marginStep <= -BPS_BASE || _marginStep >= BPS_BASE) {
            revert MarginStepOutOfBounds();
        }
        marginStep       = _marginStep;
        stepBidFactor = uint256(BPS_BASE - _marginStep);
        stepAskFactor = uint256(BPS_BASE + _marginStep);

        if (_maxTimeDelta == 0 || _maxTimeDelta > 7 days) revert MaxTimeDeltaOutOfBounds();
        MAX_TIME_DELTA = _maxTimeDelta;
    }
```

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L92-104)
```text
    function setConfidenceParam(uint256 newValue) external {
        require(msg.sender == factory, OnlyFactory());
        if (newValue > CONFIDENCE_MAX) {
            revert ConfidenceParamOutOfBounds();
        }
        if (block.timestamp < lastConfidenceUpdate + CONFIDENCE_COOLDOWN) {
            revert CooldownNotElapsed();
        }

        confidenceParam = newValue;
        lastConfidenceUpdate = block.timestamp;
        emit ConfidenceParamSet(newValue);
    }
```

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L137-141)
```text
    function _getBidAskFrom(uint256 midPrice, uint256 confidence) internal pure returns (uint256 bid, uint256 ask) {
        uint256 delta = midPrice * confidence / CONFIDENCE_BASE;
        bid = delta >= midPrice ? 0 : midPrice - delta;
        ask = midPrice + delta;
    }
```

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L214-217)
```text
        // 5. Compute bid/ask from mid + confidence-adjusted spread
        //    confidenceParam multiplies oracle spread; 0 means no spread
        uint256 adjustedSpread = spread * confidenceParam;
        (uint256 bid, uint256 ask) = _getBidAskFrom(mid, adjustedSpread);
```

**File:** smart-contracts-poc/contracts/ProtectedPriceProvider.sol (L209-210)
```text
        uint256 adjustedSpread = spread * confidenceParam;
        (uint256 bid, uint256 ask) = _getBidAskFrom(price, adjustedSpread);
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L308-310)
```text
        uint256 half = spreadBps * ONE_BPS_E18 + minMargin; // < BPS_BASE_U by construction (spreadBps <= MAX_SPREAD_BPS here)
        uint256 refBid = _bandEdge(mid, BPS_BASE_U - half, Math.Rounding.Floor);
        uint256 refAsk = _bandEdge(mid, BPS_BASE_U + half, Math.Rounding.Ceil);
```
