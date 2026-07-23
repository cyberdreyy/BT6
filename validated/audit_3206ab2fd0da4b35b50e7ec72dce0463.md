### Title
Missing Chainlink Sequencer Uptime Check in L2 Price Providers Allows Swaps on Stale Oracle Prices - (`smart-contracts-poc/contracts/PriceProviderL2.sol`, `smart-contracts-poc/contracts/ProtectedPriceProviderL2.sol`)

### Summary

`PriceProviderL2` and `ProtectedPriceProviderL2` are deployed on L2 networks and read oracle prices to settle pool swaps, but neither contract checks the Chainlink sequencer uptime feed before consuming oracle data. The protocol's own stated invariant requires that "swaps revert on stale price (maxTimeDelta/maxRefStaleness), excessive Chainlink deviation, or (L2) sequencer down," yet no sequencer uptime guard exists in either L2 provider. The registry ABI for `PriceProviderL2` includes a `_sequencerUptimeFeed` constructor parameter that is entirely absent from the deployed source code, confirming the check was intended but never implemented.

### Finding Description

Both L2 price providers implement a `FUTURE_TOLERANCE` immutable described as handling "L2 sequencer timestamp can lag behind oracle publication time," but this is only a clock-skew tolerance — it allows `refTime` to be slightly ahead of `block.timestamp`. It is not a sequencer uptime check and does not protect against the sequencer going offline.

The attack path is:

1. The L2 sequencer goes offline at time `T`. The last oracle update stored on-chain has `refTime = T - Δ` where `Δ < MAX_TIME_DELTA`.
2. While the sequencer is down, no new oracle reports can be pushed. The on-chain price is frozen at the pre-downtime value.
3. The sequencer comes back online at time `T + D` where `D < MAX_TIME_DELTA - Δ`. The stored `refTime` is now `T + D - Δ` seconds old, still within `MAX_TIME_DELTA`.
4. The staleness check in `_computeBidAsk` / `_getBidAndAskPrice` passes because `(nowTs - refTime) = D + Δ ≤ MAX_TIME_DELTA`.
5. A trader immediately calls `swap()` on the pool. The pool calls `getBidAndAskPrice()` on the L2 provider, which returns the pre-downtime bid/ask without reverting.
6. The trader executes at a price that may be significantly different from the true market price, extracting value from LPs.

The Chainlink-recommended fix is to query a sequencer uptime feed and enforce a grace period after the sequencer restarts, during which all price reads revert. The registry ABI for `PriceProviderL2` explicitly includes `_sequencerUptimeFeed` as a constructor argument and `sequencerUptimeFeed()` as a view, and `ChainlinkVerifierL2` in the registry exposes `GRACE_PERIOD` and `sequencerUptimeFeed()` — confirming the protocol designed for this check but the source code omits it.

