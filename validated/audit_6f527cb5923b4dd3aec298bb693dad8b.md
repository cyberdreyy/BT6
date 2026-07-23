### Title
Uninitialized `confidenceParam` Causes Permanent `FeedStalled` Revert in `PriceProvider` and `ProtectedPriceProvider` When `marginStep = 0` — (File: `smart-contracts-poc/contracts/PriceProvider.sol`, `smart-contracts-poc/contracts/ProtectedPriceProvider.sol`)

---

### Summary

`PriceProvider` and `ProtectedPriceProvider` store `confidenceParam` as a zero-initialized storage variable with no constructor parameter to set it. When `marginStep = 0` (a valid and explicitly allowed deployment), the uninitialized `confidenceParam = 0` causes `getBidAndAskPrice()` to always revert with `FeedStalled()`, making every pool using these providers completely unable to execute swaps from the moment of deployment until the factory separately calls `setConfidenceParam()`.

---

### Finding Description

Both `PriceProvider` and `ProtectedPriceProvider` compute the bid/ask spread as:

```solidity
uint256 adjustedSpread = spread * confidenceParam;
(uint256 bid, uint256 ask) = _getBidAskFrom(mid, adjustedSpread);
``` [1](#0-0) 

`_getBidAskFrom` computes:

```solidity
uint256 delta = midPrice * confidence / CONFIDENCE_BASE;
bid = delta >= midPrice ? 0 : midPrice - delta;
ask = midPrice + delta;
``` [2](#0-1) 

When `confidenceParam = 0` (the Solidity default, never set in the constructor): `adjustedSpread = 0`, `delta = 0`, so `bid = ask = mid`.

The step-adjustment functions then apply `stepBidFactor` and `stepAskFactor`:

```solidity
(uint256 bidOut, bool bidOk) = _applyBidAdjustments(bid);
(uint256 askOut, bool askOk) = _applyAskAdjustments(ask);
if (bidOut >= askOut) return (0, type(uint128).max);
``` [3](#0-2) 

When `marginStep = 0` (explicitly allowed by the constructor guard `_marginStep <= -BPS_BASE || _marginStep >= BPS_BASE`):

- `stepBidFactor = BPS_BASE_U - 0 = 1e18`
- `stepAskFactor = BPS_BASE_U + 0 = 1e18` [4](#0-3) 

Both `_applyBidAdjustments(mid)` and `_applyAskAdjustments(mid)` compute `mid * Q64 * 1e18 / (1e8 * 1e18) = mid * Q64 / 1e8`, yielding `bidOut == askOut`. The `bidOut >= askOut` guard fires, returning the `(0, type(uint128).max)` sentinel, and `getBidAndAskPrice()` reverts with `FeedStalled()`. [5](#0-4) 

The constructor accepts no `_confidenceParam` argument — there is no way to initialize it at deployment: [6](#0-5) 

The same flaw exists identically in `ProtectedPriceProvider`: [7](#0-6) [8](#0-7) 

**Contrast with `AnchoredPriceProvider`:** that contract is immune because its `_computeBidAsk` applies a reference band clamp (`min(refBid, cBid)` / `max(refAsk, cAsk)`) that restores `bidOut < askOut` even when the shaped quote collapses to a point. `PriceProvider` and `ProtectedPriceProvider` have no such clamp. [9](#0-8) 

---

### Impact Explanation

Every pool whose price provider is a `PriceProvider` or `ProtectedPriceProvider` deployed with `marginStep = 0` is completely unable to execute swaps from the moment of deployment. The `FeedStalled` revert propagates through the pool's swap path, making the pool's core functionality (swap, and any swap-dependent liquidity flow) entirely unusable. Liquidity deposited before `setConfidenceParam()` is called is effectively locked in a non-functional pool.

---

### Likelihood Explanation

`marginStep = 0` is a natural default for a neutral provider (no directional bias). The constructor explicitly allows it. `confidenceParam` has no constructor parameter and no initialization path — it is always 0 at deployment. Any pool launched with these providers and `marginStep = 0` is immediately broken. The factory must make a separate `setConfidenceParam()` call; if this step is omitted or delayed, the pool is DoS'd for the entire interval.

---

### Recommendation

Add a `_confidenceParam` parameter to the `PriceProvider` and `ProtectedPriceProvider` constructors and initialize `confidenceParam` there, mirroring how `marginStep` is set at construction. Alternatively, add a guard in `_getBidAndAskPrice` that treats `confidenceParam == 0` as a valid "zero-spread" configuration by returning the mid-price band directly (analogous to the `AnchoredPriceProvider` reference-mode path), rather than collapsing to a degenerate `bid == ask` that fails the ordering invariant.

---

### Proof of Concept

1. Deploy `PriceProvider` with `_marginStep = 0`, any valid `_offchainFeedId`, and a live oracle returning `mid = 3000_00000000` (8-decimal), `spread = 50` (bps), `refTime = block.timestamp`.
2. Do **not** call `setConfidenceParam()`. `confidenceParam` remains `0`.
3. Call `getBidAndAskPrice()` from a pool.
4. Trace:
   - `adjustedSpread = 50 * 0 = 0`
   - `_getBidAskFrom(3000e8, 0)` → `bid = 3000e8`, `ask = 3000e8`
   - `_applyBidAdjustments(3000e8)` → `bidOut = 3000e8 * Q64 * 1e18 / 1e26 = 3000e8 * Q64 / 1e8`
   - `_applyAskAdjustments(3000e8)` → `askOut = same value`
   - `bidOut >= askOut` → `true` → returns `(0, type(uint128).max)`
   - `getBidAndAskPrice()` reverts: `FeedStalled()`
5. All swaps on the pool revert. Pool is unusable.
6. Factory calls `setConfidenceParam(10000)` (non-zero). Pool immediately recovers. [10](#0-9) [11](#0-10)

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

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L115-120)
```text
    function getBidAndAskPrice()
        external override returns (uint128 bid, uint128 ask)
    {
        (bid, ask) = _getBidAndAskPrice();
        if (bid == 0 || ask == type(uint128).max) revert FeedStalled();
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

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L191-231)
```text
    function _getBidAndAskPrice() internal returns (uint128, uint128) {
        // 1. Read via the unified price(feedId, pool) path, forwarding the pool (msg.sender).
        //    refTime is already in seconds.
        (uint256 mid, uint256 spread, , uint256 refTime) =
            IPricedOracle(address(offchainOracle)).price(offchainFeedId, msg.sender);

        // 2. Staleness check
        if (_isStale(refTime, block.timestamp, MAX_TIME_DELTA)) {
            return (0, type(uint128).max);
        }

        // 3. Basic validity — price must be positive, spread must not be stalled marker
        if (mid == 0 || spread >= ORACLE_BPS) {
            return (0, type(uint128).max);
        }

        // 4. Price guard check (moved from oracle)
        (uint128 guardMin, uint128 guardMax) = offchainOracle.priceGuard(offchainFeedId);
        guardMax = guardMax == 0 ? type(uint128).max : guardMax;
        if (mid < guardMin || mid > guardMax) {
            return (0, type(uint128).max);
        }

        // 5. Compute bid/ask from mid + confidence-adjusted spread
        //    confidenceParam multiplies oracle spread; 0 means no spread
        uint256 adjustedSpread = spread * confidenceParam;
        (uint256 bid, uint256 ask) = _getBidAskFrom(mid, adjustedSpread);

        // 6. Apply marginStep adjustment
        (uint256 bidOut, bool bidOk) = _applyBidAdjustments(bid);
        if (!bidOk || bidOut > type(uint128).max) return (0, type(uint128).max);

        (uint256 askOut, bool askOk) = _applyAskAdjustments(ask);
        if (!askOk || askOut > type(uint128).max) return (0, type(uint128).max);

        // 7. Hard invariant: bid must be strictly less than ask.
        //    Can be violated when marginStep < 0 and confidence is too small.
        if (bidOut >= askOut) return (0, type(uint128).max);

        return (uint128(bidOut), uint128(askOut));
    }
```

**File:** smart-contracts-poc/contracts/ProtectedPriceProvider.sol (L63-92)
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

        // Tokens live ONLY here (the oracles are token-free): explicit, mandatory pair.
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

**File:** smart-contracts-poc/contracts/ProtectedPriceProvider.sol (L181-223)
```text
    function _getBidAndAskPrice() internal returns (uint128, uint128) {
        (uint256 mid, uint256 spread, , uint256 refTime) =
            IPricedOracle(address(offchainOracle)).price(offchainFeedId, msg.sender);
        return _computeBidAsk(mid, spread, refTime);
    }

    /// @dev Downstream pricing: staleness, price guard, confidence spread, marginStep.
    function _computeBidAsk(uint256 price, uint256 spread, uint256 refTime)
        internal view returns (uint128, uint128)
    {
        // 1. Staleness check
        if (_isStale(refTime, block.timestamp, MAX_TIME_DELTA)) {
            return (0, type(uint128).max);
        }

        // 2. Basic validity — price must be positive, spread must not be stalled marker
        if (price == 0 || spread >= ORACLE_BPS) {
            return (0, type(uint128).max);
        }

        // 3. Price guard check
        (uint128 guardMin, uint128 guardMax) = offchainOracle.priceGuard(offchainFeedId);
        guardMax = guardMax == 0 ? type(uint128).max : guardMax;
        if (price < guardMin || price > guardMax) {
            return (0, type(uint128).max);
        }

        // 4. Compute bid/ask from mid + confidence-adjusted spread
        uint256 adjustedSpread = spread * confidenceParam;
        (uint256 bid, uint256 ask) = _getBidAskFrom(price, adjustedSpread);

        // 5. Apply marginStep adjustment
        (uint256 bidOut, bool bidOk) = _applyBidAdjustments(bid);
        if (!bidOk || bidOut > type(uint128).max) return (0, type(uint128).max);

        (uint256 askOut, bool askOk) = _applyAskAdjustments(ask);
        if (!askOk || askOut > type(uint128).max) return (0, type(uint128).max);

        // 6. Hard invariant: bid must be strictly less than ask.
        if (bidOut >= askOut) return (0, type(uint128).max);

        return (uint128(bidOut), uint128(askOut));
    }
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L341-346)
```text
        //    bid ≤ refBid < refAsk ≤ ask, so bid < ask holds by construction.
        uint256 bidOut = Math.min(refBid, cBid);
        uint256 askOut = Math.max(refAsk, cAsk);
        if (bidOut == 0 || bidOut >= askOut) {
            return (0, type(uint128).max);
        }
```
