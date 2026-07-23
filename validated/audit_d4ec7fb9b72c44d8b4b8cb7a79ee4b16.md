### Title
`AnchoredPriceProvider` uses L1-only staleness check with no L2 variant, causing valid oracle data to be rejected as stale on L2 and breaking all pool swaps — (`File: smart-contracts-poc/contracts/AnchoredPriceProvider.sol`)

---

### Summary

`AnchoredPriceProvider` is described as "the one standard provider for public pools." Its `_isStale` helper unconditionally treats any `refTime > block.timestamp` as stale. On L2 networks (Arbitrum, Optimism), oracle timestamps from Pyth can be slightly ahead of `block.timestamp` due to sequencer clock skew. The codebase already acknowledges this problem and ships `PriceProviderL2` and `ProtectedPriceProviderL2` with a `FUTURE_TOLERANCE` parameter to handle it — but no `AnchoredPriceProviderL2` exists. When `AnchoredPriceProvider` is deployed on L2, every oracle update whose `refTime` is even 1 second ahead of the L2 block timestamp is rejected as stale, causing `_readLeg` to return `ok = false`, `getBidAndAskPrice` to revert with `FeedStalled`, and all pool swaps to be permanently broken.

---

### Finding Description

`AnchoredPriceProvider._isStale` is a three-argument function that returns `true` (stale) whenever `refTime > nowTs`:

