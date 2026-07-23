### Title
Missing Sequencer Uptime Check in L2 Price Providers Allows Stale Prices to Reach Pool Swaps After Sequencer Recovery — (`smart-contracts-poc/contracts/PriceProviderL2.sol`, `smart-contracts-poc/contracts/ProtectedPriceProviderL2.sol`)

---

### Summary

`PriceProviderL2` and `ProtectedPriceProviderL2` are explicitly L2-targeted price providers (they carry `FUTURE_TOLERANCE` to handle sequencer clock skew) but contain no sequencer uptime feed check. When the L2 sequencer is down, no new oracle data can be pushed to the providers oracle. When the sequencer recovers, the stored pre-downtime data is consumed by the provider's `_isStale` window without any grace-period enforcement, allowing a stale bid/ask quote to reach pool swaps.

---

### Finding Description

Both L2 providers read oracle data through the attributed push-based path:

```solidity
// PriceProviderL2._getBidAndAskPrice()
(uint256 mid, uint256 spread, , uint256 refTime) =
    IPricedOracle(address(offchainOracle)).price(offchainFeedId, msg.sender);

if (_isStale(refTime, block.timestamp, MAX_TIME_DELTA, FUTURE_TOLERANCE)) {
    return (0, type(uint128).max);
}
```

The only freshness gate is `_isStale`, which compares the oracle data's stored `refTime` against `block.timestamp`:

```solidity
function _isStale(
    uint256 refTime, uint256 nowTs,
    uint256 maxDelta, uint256 futureTol
) internal pure returns (bool) {
    if (refTime == 0) return true;
    if (refTime > nowTs) return (refTime - nowTs) > futureTol;
    return (nowTs - refTime) > maxDelta;
}
```

`MAX_TIME_DELTA` is an immutable set at construction, bounded only by `(0, 7 days]`. The providers oracle is push-based: off-chain actors submit signed reports (Pyth Lazer / Chainlink Data Streams) that are stored on-chain. When the L2 sequencer is down, no transactions can land, so no new oracle data can be pushed. When the sequencer recovers, the last pre-downtime data remains in storage with its original `refTime`. If the downtime duration is shorter than `MAX_TIME_DELTA`, `_isStale` returns `false` and the stale price is forwarded to the pool as a valid bid/ask quote.

Neither `PriceProviderL2` nor `ProtectedPriceProviderL2` consult a sequencer uptime feed (e.g., Chainlink's `AggregatorV3Interface` sequencer feed) or enforce any grace period after sequencer recovery before allowing price reads. The registry ABI confirms a `ChainlinkVerifierL2` contract exists with `sequencerUptimeFeed` and `GRACE_PERIOD`, but this check lives only in the push/ingestion path, not in the provider read path consumed by pools.

---

### Impact Explanation

A stale bid/ask quote reaching a pool swap is an explicit allowed impact. Concretely:

- If the market price moved significantly during sequencer downtime (e.g., a 5 % crash), the pool continues quoting the pre-crash price immediately after recovery.
- Arbitrageurs can drain the pool by buying the underpriced asset at the stale ask, causing direct LP principal loss.
- The pool's swap conservation invariant is broken: the trader receives more value than the current oracle/bin curve permits.

---

### Likelihood Explanation

Base and HyperEVM are the protocol's target L2 chains. Both have experienced sequencer downtime historically. The window of vulnerability is the entire period between sequencer recovery and the first successful fresh oracle push — which itself requires a transaction to land, meaning the very first block after recovery is always vulnerable. With `MAX_TIME_DELTA` values up to 7 days, even multi-hour outages leave the stale price accepted.

---

### Recommendation

Add a sequencer uptime check inside `getBidAndAskPrice()` (or at the top of `_getBidAndAskPrice()`) in both `PriceProviderL2` and `ProtectedPriceProviderL2`:

```solidity
// Store as immutables set at construction:
AggregatorV3Interface public immutable sequencerUptimeFeed;
uint256 public immutable GRACE_PERIOD; // e.g. 3600 seconds

function _checkSequencer() internal view {
    (, int256 answer, uint256 startedAt,,) = sequencerUptimeFeed.latestRoundData();
    // answer == 0 means sequencer is up
    if (answer != 0) revert SequencerDown();
    if (block.timestamp - startedAt < GRACE_PERIOD) revert GracePeriodNotOver();
}
```

Call `_checkSequencer()` before the oracle read in `_getBidAndAskPrice()`. The grace period should be at least as large as the typical time needed for a fresh oracle push to land after sequencer recovery, ensuring the stale pre-downtime data is aged out by `_isStale` before any swap can proceed.

---

### Proof of Concept

1. Deploy `PriceProviderL2` on Base with `MAX_TIME_DELTA = 7200` (2 hours), `FUTURE_TOLERANCE = 60`.
2. At `T = 0`, off-chain pusher submits a report: `refTime = T`, `mid = 3000e8` (ETH price).
3. At `T = 100`, the Base sequencer goes down. ETH price drops to `2700e8` off-chain.
4. At `T = 7100` (within `MAX_TIME_DELTA`), the sequencer recovers. No new oracle push has landed yet.
5. An arbitrageur immediately calls `swap()` on the pool. The pool calls `getBidAndAskPrice()`.
6. `_isStale(T, T+7100, 7200, 60)` → `7100 - 0 = 7100 ≤ 7200` → **not stale** → returns `ask` derived from `mid = 3000e8`.
7. Arbitrageur buys ETH at the stale `3000e8` ask while the real price is `2700e8`, extracting ~10% from LP reserves. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** smart-contracts-poc/contracts/PriceProviderL2.sol (L34-42)
```text
    uint256 public immutable MAX_TIME_DELTA;

    /// @dev L2 sequencer timestamp can lag behind oracle publication time.
    ///      Allows refTime up to FUTURE_TOLERANCE seconds ahead of block.timestamp.
    uint256 public immutable FUTURE_TOLERANCE;

    address public immutable baseToken;
    address public immutable quoteToken;

```

**File:** smart-contracts-poc/contracts/PriceProviderL2.sol (L92-96)
```text
        if (_maxTimeDelta == 0 || _maxTimeDelta > 7 days) revert MaxTimeDeltaOutOfBounds();
        if (_futureTolerance > 1 hours) revert FutureToleranceOutOfBounds();
        MAX_TIME_DELTA   = _maxTimeDelta;
        FUTURE_TOLERANCE = _futureTolerance;
    }
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

**File:** smart-contracts-poc/contracts/PriceProviderL2.sol (L208-217)
```text
    function _getBidAndAskPrice() internal returns (uint128, uint128) {
        // 1. Read via the unified price(feedId, pool) path, forwarding the pool (msg.sender).
        //    refTime is already in seconds.
        (uint256 mid, uint256 spread, , uint256 refTime) =
            IPricedOracle(address(offchainOracle)).price(offchainFeedId, msg.sender);

        // 2. Staleness check
        if (_isStale(refTime, block.timestamp, MAX_TIME_DELTA, FUTURE_TOLERANCE)) {
            return (0, type(uint128).max);
        }
```

**File:** smart-contracts-poc/contracts/ProtectedPriceProviderL2.sol (L196-209)
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
        if (_isStale(refTime, block.timestamp, MAX_TIME_DELTA, FUTURE_TOLERANCE)) {
            return (0, type(uint128).max);
        }
```