**Root cause in `PriceProviderL2._getBidAndAskPrice`:** [1](#0-0) 

The only guard is `_isStale`, which checks `(nowTs - refTime) > maxDelta`: [2](#0-1) 

No sequencer uptime feed is stored or queried anywhere in the contract: [3](#0-2) 

**Same omission in `ProtectedPriceProviderL2._computeBidAsk`:** [4](#0-3) 

**Registry ABI confirms `_sequencerUptimeFeed` was intended:** [5](#0-4) 

**Stated invariant that is violated:** [6](#0-5) 

### Impact Explanation

A trader can execute a swap against a stale pre-downtime price immediately after the sequencer restarts, as long as the downtime was shorter than `MAX_TIME_DELTA`. The pool settles the trade at the frozen bid/ask, which may be arbitrarily far from the true market price. LPs bear the loss: the pool receives the correct input token amount but pays out at the wrong price, violating swap conservation. This is a direct loss of LP principal, qualifying as **Medium** (requires the external condition of a sequencer outage shorter than `MAX_TIME_DELTA`, but L2 sequencer outages are a documented, recurring real-world event on Arbitrum, Base, Optimism, etc.).

### Likelihood Explanation

L2 sequencer outages are not hypothetical — Arbitrum, Optimism, and Base have each experienced documented downtime events. The attacker requires no privileged access: they only need to monitor the sequencer status and submit a swap transaction in the first block after the sequencer restarts, before any oracle updater can push a fresh price. The window is bounded by `MAX_TIME_DELTA` minus the actual downtime, which can be minutes to hours depending on configuration.

### Recommendation

Add a Chainlink sequencer uptime feed check to both `PriceProviderL2` and `ProtectedPriceProviderL2`, following the pattern already present in the registry's `ChainlinkVerifierL2`:

```solidity
// In constructor:
AggregatorV3Interface public immutable sequencerUptimeFeed;
uint256 public constant GRACE_PERIOD = 3600; // 1 hour

// In _getBidAndAskPrice() / _computeBidAsk(), before any price use:
(, int256 answer, uint256 startedAt,,) = sequencerUptimeFeed.latestRoundData();
// answer == 0 means sequencer is up; 1 means down
if (answer != 0) return (0, type(uint128).max); // sequencer down → fail closed
if (block.timestamp - startedAt < GRACE_PERIOD) return (0, type(uint128).max); // grace period
```

This ensures that when the sequencer is down or has just restarted, `getBidAndAskPrice()` returns the `(0, max)` sentinel, causing the pool's swap to revert with `FeedStalled`.

### Proof of Concept

```
Setup:
  - Deploy PriceProviderL2 on Arbitrum with MAX_TIME_DELTA = 3600 (1 hour)
  - Oracle last updated at T=0 with price P0

Attack:
  1. At T=0: oracle stores price P0 (e.g., ETH = $3000)
  2. At T=100: L2 sequencer goes offline
  3. Real market price moves to P1 = $2500 during downtime
  4. At T=3500: sequencer comes back online (downtime = 3400s < MAX_TIME_DELTA)
  5. Attacker immediately calls pool.swap() (zero-block delay)
  6. Pool calls provider.getBidAndAskPrice()
  7. _isStale check: (3500 - 0) = 3500 < 3600 → NOT stale → passes
  8. Provider returns bid/ask based on P0 = $3000
  9. Attacker buys ETH at $3000 when true price is $2500
  10. LPs lose ~$500 per ETH traded (16.7% loss)

Expected (with fix): step 7 queries sequencerUptimeFeed, finds sequencer
just restarted (startedAt = T=3500), grace period not elapsed → reverts FeedStalled.
```

### Citations

**File:** smart-contracts-poc/contracts/PriceProviderL2.sol (L29-51)
```text
    // ── Immutables ──────────────────────────────────────────────────────
    IOffchainOracle public immutable offchainOracle;
    bytes32         public immutable offchainFeedId;
    address         public immutable factory;

    uint256 public immutable MAX_TIME_DELTA;

    /// @dev L2 sequencer timestamp can lag behind oracle publication time.
    ///      Allows refTime up to FUTURE_TOLERANCE seconds ahead of block.timestamp.
    uint256 public immutable FUTURE_TOLERANCE;

    address public immutable baseToken;
    address public immutable quoteToken;

    // ── Storage ─────────────────────────────────────────────────────────
    uint256 public confidenceParam;
    uint256 public lastConfidenceUpdate;

    /// @dev marginStep and the derived step factors — set once at construction (immutable).
    int256  public immutable marginStep;
    uint256 internal immutable stepBidFactor; // BPS_BASE_U - marginStep
    uint256 internal immutable stepAskFactor; // BPS_BASE_U + marginStep

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

**File:** smart-contracts-poc/contracts/ProtectedPriceProviderL2.sol (L202-210)
```text
    /// @dev Downstream pricing: staleness, price guard, confidence spread, marginStep.
    function _computeBidAsk(uint256 price, uint256 spread, uint256 refTime)
        internal view returns (uint128, uint128)
    {
        // 1. Staleness check
        if (_isStale(refTime, block.timestamp, MAX_TIME_DELTA, FUTURE_TOLERANCE)) {
            return (0, type(uint128).max);
        }

```

**File:** smart-contracts-poc/contract-registry/versions/registry.json (L597-636)
```json
        "PriceProviderL2": {
          "abi": [
            {
              "type": "constructor",
              "inputs": [
                {
                  "name": "_factory",
                  "type": "address",
                  "internalType": "address"
                },
                {
                  "name": "_oracle",
                  "type": "address",
                  "internalType": "address"
                },
                {
                  "name": "_offchainFeedId",
                  "type": "bytes32",
                  "internalType": "bytes32"
                },
                {
                  "name": "_cexStep",
                  "type": "int256",
                  "internalType": "int256"
                },
                {
                  "name": "_maxTimeDelta",
                  "type": "uint256",
                  "internalType": "uint256"
                },
                {
                  "name": "_futureTolerance",
                  "type": "uint256",
                  "internalType": "uint256"
                },
                {
                  "name": "_sequencerUptimeFeed",
                  "type": "address",
                  "internalType": "address"
                },
```

**File:** README.md (L49-49)
```markdown
No trade on bad oracle: swaps revert on stale price (maxTimeDelta/maxRefStaleness), excessive Chainlink deviation, or (L2) sequencer down.
```