```solidity
// AnchoredPriceProvider.sol lines 222-230
function _isStale(uint256 refTime, uint256 nowTs, uint256 maxDelta) internal pure returns (bool) {
    if (refTime == 0) return true;
    if (refTime > nowTs) return true;   // ← hard L1 assumption: future = stale
    return (nowTs - refTime) > maxDelta;
}
``` [1](#0-0) 

The L2-aware sibling `PriceProviderL2._isStale` takes a fourth argument `futureTol` and only rejects future timestamps that exceed the tolerance:

```solidity
// PriceProviderL2.sol lines 135-150
function _isStale(uint256 refTime, uint256 nowTs, uint256 maxDelta, uint256 futureTol) internal pure returns (bool) {
    if (refTime == 0) return true;
    if (refTime > nowTs) {
        return (refTime - nowTs) > futureTol;   // ← tolerates sequencer clock skew
    }
    return (nowTs - refTime) > maxDelta;
}
``` [2](#0-1) 

The directory listing confirms the asymmetry: `PriceProviderL2.sol` and `ProtectedPriceProviderL2.sol` exist, but there is no `AnchoredPriceProviderL2.sol`.



When `_isStale` returns `true` inside `_readLeg`, the leg returns `ok = false`:

```solidity
// AnchoredPriceProvider.sol lines 283
if (_isStale(refTime, block.timestamp, MAX_REF_STALENESS)) return (mid, spreadBps, refTime, false);
``` [3](#0-2) 

`_getBidAndAskPrice` propagates the failure as the `(0, type(uint128).max)` sentinel:

```solidity
// AnchoredPriceProvider.sol lines 259-260
(uint256 mid, uint256 spreadBps, , bool ok) = _readLeg(baseFeedId);
if (!ok) return (0, type(uint128).max);
``` [4](#0-3) 

And `getBidAndAskPrice` reverts with `FeedStalled`:

```solidity
// AnchoredPriceProvider.sol lines 214-217
function getBidAndAskPrice() external override returns (uint128 bid, uint128 ask) {
    (bid, ask) = _getBidAndAskPrice();
    if (bid == 0 || ask == type(uint128).max) revert FeedStalled();
}
``` [5](#0-4) 

The pool calls `getBidAndAskPrice()` during every swap. A persistent `FeedStalled` revert means no swap can execute.

---

### Impact Explanation

Every swap through a pool whose price provider is `AnchoredPriceProvider` reverts with `FeedStalled` whenever the Pyth oracle's `refTime` is ahead of the L2 `block.timestamp`. On Arbitrum and Optimism, Pyth timestamps routinely lead the sequencer's block timestamp by seconds to tens of seconds. The result is a completely broken swap path — the core pool functionality — for the duration of the clock skew, which can be continuous. This matches the "Broken core pool functionality causing loss of funds or unusable swap/liquidity flows" impact gate.

---

### Likelihood Explanation

`AnchoredPriceProvider` is explicitly documented as "the one standard provider for public pools," making L2 deployment highly probable. Pyth's off-chain price service publishes timestamps derived from its own clock, which on L2 sequencers regularly exceeds `block.timestamp`. The `PriceProviderL2` constructor allows `FUTURE_TOLERANCE` up to 1 hour, confirming the protocol team is aware the skew can be significant. No deployment guard or chain-ID check prevents `AnchoredPriceProvider` from being used on L2. [6](#0-5) 

---

### Recommendation

Create `AnchoredPriceProviderL2` that mirrors `PriceProviderL2`'s approach: add an immutable `FUTURE_TOLERANCE` (bounded to ≤ 1 hour at construction) and replace the three-argument `_isStale` with the four-argument version that tolerates `refTime` up to `FUTURE_TOLERANCE` seconds ahead of `block.timestamp`. Alternatively, add `FUTURE_TOLERANCE` as an immutable to `AnchoredPriceProvider` itself (defaulting to 0 for L1 deployments) and use the four-argument staleness check unconditionally.

---

### Proof of Concept

1. Deploy `AnchoredPriceProvider` on Arbitrum with a Pyth-backed `offchainOracle`.
2. Pyth publishes a price update with `refTime = block.timestamp + 5` (5 seconds ahead — normal sequencer skew).
3. Pool calls `getBidAndAskPrice()` during a swap.
4. `_readLeg` calls `_isStale(refTime=T+5, nowTs=T, MAX_REF_STALENESS)`.
5. `if (refTime > nowTs) return true` fires immediately.
6. `_readLeg` returns `ok = false`.
7. `_getBidAndAskPrice` returns `(0, type(uint128).max)`.
8. `getBidAndAskPrice` reverts with `FeedStalled`.
9. The swap reverts. All swaps revert for as long as the clock skew persists.

### Citations

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L214-217)
```text
    function getBidAndAskPrice() external override returns (uint128 bid, uint128 ask) {
        (bid, ask) = _getBidAndAskPrice();
        if (bid == 0 || ask == type(uint128).max) revert FeedStalled();
    }
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L222-230)
```text
    function _isStale(
        uint256 refTime,
        uint256 nowTs,
        uint256 maxDelta
    ) internal pure returns (bool) {
        if (refTime == 0) return true;
        if (refTime > nowTs) return true;
        return (nowTs - refTime) > maxDelta;
    }
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L259-260)
```text
        (uint256 mid, uint256 spreadBps, , bool ok) = _readLeg(baseFeedId);
        if (!ok) return (0, type(uint128).max);
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L282-284)
```text
        // Stale reference → not ok. Clamping to a stale anchor is the one false-safety case.
        if (_isStale(refTime, block.timestamp, MAX_REF_STALENESS)) return (mid, spreadBps, refTime, false);

```

**File:** smart-contracts-poc/contracts/PriceProviderL2.sol (L92-95)
```text
        if (_maxTimeDelta == 0 || _maxTimeDelta > 7 days) revert MaxTimeDeltaOutOfBounds();
        if (_futureTolerance > 1 hours) revert FutureToleranceOutOfBounds();
        MAX_TIME_DELTA   = _maxTimeDelta;
        FUTURE_TOLERANCE = _futureTolerance;
```

**File:** smart-contracts-poc/contracts/PriceProviderL2.sol (L135-150)
```text
    function _isStale(
        uint256 refTime,
        uint256 nowTs,
        uint256 maxDelta,
        uint256 futureTol
    ) internal pure returns (bool) {
        if (refTime == 0) return true;

        if (refTime > nowTs) {
            // refTime in the future: tolerate only within futureTol
            return (refTime - nowTs) > futureTol;
        }

        // refTime in the past or equal: check age
        return (nowTs - refTime) > maxDelta;
    }
```
