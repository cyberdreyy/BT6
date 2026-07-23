### Title
Missing L2 Sequencer Uptime Check in L2 Price Providers Violates Stated Invariant — (File: `smart-contracts-poc/contracts/PriceProviderL2.sol`, `smart-contracts-poc/contracts/ProtectedPriceProviderL2.sol`)

---

### Summary

The protocol's `README.md` explicitly declares as a non-negotiable invariant:

> *"No trade on bad oracle: swaps revert on stale price (maxTimeDelta/maxRefStaleness), excessive Chainlink deviation, or (L2) sequencer down."*

Neither `PriceProviderL2` nor `ProtectedPriceProviderL2` implement any on-chain check against a Chainlink L2 sequencer uptime feed. The only L2-awareness in these contracts is a `FUTURE_TOLERANCE` window that tolerates oracle `refTime` slightly ahead of `block.timestamp` to handle sequencer clock skew — this is the opposite of a sequencer-down guard. When the Arbitrum sequencer goes offline and then restarts, a window exists where the last pre-outage price is still within `MAX_TIME_DELTA` and passes all staleness checks, allowing swaps to execute against a price that no longer reflects the market.

---

### Finding Description

`PriceProviderL2._isStale()` performs only a timestamp-age check:

```solidity
// PriceProviderL2.sol lines 135-150
function _isStale(
    uint256 refTime,
    uint256 nowTs,
    uint256 maxDelta,
    uint256 futureTol
) internal pure returns (bool) {
    if (refTime == 0) return true;
    if (refTime > nowTs) {
        return (refTime - nowTs) > futureTol;   // clock-skew tolerance only
    }
    return (nowTs - refTime) > maxDelta;         // age check only
}
``` [1](#0-0) 

`ProtectedPriceProviderL2` contains an identical implementation: [2](#0-1) 

Neither contract queries a Chainlink sequencer uptime feed (e.g., `AggregatorV2V3Interface(sequencerUptimeFeed).latestRoundData()`) nor enforces a post-restart grace period. The `FUTURE_TOLERANCE` immutable is explicitly described as handling sequencer *clock skew*, not sequencer *downtime*: [3](#0-2) 

The attack window opens as follows:

1. Arbitrum sequencer goes offline. No L2 transactions execute; oracle pushers cannot call `updateReport()` on `ChainlinkOracle` or push to `CompressedOracleV1`.
2. The last stored `oracleData[feedId]` price and `timestampMs` are frozen at the pre-outage value.
3. The sequencer restarts. `block.timestamp` resumes advancing; the frozen `refTime` begins aging.
4. For the duration `[restart, restart + MAX_TIME_DELTA]`, the staleness check `(nowTs - refTime) > maxDelta` still passes.
5. During this window, before pushers have submitted a fresh report, any caller can execute a swap. `_getBidAndAskPrice()` returns the pre-outage bid/ask, which may be arbitrarily far from the current market price.

`MAX_TIME_DELTA` is bounded only by `[1, 7 days]` at construction: [4](#0-3) 

The deployment script example shows `maxTimeDelta: 10` (seconds), but this is a configuration choice with no on-chain floor enforcement beyond `> 0`. A deployer using a larger value (minutes to hours) dramatically widens the exploitation window.

The stated invariant in `README.md` is unambiguous: [5](#0-4) 

---

### Impact Explanation

During the post-restart window, swaps execute against a stale bid/ask that reflects pre-outage market conditions. If the market moved significantly during the outage (a common scenario — sequencer outages often coincide with high-volatility events), traders can:

- **Buy at a stale low ask** (market price has risen): extract value from LPs who cannot withdraw during a swap.
- **Sell at a stale high bid** (market price has fallen): same directional drain.

This is a direct bad-price execution impact: the pool receives the correct token amount per the stale quote, but the quote itself is wrong, so LPs bear the loss. This matches the allowed impact gate: *"Bad-price execution: stale, inverted, unbounded, or unclamped bid/ask quote reaches a pool swap."*

---

### Likelihood Explanation

Arbitrum has experienced multiple sequencer outages (December 2021, June 2023, others). The trigger is an external event (sequencer downtime) but is not attacker-controlled — any user can exploit the window opportunistically the moment the sequencer restarts. The window size is proportional to `MAX_TIME_DELTA`, which is a deployment-time parameter with no on-chain minimum above 1 second. The protocol explicitly targets L2 deployment and ships two L2-specific provider contracts, making this path reachable in production.

---

### Recommendation

Add a sequencer uptime check to both `PriceProviderL2` and `ProtectedPriceProviderL2`, following the Chainlink-recommended pattern:

```solidity
// Store as an immutable set at construction
AggregatorV2V3Interface public immutable sequencerUptimeFeed;
uint256 public immutable GRACE_PERIOD; // e.g. 3600 seconds

function _checkSequencer() internal view {
    (, int256 answer, uint256 startedAt,,) = sequencerUptimeFeed.latestRoundData();
    // answer == 0 means sequencer is up; 1 means down
    require(answer == 0, SequencerDown());
    // Enforce grace period after restart
    require(block.timestamp - startedAt >= GRACE_PERIOD, GracePeriodNotOver());
}
```

Call `_checkSequencer()` at the top of `_getBidAndAskPrice()` in both contracts, before the staleness check. This directly implements the invariant the protocol already claims to enforce.

---

### Proof of Concept

1. Deploy `PriceProviderL2` on Arbitrum with `MAX_TIME_DELTA = 3600` (1 hour).
2. At `t=0`: oracle holds price `P0 = 1000 USDC/ETH`. Sequencer goes offline.
3. At `t=1800`: real market price moves to `P1 = 1200 USDC/ETH`. Sequencer restarts.
4. At `t=1801`: attacker calls `pool.swap()`. `_isStale(refTime=t0, nowTs=t0+1801, maxDelta=3600)` → `1801 < 3600` → **not stale**. Swap executes at `P0`.
5. Attacker buys ETH at the stale ask derived from `P0 = 1000`, immediately worth `P1 = 1200` on any other venue. LP position is drained by the 20% price gap.

### Citations

**File:** smart-contracts-poc/contracts/PriceProviderL2.sol (L36-38)
```text
    /// @dev L2 sequencer timestamp can lag behind oracle publication time.
    ///      Allows refTime up to FUTURE_TOLERANCE seconds ahead of block.timestamp.
    uint256 public immutable FUTURE_TOLERANCE;
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

**File:** smart-contracts-poc/contracts/ProtectedPriceProviderL2.sol (L138-153)
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

**File:** README.md (L49-49)
```markdown
No trade on bad oracle: swaps revert on stale price (maxTimeDelta/maxRefStaleness), excessive Chainlink deviation, or (L2) sequencer down.
```
