### Title
`ProtectedPriceProviderL2` Missing Sequencer Uptime Check Allows Stale Prices After L2 Sequencer Recovery - (File: `smart-contracts-poc/contracts/ProtectedPriceProviderL2.sol`)

### Summary

`ProtectedPriceProviderL2` is the canonical L2 price provider deployed on Arbitrum, Base, Optimism, and other L2s. It performs a time-based staleness check (`_isStale`) against `MAX_TIME_DELTA`, but contains **no Chainlink Sequencer Uptime Feed check**. When an L2 sequencer goes down and recovers, the last pushed oracle price (which may be significantly stale) passes the staleness check if the downtime was shorter than `MAX_TIME_DELTA`. A public trader can immediately execute swaps at the pre-downtime price before any pusher can refresh the on-chain oracle data.

### Finding Description

`ProtectedPriceProviderL2._computeBidAsk` performs the following staleness check:

```solidity
// 1. Staleness check
if (_isStale(refTime, block.timestamp, MAX_TIME_DELTA, FUTURE_TOLERANCE)) {
    return (0, type(uint128).max);
}
``` [1](#0-0) 

The `_isStale` function only checks whether the oracle's `refTime` is older than `MAX_TIME_DELTA` seconds:

```solidity
function _isStale(
    uint256 refTime, uint256 nowTs, uint256 maxDelta, uint256 futureTol
) internal pure returns (bool) {
    if (refTime == 0) return true;
    if (refTime > nowTs) {
        return (refTime - nowTs) > futureTol;
    }
    return (nowTs - refTime) > maxDelta;
}
``` [2](#0-1) 

The constructor accepts `_maxTimeDelta` up to 7 days and `_futureTolerance` up to 1 hour, but takes **no `_sequencerUptimeFeed` parameter**: [3](#0-2) 

The contract has no `sequencerUptimeFeed` storage variable, no `GRACE_PERIOD` constant, and no call to any sequencer uptime aggregator anywhere in its body.

The Metric OMM oracle is push-based: off-chain pushers write prices into `CompressedOracle`, `PythOracle`, or `ChainlinkOracle` storage. When the L2 sequencer is down, pushers cannot land transactions. When the sequencer recovers, the on-chain storage still holds the last pre-downtime price. The `_isStale` check passes as long as `block.timestamp - refTime ≤ MAX_TIME_DELTA`. If the downtime was shorter than `MAX_TIME_DELTA`, the stale price is accepted as live.

The `FUTURE_TOLERANCE` field only addresses sequencer clock skew (oracle `refTime` slightly ahead of `block.timestamp`), not sequencer downtime. [4](#0-3) 

The same gap exists in `PriceProviderL2.sol`: [5](#0-4) 

The `PriceProviderFactoryL2` deploys these providers on Arbitrum, Base, Optimism, Avalanche, Polygon, and others: [6](#0-5) 

### Impact Explanation

After sequencer recovery, a public trader can call `MetricOmmPool.swap` → `ProtectedPriceProviderL2.getBidAndAskPrice` → `_computeBidAsk`. The provider returns the pre-downtime bid/ask (e.g., a price from 45 minutes ago when `MAX_TIME_DELTA = 1 hour`). The pool executes the swap at this stale price. If the market moved during the outage, the trader receives more output tokens than the current oracle price permits, draining LP assets. This is a direct bad-price execution impact: LP principal is lost to the arbitrageur who exploits the stale quote.

### Likelihood Explanation

L2 sequencer outages are documented historical events (Arbitrum experienced a ~7-hour outage in 2022; Base and Optimism have had shorter interruptions). The `MAX_TIME_DELTA` constructor bound is 7 days, so a deployer can legitimately set it to hours. The attack requires only a public `swap` call immediately after sequencer recovery — no privileged access, no special setup. The window is the time between sequencer recovery and the first successful price push, which can be tens of seconds to minutes depending on pusher bot latency.

### Recommendation

Add a Chainlink Sequencer Uptime Feed check inside `_computeBidAsk` (or in `getBidAndAskPrice`) that:
1. Reads the sequencer uptime aggregator (`AggregatorV3Interface(sequencerUptimeFeed).latestRoundData()`).
2. Reverts (returns the stall sentinel) if the sequencer is currently down (`answer != 0`).
3. Reverts if the sequencer has been back up for less than `MAX_TIME_DELTA` seconds (`block.timestamp - startedAt < MAX_TIME_DELTA`), ensuring the stale window is fully outside the accepted price age.

The `sequencerUptimeFeed` address should be an immutable set at construction, with a `address(0)` guard to allow L1 deployments to skip the check.

### Proof of Concept

```
Setup:
  - Deploy ProtectedPriceProviderL2 on Arbitrum with MAX_TIME_DELTA = 3600 (1 hour)
  - Push a price P0 at time T0 (refTime = T0)

Attack:
  1. At T0 + 1s, the L2 sequencer goes offline.
     No new prices can be pushed; on-chain storage holds P0.

  2. At T0 + 3500s (< MAX_TIME_DELTA), the sequencer recovers.
     block.timestamp = T0 + 3500.
     _isStale check: (T0 + 3500) - T0 = 3500 < 3600 → NOT stale → price P0 accepted.

  3. Attacker immediately calls pool.swap() before any pusher bot lands a new price.
     Provider returns bid/ask derived from P0 (3500-second-old price).
     If the true market price moved 2% during the outage, the attacker extracts
     ~2% of the swap notional from LP reserves.

  4. Pusher bot lands new price P1 at T0 + 3510s.
     Damage already done; LP cannot recover the loss.
```

### Citations

**File:** smart-contracts-poc/contracts/ProtectedPriceProviderL2.sol (L40-42)
```text
    /// @dev L2 sequencer timestamp can lag behind oracle publication time.
    ///      Allows refTime up to FUTURE_TOLERANCE seconds ahead of block.timestamp.
    uint256 public immutable FUTURE_TOLERANCE;
```

**File:** smart-contracts-poc/contracts/ProtectedPriceProviderL2.sol (L68-100)
```text
    constructor(
        address _factory,
        address _oracle,
        bytes32 _offchainFeedId,
        int256  _marginStep,
        uint256 _maxTimeDelta,
        uint256 _futureTolerance,
        address _baseToken,
        address _quoteToken
    ) {
        require(_factory != address(0));
        factory = _factory;

        offchainOracle = IOffchainOracle(_oracle);
        offchainFeedId = _offchainFeedId;

        // Tokens live ONLY here (the oracles are token-free): explicit, mandatory pair.
        require(_baseToken != address(0) && _quoteToken != address(0) && _baseToken != _quoteToken);
        baseToken  = _baseToken;
        quoteToken = _quoteToken;

        if (_marginStep <= -BPS_BASE || _marginStep >= BPS_BASE) {
            revert MarginStepOutOfBounds();
        }
        marginStep       = _marginStep;
        stepBidFactor = uint256(BPS_BASE - _marginStep);
        stepAskFactor = uint256(BPS_BASE + _marginStep);

        if (_maxTimeDelta == 0 || _maxTimeDelta > 7 days) revert MaxTimeDeltaOutOfBounds();
        if (_futureTolerance > 1 hours) revert FutureToleranceOutOfBounds();
        MAX_TIME_DELTA   = _maxTimeDelta;
        FUTURE_TOLERANCE = _futureTolerance;
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

**File:** smart-contracts-poc/contracts/ProtectedPriceProviderL2.sol (L206-209)
```text
        // 1. Staleness check
        if (_isStale(refTime, block.timestamp, MAX_TIME_DELTA, FUTURE_TOLERANCE)) {
            return (0, type(uint128).max);
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

**File:** smart-contracts-poc/contract-registry/versions/registry.json (L5800-5836)
```json
        "PriceProviderFactoryL2": {
          "arbitrum": {
            "address": "0x50f11246D87313eC1AB8f8eB01450B297ea9e245",
            "version": "0.1.0"
          },
          "avalanche": {
            "address": "0x50f11246D87313eC1AB8f8eB01450B297ea9e245",
            "version": "0.1.0"
          },
          "base": {
            "address": "0x50f11246D87313eC1AB8f8eB01450B297ea9e245",
            "version": "0.1.0"
          },
          "berachain": {
            "address": "0x50f11246D87313eC1AB8f8eB01450B297ea9e245",
            "version": "0.1.0"
          },
          "bsc": {
            "address": "0x50f11246D87313eC1AB8f8eB01450B297ea9e245",
            "version": "0.1.0"
          },
          "megaeth": {
            "address": "0x50f11246D87313eC1AB8f8eB01450B297ea9e245",
            "version": "0.1.0"
          },
          "monad": {
            "address": "0x50f11246D87313eC1AB8f8eB01450B297ea9e245",
            "version": "0.1.0"
          },
          "optimism": {
            "address": "0x50f11246D87313eC1AB8f8eB01450B297ea9e245",
            "version": "0.1.0"
          },
          "polygon": {
            "address": "0x50f11246D87313eC1AB8f8eB01450B297ea9e245",
            "version": "0.1.0"
          }
```
