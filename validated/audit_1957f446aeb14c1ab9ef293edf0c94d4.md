### Title
L2 Price Providers Lack Sequencer Uptime Check, Allowing Stale Prices to Reach Pool Swaps — (`smart-contracts-poc/contracts/PriceProviderL2.sol`, `smart-contracts-poc/contracts/ProtectedPriceProviderL2.sol`)

---

### Summary

`PriceProviderL2` and `ProtectedPriceProviderL2` are explicitly designed for L2 deployment (they carry `FUTURE_TOLERANCE` to handle sequencer clock skew) but contain no check against a Chainlink L2 sequencer uptime feed. When the sequencer goes offline, no new oracle reports can be pushed on-chain. When it restarts, the last stored price — which may be significantly stale relative to the true market — is immediately served to pool swaps as long as its age is within `MAX_TIME_DELTA`. The codebase already contains a `ChainlinkVerifierL2` contract (visible in the contract registry) with a `sequencerUptimeFeed` and `GRACE_PERIOD`, but neither L2 price provider calls it.

---

### Finding Description

Both L2 providers implement a pure time-delta staleness check:

```solidity
// PriceProviderL2.sol  (identical logic in ProtectedPriceProviderL2.sol)
function _isStale(
    uint256 refTime, uint256 nowTs,
    uint256 maxDelta, uint256 futureTol
) internal pure returns (bool) {
    if (refTime == 0) return true;
    if (refTime > nowTs) return (refTime - nowTs) > futureTol;
    return (nowTs - refTime) > maxDelta;          // ← only age check
}
``` [1](#0-0) 

`MAX_TIME_DELTA` is bounded only to `(0, 7 days]` at construction: [2](#0-1) 

`_getBidAndAskPrice` calls `_isStale` and, if it passes, immediately computes and returns bid/ask to the pool: [3](#0-2) 

`ProtectedPriceProviderL2` follows the same path through `_computeBidAsk`: [4](#0-3) 

Neither provider queries a sequencer uptime feed before serving the price. The contract registry confirms a `ChainlinkVerifierL2` with `GRACE_PERIOD` and `sequencerUptimeFeed` exists in the codebase but is not wired into either L2 provider: [5](#0-4) 

**Attack scenario:**

1. Sequencer goes offline on Arbitrum/Base/etc. No new oracle reports can be pushed to `ChainlinkOracle` or `PythOracle`.
2. The last stored price (e.g., ETH = $3 000) ages but remains within `MAX_TIME_DELTA` (e.g., configured to 1 hour; sequencer was down 45 min).
3. Sequencer restarts. The true market price is now $2 700 (moved during outage).
4. A trader immediately calls `MetricOmmPool.swap()`. The pool calls `getBidAndAskPrice()` on `PriceProviderL2`. `_isStale` passes (45 min < 1 hour). The pool quotes at $3 000.
5. The trader buys the base token at $3 000 while the market is at $2 700 — a ~10% gain at the LP's expense.

---

### Impact Explanation

Bad-price execution: a stale bid/ask quote (pre-outage price) reaches a live pool swap immediately after sequencer restart. LPs suffer direct loss of principal proportional to the price drift during the outage. This matches the allowed impact gate: *"Bad-price execution: stale, inverted, unbounded, or unclamped bid/ask quote reaches a pool swap."*

---

### Likelihood Explanation

L2 sequencer outages are documented historical events (Arbitrum, Optimism, Base have each experienced multi-minute to multi-hour outages). The vulnerability window is the interval `[sequencer restart, next oracle push]`. During high-volatility events — which often coincide with or cause sequencer stress — price drift is largest. Any user can trigger the swap; no special role is required.

---

### Recommendation

Before computing bid/ask, query the Chainlink L2 sequencer uptime feed (already present in the codebase as `ChainlinkVerifierL2`) and revert if the sequencer is down or within the grace period after restart:

```solidity
// pseudocode — integrate into _getBidAndAskPrice / _computeBidAsk
(, int256 answer, , uint256 updatedAt,) = sequencerUptimeFeed.latestRoundData();
if (answer != 0) revert SequencerDown();                        // 1 = down
if (block.timestamp - updatedAt < GRACE_PERIOD) revert GracePeriod(); // e.g. 1 hour
```

Wire the existing `ChainlinkVerifierL2.sequencerUptimeFeed` and `GRACE_PERIOD` into both `PriceProviderL2` and `ProtectedPriceProviderL2`. On chains without a sequencer (L1 Ethereum), pass `address(0)` and skip the check.

---

### Proof of Concept

```solidity
// Foundry test sketch
function test_stalePrice_afterSequencerRestart() public {
    // 1. Push a fresh oracle report at T=0
    uint256 T0 = block.timestamp;
    oracle.updateReport(_v3(feedId, 3000e18, 2999e18, 3001e18, uint32(T0)));

    // 2. Simulate sequencer outage: advance time 45 min, no new reports
    vm.warp(T0 + 45 minutes);

    // 3. Market moved to $2700 off-chain, but on-chain price is still $3000
    // 4. Sequencer restarts — trader calls getBidAndAskPrice immediately
    (uint128 bid, uint128 ask) = provider.getBidAndAskPrice();

    // 5. Price is stale but passes _isStale (45 min < MAX_TIME_DELTA = 1 hour)
    //    bid/ask still reflect $3000, not $2700 — LP is exploited
    assertGt(bid, 0, "stale price served without sequencer uptime check");
}
```

### Citations

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

**File:** smart-contracts-poc/contract-registry/versions/registry.json (L5685-5791)
```json
        "ChainlinkVerifierL2": {
          "abi": [
            {
              "type": "constructor",
              "inputs": [
                {
                  "name": "_sequencerUptimeFeed",
                  "type": "address",
                  "internalType": "address"
                }
              ],
              "stateMutability": "nonpayable"
            },
            {
              "type": "function",
              "name": "GRACE_PERIOD",
              "inputs": [],
              "outputs": [
                {
                  "name": "",
                  "type": "uint256",
                  "internalType": "uint256"
                }
              ],
              "stateMutability": "view"
            },
            {
              "type": "function",
              "name": "sequencerUptimeFeed",
              "inputs": [],
              "outputs": [
                {
                  "name": "",
                  "type": "address",
                  "internalType": "contract AggregatorV3Interface"
                }
              ],
              "stateMutability": "view"
            },
            {
              "type": "event",
              "name": "ClOracleRemoved",
              "inputs": [
                {
                  "name": "token",
                  "type": "address",
                  "indexed": true,
                  "internalType": "address"
                }
              ],
              "anonymous": false
            },
            {
              "type": "event",
              "name": "ClOracleSet",
              "inputs": [
                {
                  "name": "token",
                  "type": "address",
                  "indexed": true,
                  "internalType": "address"
                },
                {
                  "name": "oracle",
                  "type": "address",
                  "indexed": true,
                  "internalType": "address"
                },
                {
                  "name": "heartbeat",
                  "type": "uint32",
                  "indexed": false,
                  "internalType": "uint32"
                }
              ],
              "anonymous": false
            },
            {
              "type": "event",
              "name": "ClOracleStateSet",
              "inputs": [
                {
                  "name": "token",
                  "type": "address",
                  "indexed": true,
                  "internalType": "address"
                },
                {
                  "name": "oracle",
                  "type": "address",
                  "indexed": true,
                  "internalType": "address"
                }
              ],
              "anonymous": false
            },
            {
              "type": "error",
              "name": "ClOracleNotFound",
              "inputs": []
            }
          ],
          "methodIdentifiers": {
            "GRACE_PERIOD()": "c1a287e2",
            "sequencerUptimeFeed()": "a7264705"
          }
        }
```
