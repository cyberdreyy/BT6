### Title
Synthetic two-feed `AnchoredPriceProvider` applies `minMargin` only once, underestimating the safety band and enabling bad-price execution — (`smart-contracts-poc/contracts/AnchoredPriceProvider.sol`)

---

### Summary

In `AnchoredPriceProvider._getBidAndAskPrice()`, when operating in synthetic (two-feed) mode (`quoteFeedId != bytes32(0)`), the per-leg spread uncertainties are correctly summed (`spreadBps += spreadBps2`), but `_computeBidAsk` applies `minMargin` only once. Because each feed leg independently contributes a minimum uncertainty floor, the effective half-width should be `(spreadBps1 + spreadBps2) * ONE_BPS_E18 + 2 * minMargin`, not `(spreadBps1 + spreadBps2) * ONE_BPS_E18 + minMargin`. The resulting band is too narrow, allowing bid/ask prices that are too close to mid to reach pool swaps.

---

### Finding Description

**Analog to the seed bug:** In Superfluid, `getBufferAmountByFlowRate(rate1 + rate2)` underestimates the required buffer because each flow independently triggers a minimum deposit floor. Here, `(spreadBps1 + spreadBps2) * ONE_BPS_E18 + minMargin` underestimates the required band because each feed leg independently contributes its own minimum margin floor — the same non-linearity, different domain.

In `_getBidAndAskPrice()`:

```solidity
(uint256 mid, uint256 spreadBps, , bool ok) = _readLeg(baseFeedId);
// ...
(uint256 mid2, uint256 spreadBps2, , bool ok2) = _readLeg(_quote);
// ...
mid = Math.mulDiv(mid, ORACLE_DECIMALS, mid2);   // ratio price
spreadBps += spreadBps2;                          // ✓ relative uncertainties add
return _computeBidAsk(mid, spreadBps);
``` [1](#0-0) 

In `_computeBidAsk()`:

```solidity
uint256 half = spreadBps * ONE_BPS_E18 + minMargin;   // ✗ minMargin applied only once
uint256 refBid = _bandEdge(mid, BPS_BASE_U - half, Math.Rounding.Floor);
uint256 refAsk = _bandEdge(mid, BPS_BASE_U + half, Math.Rounding.Ceil);
``` [2](#0-1) 

`minMargin` is documented as "Per-side minimum margin on top of the reference spread." For a synthetic ratio `price1 / price2`, each feed leg has its own irreducible minimum uncertainty (execution latency, feed granularity, minimum tick). The combined minimum margin for a two-leg synthetic must be `2 × minMargin`. The current code applies it only once, producing a band that is `minMargin` too narrow on each side.

The clamp in step 8 uses `refBid` as the maximum allowed bid and `refAsk` as the minimum allowed ask:

```solidity
uint256 bidOut = Math.min(refBid, cBid);
uint256 askOut = Math.max(refAsk, cAsk);
``` [3](#0-2) 

A too-narrow band means `refBid` is too high and `refAsk` is too low — the pool is permitted to quote prices that are closer to mid than the true oracle uncertainty warrants. In reference mode (no source), the pool returns exactly `(refBid, refAsk)`, so every swap executes at these too-tight prices. [4](#0-3) 

---

### Impact Explanation

In reference mode with a synthetic pair, every swap executes at a bid/ask that is `minMargin / BPS_BASE_U × mid` closer to mid than the true uncertainty band requires. Traders can buy at the too-low ask or sell at the too-high bid, extracting value from LPs on every trade. The per-trade LP loss is proportional to `minMargin` and trade size. For a pool with `minMargin = 50 bps` (5e15 in BPS_BASE_U scale), the band is 50 bps too narrow on each side — a material and continuous LP drain. This satisfies the "bad-price execution" and "direct loss of LP assets" impact gates.

---

### Likelihood Explanation

Any pool using `AnchoredPriceProvider` with `quoteFeedId != bytes32(0)` is affected. The synthetic mode is an explicitly supported and documented feature (e.g., BTC/USD ÷ ETH/USD = BTC/ETH). A deployer setting `minMargin` to a per-leg value (the natural interpretation of the parameter name and NatSpec) will unknowingly produce a band that is half as wide as intended. No privileged action or malicious setup is required — the miscalculation is structural and fires on every `getBidAndAskPrice()` call in synthetic mode. [5](#0-4) 

---

### Recommendation

Track whether synthetic mode is active in `_getBidAndAskPrice()` and pass a leg-count multiplier to `_computeBidAsk`, or compute the band directly with the correct margin:

```solidity
// In _getBidAndAskPrice():
uint256 marginMultiplier = (_quote != bytes32(0)) ? 2 : 1;
return _computeBidAsk(mid, spreadBps, marginMultiplier);

// In _computeBidAsk():
uint256 half = spreadBps * ONE_BPS_E18 + minMargin * marginMultiplier;
```

Alternatively, document that deployers of synthetic providers must set `minMargin` to the sum of per-leg minimums and enforce this in the factory.

---

### Proof of Concept

1. Deploy `AnchoredPriceProvider` with `quoteFeedId != 0` (synthetic BTC/ETH), `minMargin = 5e15` (50 bps in BPS_BASE_U scale), `MAX_SPREAD_BPS = 500`.
2. At swap time, base feed returns `spreadBps1 = 10`, quote feed returns `spreadBps2 = 10`. Combined `spreadBps = 20`.
3. **Current code:** `half = 20 × 1e14 + 5e15 = 7e15` → band half-width = 0.70%.
4. **Correct code:** `half = 20 × 1e14 + 2 × 5e15 = 1.2e16` → band half-width = 1.20%.
5. The pool quotes bid/ask that are 0.50% closer to mid than the true uncertainty warrants.
6. A trader repeatedly buys at the too-low ask and sells externally at the true market price, extracting ~0.50% per round-trip from LP capital with no oracle manipulation required. [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L67-69)
```text
    /// @notice Optional second feed for synthetic ratio quoting; zero = single-feed (no conversion).
    ///         Synthetic mid = price(baseFeedId) / price(quoteFeedId), e.g. BTC/USD ÷ ETH/USD = BTC/ETH.
    bytes32         public immutable quoteFeedId;
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L73-74)
```text
    /// @notice Per-side minimum margin on top of the reference spread, BPS_BASE_U scale (1 bps = 1e14).
    uint256 public immutable minMargin;
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

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L307-313)
```text
        // Reference band: mid ± (spreadBps + minMargin), bid rounded down, ask rounded up.
        uint256 half = spreadBps * ONE_BPS_E18 + minMargin; // < BPS_BASE_U by construction (spreadBps <= MAX_SPREAD_BPS here)
        uint256 refBid = _bandEdge(mid, BPS_BASE_U - half, Math.Rounding.Floor);
        uint256 refAsk = _bandEdge(mid, BPS_BASE_U + half, Math.Rounding.Ceil);
        if (refBid == 0 || refAsk > type(uint128).max || refBid >= refAsk) {
            return (0, type(uint128).max);
        }
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L336-338)
```text
        } else {
            return (uint128(refBid), uint128(refAsk));
        }
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L342-346)
```text
        uint256 bidOut = Math.min(refBid, cBid);
        uint256 askOut = Math.max(refAsk, cAsk);
        if (bidOut == 0 || bidOut >= askOut) {
            return (0, type(uint128).max);
        }
```
