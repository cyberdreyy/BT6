### Title
Single `MAX_REF_STALENESS` Applied to Both Legs of Synthetic Ratio Feeds Allows Stale Prices to Reach Pool Swaps — (`AnchoredPriceProvider.sol`)

---

### Summary

`AnchoredPriceProvider` supports synthetic ratio quoting (e.g., BTC/ETH = BTC/USD ÷ ETH/USD) via a `quoteFeedId`. Both the base and quote feed legs are validated against the **same single immutable** `MAX_REF_STALENESS`. The `AnchoredProviderFactory` envelope system validates staleness bounds only against `baseFeedId`'s class, not `quoteFeedId`'s. When the two feeds have different heartbeat intervals and `MAX_REF_STALENESS` is set to the slower feed's heartbeat, the faster-updating feed can be significantly stale while still passing the check, causing a stale synthetic price to reach pool swaps.

---

### Finding Description

In `AnchoredPriceProvider._getBidAndAskPrice()`, when `quoteFeedId != bytes32(0)` (synthetic mode), both `_readLeg(baseFeedId)` and `_readLeg(quoteFeedId)` are called: [1](#0-0) 

Inside `_readLeg()`, staleness is checked using the single `MAX_REF_STALENESS` for **both** legs: [2](#0-1) 

`MAX_REF_STALENESS` is a single immutable set once at construction: [3](#0-2) 

In `AnchoredProviderFactory.createAnchoredProvider()`, the envelope validation for `maxRefStaleness` is keyed **only on `baseFeedId`'s class**, not `quoteFeedId`'s: [4](#0-3) 

The factory's own NatSpec confirms this gap explicitly:

> *"The envelope is keyed on `baseFeedId` (the provider's class); the ref feed only contributes its uncertainty and is validated for existence at provider construction."* [5](#0-4) 

**Concrete scenario:**

| Feed | Heartbeat | `MAX_REF_STALENESS` | Stale after 59 min? | Passes check? |
|---|---|---|---|---|
| BTC/USD (`baseFeedId`) | 1 hour | 3600 s | No | Yes |
| ETH/USD (`quoteFeedId`) | 30 seconds | 3600 s | **Yes** | **Yes (incorrectly)** |

The factory envelope for BTC/USD allows `maxRefStaleness` up to 1 hour. A deployer who sets `maxRefStaleness = 3600` is within the envelope. But ETH/USD, which updates every 30 seconds, can be 59 minutes stale and still pass `_isStale(refTime, block.timestamp, 3600)`. The stale ETH/USD price is then used to compute the synthetic BTC/ETH mid: [6](#0-5) 

This stale synthetic mid flows into `_computeBidAsk()` and ultimately into the bid/ask quotes returned to the pool swap.

---

### Impact Explanation

**Bad-price execution** — a stale quote-leg price produces an incorrect synthetic mid (e.g., BTC/ETH), which propagates through the band clamp and is returned as the pool's bid/ask. Swaps execute at the wrong price: if the stale ETH/USD is lower than the true price, the synthetic BTC/ETH is inflated, and traders selling BTC receive fewer ETH than they should (or vice versa). This is a direct loss of user principal on every swap during the staleness window.

---

### Likelihood Explanation

**Medium.** The `createAnchoredProvider()` function is permissionless — any user can deploy a synthetic provider. The factory envelope only guards `baseFeedId`'s staleness class and explicitly does not validate `quoteFeedId`'s heartbeat requirements. A deployer following the factory's guidance will set `maxRefStaleness` within the envelope for `baseFeedId`, unaware that `quoteFeedId` requires a tighter bound. The trigger (pusher failing to update one feed due to network issues or congestion) is a realistic operational scenario, not a contrived attack.

---

### Recommendation

Add a separate staleness parameter for the quote leg, or enforce that `maxRefStaleness` is validated against both feeds' envelopes in the factory. At minimum, the factory should require the deployer to supply a `maxQuoteRefStaleness` when `quoteFeedId != 0`, and validate it against `quoteFeedId`'s class envelope:

```solidity
// In AnchoredPriceProvider constructor:
uint256 public immutable MAX_REF_STALENESS;       // for baseFeedId
uint256 public immutable MAX_QUOTE_REF_STALENESS; // for quoteFeedId (new)

// In _readLeg, pass the appropriate threshold per leg:
if (_isStale(refTime, block.timestamp, isQuoteLeg ? MAX_QUOTE_REF_STALENESS : MAX_REF_STALENESS))
    return (mid, spreadBps, refTime, false);
```

The factory should validate `maxQuoteRefStaleness` against `feedClass[quoteFeedId]`'s envelope when `quoteFeedId != 0`.

---

### Proof of Concept

1. Admin sets an envelope for BTC/USD class: `stalenessMin=60, stalenessMax=3600`.
2. Deployer calls `createAnchoredProvider(oracle, btcUsdFeedId, ethUsdFeedId, ..., maxRefStaleness=3600, ...)`. ETH/USD has a 30-second heartbeat; BTC/USD has a 1-hour heartbeat. The call succeeds — `3600` is within the BTC/USD envelope.
3. `MAX_REF_STALENESS = 3600` is stored as an immutable in the deployed `AnchoredPriceProvider`.
4. The ETH/USD pusher fails to update for 59 minutes (e.g., network congestion).
5. A pool swap calls `getBidAndAskPrice()` on the provider.
6. `_readLeg(ethUsdFeedId)` fetches the 59-minute-old ETH/USD price. `_isStale(refTime, block.timestamp, 3600)` evaluates `3540 > 3600` → **false** → staleness check passes.
7. The stale ETH/USD price is used: `mid = mulDiv(btcUsdMid, 1e8, staleEthUsdMid)`.
8. `_computeBidAsk` produces bid/ask from the stale synthetic mid.
9. The pool swap executes at the incorrect price, causing loss to one side of the trade. [7](#0-6)

### Citations

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L150-151)
```text
        if (_maxRefStaleness > 7 days) revert MaxRefStalenessOutOfBounds(); // 0 allowed = same-block reference
        MAX_REF_STALENESS = _maxRefStaleness;
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
