### Title
Single `MAX_REF_STALENESS` applied uniformly to both legs of synthetic ratio in `AnchoredPriceProvider` allows stale quote-leg price to reach pool swaps — (File: `smart-contracts-poc/contracts/AnchoredPriceProvider.sol`)

---

### Summary

`AnchoredPriceProvider._readLeg()` applies the single immutable `MAX_REF_STALENESS` to **both** `baseFeedId` and `quoteFeedId`. When the two feeds have different real-world heartbeats, the staleness bound must be set wide enough to accommodate the slower feed, which then silently permits the faster feed to be stale by up to that same wide window. A stale price from the faster leg is used in the synthetic ratio calculation, producing a corrupted bid/ask that reaches the pool swap.

---

### Finding Description

`AnchoredPriceProvider` supports a synthetic ratio mode: when `quoteFeedId != 0`, it reads both legs and computes `mid = price(baseFeedId) / price(quoteFeedId)`.

Both legs are validated through the same internal helper `_readLeg()`:

```solidity
// AnchoredPriceProvider.sol L277-L295
function _readLeg(bytes32 feedId)
    internal returns (uint256 mid, uint256 spreadBps, uint256 refTime, bool ok)
{
    (mid, spreadBps, , refTime) = IPricedOracle(address(offchainOracle)).price(feedId, msg.sender);

    // Stale reference → not ok.
    if (_isStale(refTime, block.timestamp, MAX_REF_STALENESS)) return (mid, spreadBps, refTime, false);
    ...
}
``` [1](#0-0) 

The same `MAX_REF_STALENESS` is used for both the base leg and the quote leg. There is no per-feed staleness parameter.

`MAX_REF_STALENESS` is set at construction and is immutable:

```solidity
// AnchoredPriceProvider.sol L150-L151
if (_maxRefStaleness > 7 days) revert MaxRefStalenessOutOfBounds();
MAX_REF_STALENESS = _maxRefStaleness;
``` [2](#0-1) 

The factory validates `maxRefStaleness` **only against the envelope keyed on `baseFeedId`'s class** — there is no envelope check for `quoteFeedId`:

```solidity
// AnchoredProviderFactory.sol L171-L180
bytes32 classId = feedClass[baseFeedId];
if (classId == bytes32(0)) classId = DEFAULT_CLASS;

Envelope storage env = envelopes[classId];
if (!env.exists) revert EnvelopeNotFound(classId);
if (
    minMargin < env.minMarginMin || minMargin > env.minMarginMax
    || maxRefStaleness < env.stalenessMin || maxRefStaleness > env.stalenessMax
    || maxSpreadBps < env.maxSpreadMin || maxSpreadBps > env.maxSpreadMax
) revert ParamsOutOfEnvelope();
``` [3](#0-2) 

The factory's own NatSpec acknowledges this asymmetry: *"The envelope is keyed on `baseFeedId` (the provider's class); the ref feed only contributes its uncertainty."* [4](#0-3) 

**Concrete attack path:**

1. Admin sets the envelope for the BTC/USD feed class with `stalenessMax = 24 hours` (legitimate: BTC/USD Chainlink Data Streams has a 24-hour heartbeat on some networks).
2. A creator (permissionless call to `createAnchoredProvider`) deploys a synthetic provider with:
   - `baseFeedId = BTC/USD` (24-hour heartbeat)
   - `quoteFeedId = ETH/USD` (1-hour heartbeat)
   - `maxRefStaleness = 24 hours` (within the BTC/USD class envelope)
3. The ETH/USD feed is not updated for 23 hours (off-chain infrastructure lag, or deliberate griefing by the pusher).
4. A swap is executed. `_readLeg(ETH/USD)` checks `(23 hours) > MAX_REF_STALENESS (24 hours)` → **false** → staleness check passes.
5. The synthetic ratio `BTC/ETH = price(BTC/USD) / price(ETH/USD)` is computed using a 23-hour-old ETH/USD price.
6. The corrupted bid/ask is returned to the pool and used to settle the swap.

The `_getBidAndAskPrice` call chain confirms both legs flow through the same `_readLeg` with no per-leg staleness override:

```solidity
// AnchoredPriceProvider.sol L258-L271
function _getBidAndAskPrice() internal returns (uint128, uint128) {
    (uint256 mid, uint256 spreadBps, , bool ok) = _readLeg(baseFeedId);
    if (!ok) return (0, type(uint128).max);

    bytes32 _quote = quoteFeedId;
    if (_quote != bytes32(0)) {
        (uint256 mid2, uint256 spreadBps2, , bool ok2) = _readLeg(_quote);
        ...
        mid = Math.mulDiv(mid, ORACLE_DECIMALS, mid2);
        spreadBps += spreadBps2;
    }
    return _computeBidAsk(mid, spreadBps);
}
``` [5](#0-4) 

---

### Impact Explanation

A stale quote-leg price corrupts the synthetic ratio mid. For example, if ETH/USD is 23 hours stale and ETH has moved from $2,000 to $3,000, the synthetic BTC/ETH ratio is computed as `50,000 / 2,000 = 25` instead of the correct `50,000 / 3,000 ≈ 16.67`. A trader can swap BTC for ETH at the inflated synthetic price, extracting the difference from LP positions. This is a direct bad-price execution impact: the pool receives less input than the oracle/bin curve permits, or the trader receives more output than warranted — LP principal loss above Sherlock thresholds for a sufficiently large pool.

---

### Likelihood Explanation

- `createAnchoredProvider` is permissionless; any address can deploy a synthetic provider.
- The factory's `DEFAULT_CLASS` envelope applies to all feeds without an explicit class assignment, meaning a wide `stalenessMax` in the default envelope immediately enables this for any synthetic pair.
- Feeds with genuinely different heartbeats (e.g., RWA/stablecoin base vs. crypto quote) are a natural and documented use case for the synthetic ratio mode.
- The off-chain pusher for the faster feed may legitimately lag (network congestion, infrastructure failure) without triggering any on-chain guard.

---

### Recommendation

Add a separate `maxQuoteStaleness` immutable to `AnchoredPriceProvider` and apply it in `_readLeg` when reading the quote leg:

```solidity
uint256 public immutable MAX_QUOTE_STALENESS; // new immutable

function _getBidAndAskPrice() internal returns (uint128, uint128) {
    (uint256 mid, uint256 spreadBps, , bool ok) = _readLeg(baseFeedId, MAX_REF_STALENESS);
    ...
    if (_quote != bytes32(0)) {
        (uint256 mid2, uint256 spreadBps2, , bool ok2) = _readLeg(_quote, MAX_QUOTE_STALENESS);
        ...
    }
}
```

The factory should validate `maxQuoteStaleness` against the envelope for `quoteFeedId`'s class (or a separate quote-class envelope), not the base feed's class.

---

### Proof of Concept

```solidity
// Scenario: BTC/USD (24h heartbeat) / ETH/USD (1h heartbeat) synthetic provider
// MAX_REF_STALENESS = 24 hours (within BTC/USD class envelope)

// T=0: Both feeds fresh. BTC/USD = 50_000e8, ETH/USD = 2_000e8
// Synthetic BTC/ETH mid = 25e8 (correct)

// T=23h: ETH/USD not updated (off-chain lag). BTC/USD updated to 50_000e8.
// ETH/USD still reads 2_000e8 with refTime = T=0.

// _readLeg(ETH/USD):
//   refTime = T=0, nowTs = T=23h
//   (23h) > MAX_REF_STALENESS (24h) → false → ok = true  ← PASSES

// Synthetic mid = 50_000e8 / 2_000e8 = 25e8
// Actual mid    = 50_000e8 / 3_000e8 ≈ 16.67e8  (ETH moved to $3,000)

// Trader swaps ETH→BTC at stale price 25 BTC/ETH instead of 16.67 BTC/ETH
// LP overpays ~33% in BTC per unit of ETH received
```

### Citations

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L150-151)
```text
        if (_maxRefStaleness > 7 days) revert MaxRefStalenessOutOfBounds(); // 0 allowed = same-block reference
        MAX_REF_STALENESS = _maxRefStaleness;
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L258-272)
```text
    function _getBidAndAskPrice() internal returns (uint128, uint128) {
        (uint256 mid, uint256 spreadBps, , bool ok) = _readLeg(baseFeedId);
        if (!ok) return (0, type(uint128).max);

        bytes32 _quote = quoteFeedId;
        if (_quote != bytes32(0)) {
            (uint256 mid2, uint256 spreadBps2, , bool ok2) = _readLeg(_quote);
            if (!ok2 || mid2 == 0) return (0, type(uint128).max);
            // Synthetic ratio (8-decimal): mid1 / mid2. Relative uncertainties of a ratio add.
            mid = Math.mulDiv(mid, ORACLE_DECIMALS, mid2);
            spreadBps += spreadBps2;
        }

        return _computeBidAsk(mid, spreadBps);
    }
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L277-295)
```text
    function _readLeg(bytes32 feedId)
        internal returns (uint256 mid, uint256 spreadBps, uint256 refTime, bool ok)
    {
        (mid, spreadBps, , refTime) = IPricedOracle(address(offchainOracle)).price(feedId, msg.sender);

        // Stale reference → not ok. Clamping to a stale anchor is the one false-safety case.
        if (_isStale(refTime, block.timestamp, MAX_REF_STALENESS)) return (mid, spreadBps, refTime, false);

        // Basic validity — mid positive, spreadBps not the stalled/off-hours marker (the Chainlink oracle
        // writes spreadBps = ORACLE_BPS when an RWA market is closed).
        if (mid == 0 || spreadBps >= ORACLE_BPS) return (mid, spreadBps, refTime, false);

        // Per-leg price guard.
        (uint128 guardMin, uint128 guardMax) = offchainOracle.priceGuard(feedId);
        guardMax = guardMax == 0 ? type(uint128).max : guardMax;
        if (mid < guardMin || mid > guardMax) return (mid, spreadBps, refTime, false);

        ok = true;
    }
```

**File:** smart-contracts-poc/contracts/AnchoredProviderFactory.sol (L153-155)
```text
    /// @param quoteFeedId optional second feed for synthetic ratio quoting (zero = single-feed). The
    ///        envelope is keyed on `baseFeedId` (the provider's class); the ref feed only contributes its
    ///        uncertainty and is validated for existence at provider construction.
```

**File:** smart-contracts-poc/contracts/AnchoredProviderFactory.sol (L171-180)
```text
        bytes32 classId = feedClass[baseFeedId];
        if (classId == bytes32(0)) classId = DEFAULT_CLASS;

        Envelope storage env = envelopes[classId];
        if (!env.exists) revert EnvelopeNotFound(classId);
        if (
            minMargin < env.minMarginMin || minMargin > env.minMarginMax
            || maxRefStaleness < env.stalenessMin || maxRefStaleness > env.stalenessMax
            || maxSpreadBps < env.maxSpreadMin || maxSpreadBps > env.maxSpreadMax
        ) revert ParamsOutOfEnvelope();
```
