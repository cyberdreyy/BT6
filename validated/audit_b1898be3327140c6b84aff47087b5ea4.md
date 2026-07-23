Looking at the synthetic-ratio path in `AnchoredPriceProvider._getBidAndAskPrice()`, I can identify a native analog to H-05.

---

### Title
Synthetic-ratio `AnchoredPriceProvider` never validates cross-leg timestamp alignment, allowing stale-leg price to reach pool swaps — (`smart-contracts-poc/contracts/AnchoredPriceProvider.sol`)

---

### Summary

When `AnchoredPriceProvider` operates in synthetic-ratio mode (`quoteFeedId != bytes32(0)`), it reads two independent oracle feeds and computes `mid = price(baseFeedId) / price(quoteFeedId)`. Each leg's `refTime` is individually staleness-checked inside `_readLeg`, but the two timestamps are **never compared against each other**. The `refTime` return value is silently discarded at the call site. A swap can therefore execute against a ratio whose numerator and denominator were observed at arbitrarily different points in time — up to `MAX_REF_STALENESS` apart — producing a stale, incorrect bid/ask that reaches the pool.

---

### Finding Description

`_getBidAndAskPrice` calls `_readLeg` twice:

```solidity
(uint256 mid,  uint256 spreadBps,  , bool ok)  = _readLeg(baseFeedId);
...
(uint256 mid2, uint256 spreadBps2, , bool ok2) = _readLeg(_quote);
``` [1](#0-0) 

The third positional return value of `_readLeg` is `refTime`:

```solidity
function _readLeg(bytes32 feedId)
    internal returns (uint256 mid, uint256 spreadBps, uint256 refTime, bool ok)
{
    (mid, spreadBps, , refTime) = IPricedOracle(address(offchainOracle)).price(feedId, msg.sender);
    if (_isStale(refTime, block.timestamp, MAX_REF_STALENESS)) return (mid, spreadBps, refTime, false);
``` [2](#0-1) 

Both call sites drop `refTime` with a bare `,`. Each leg independently passes its own staleness gate, but **no guard checks that `refTime_base` and `refTime_quote` are within any bound of each other**. The constructor permits `MAX_REF_STALENESS` up to 7 days:

```solidity
if (_maxRefStaleness > 7 days) revert MaxRefStalenessOutOfBounds();
``` [3](#0-2) 

The synthetic mid is then computed and fed directly into `_computeBidAsk`:

```solidity
mid = Math.mulDiv(mid, ORACLE_DECIMALS, mid2);
spreadBps += spreadBps2;
...
return _computeBidAsk(mid, spreadBps);
``` [4](#0-3) 

`_computeBidAsk` applies the band clamp and returns the final `(bid, ask)` to the pool with no further timestamp check. [5](#0-4) 

---

### Impact Explanation

The pool executes swaps at the bid/ask returned by `getBidAndAskPrice`. If `baseFeedId` (e.g., BTC/USD) was last pushed 55 minutes ago and `quoteFeedId` (e.g., ETH/USD) was pushed in the current block, the synthetic BTC/ETH ratio reflects BTC's old price divided by ETH's current price. A trader who monitors oracle update cadence can time a swap to exploit the divergence: buy the asset that appears artificially cheap (stale numerator, fresh denominator, or vice-versa) and immediately arbitrage the true market price. LPs absorb the loss. This is a direct bad-price execution impact on pool swaps — within the contest's allowed impact gate.

---

### Likelihood Explanation

- Synthetic-ratio mode is an explicit, documented feature (`quoteFeedId` set at construction).
- Oracle feeds pushed by independent pushers or Chainlink/Pyth adapters naturally update at different cadences.
- The divergence window is observable on-chain (oracle stores per-feed timestamps); no privileged access is required to detect or exploit it.
- No existing guard in `_computeBidAsk`, the spread circuit-breaker, or the price-guard closes this gap — they all operate on the already-computed ratio.

---

### Recommendation

After both legs are read, compare their timestamps and revert if the divergence exceeds a new immutable `MAX_LEG_TIMESTAMP_DELTA`:

```solidity
// In _getBidAndAskPrice, capture both refTimes:
(uint256 mid,  uint256 spreadBps,  uint256 refTime1, bool ok)  = _readLeg(baseFeedId);
...
(uint256 mid2, uint256 spreadBps2, uint256 refTime2, bool ok2) = _readLeg(_quote);

// Cross-leg consistency guard:
uint256 tDiff = refTime1 > refTime2 ? refTime1 - refTime2 : refTime2 - refTime1;
if (tDiff > MAX_LEG_TIMESTAMP_DELTA) return (0, type(uint128).max);
```

`MAX_LEG_TIMESTAMP_DELTA` should be set at construction (immutable), independently of `MAX_REF_STALENESS`, and kept tight (e.g., ≤ 60 seconds for liquid pairs).

---

### Proof of Concept

1. Deploy `AnchoredPriceProvider` with `baseFeedId = BTC/USD`, `quoteFeedId = ETH/USD`, `MAX_REF_STALENESS = 3600` (1 hour).
2. Push a BTC/USD price of **$60 000** at `T = now − 3500 s` (within staleness window). Do not update it again.
3. At `T = now`, push ETH/USD at **$3 000** (current).
4. True BTC/ETH ratio at `T = now` is, say, **22** (BTC rose to $66 000 in the interim).
5. Call `getBidAndAskPrice()`: `_readLeg(baseFeedId)` returns `mid1 = 60_000e8`, `refTime1 = now − 3500` (passes staleness). `_readLeg(quoteFeedId)` returns `mid2 = 3_000e8`, `refTime2 = now` (passes staleness). Synthetic mid = `60_000 / 3_000 = 20` — **10% below true ratio**.
6. Attacker swaps into the pool buying the base asset at the stale-low synthetic price; pool LPs are short-changed by the 10% gap.

### Citations

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L150-151)
```text
        if (_maxRefStaleness > 7 days) revert MaxRefStalenessOutOfBounds(); // 0 allowed = same-block reference
        MAX_REF_STALENESS = _maxRefStaleness;
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L258-271)
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
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L277-284)
```text
    function _readLeg(bytes32 feedId)
        internal returns (uint256 mid, uint256 spreadBps, uint256 refTime, bool ok)
    {
        (mid, spreadBps, , refTime) = IPricedOracle(address(offchainOracle)).price(feedId, msg.sender);

        // Stale reference → not ok. Clamping to a stale anchor is the one false-safety case.
        if (_isStale(refTime, block.timestamp, MAX_REF_STALENESS)) return (mid, spreadBps, refTime, false);

```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L299-349)
```text
    function _computeBidAsk(uint256 mid, uint256 spreadBps)
        internal view returns (uint128, uint128)
    {
        // Circuit breaker: extreme (combined) uncertainty means the feed is clearly broken.
        if (spreadBps > MAX_SPREAD_BPS) {
            return (0, type(uint128).max);
        }

        // Reference band: mid ± (spreadBps + minMargin), bid rounded down, ask rounded up.
        uint256 half = spreadBps * ONE_BPS_E18 + minMargin; // < BPS_BASE_U by construction (spreadBps <= MAX_SPREAD_BPS here)
        uint256 refBid = _bandEdge(mid, BPS_BASE_U - half, Math.Rounding.Floor);
        uint256 refAsk = _bandEdge(mid, BPS_BASE_U + half, Math.Rounding.Ceil);
        if (refBid == 0 || refAsk > type(uint128).max || refBid >= refAsk) {
            return (0, type(uint128).max);
        }

        // Custom quote: source (both variants) or shaped reference quote (customizable variant).
        //    Immutable reference mode quotes the band directly — zero knob SLOADs.
        address _source = source;
        uint256 cBid;
        uint256 cAsk;
        if (_source != address(0)) {
            // 7a. Source mode: any failure (revert, OOG, garbage, zero, inverted) halts — fail
            //     closed. Knobs do NOT post-process the source output (the source shapes itself).
            bool ok;
            (ok, cBid, cAsk) = _readSource(_source);
            if (!ok) {
                return (0, type(uint128).max);
            }
        } else if (MUTABLE_PARAMS) {
            // 7b. Shaped reference quote: mid ± mid·spreadBps·confidence, then the marginStep step
            //     factors — PriceProvider semantics, clamped into the band below.
            bool ok;
            (ok, cBid, cAsk) = _shapedQuote(mid, spreadBps);
            if (!ok) {
                return (0, type(uint128).max);
            }
        } else {
            return (uint128(refBid), uint128(refAsk));
        }

        // 8. Clamp: out-of-band custom quotes are clipped silently to the band edge.
        //    bid ≤ refBid < refAsk ≤ ask, so bid < ask holds by construction.
        uint256 bidOut = Math.min(refBid, cBid);
        uint256 askOut = Math.max(refAsk, cAsk);
        if (bidOut == 0 || bidOut >= askOut) {
            return (0, type(uint128).max);
        }

        return (uint128(bidOut), uint128(askOut));
    }
```
