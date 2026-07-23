### Title
Synthetic Ratio Truncates to Zero for Low-Price Base Tokens, Permanently Bricking Pool Swaps — (`smart-contracts-poc/contracts/AnchoredPriceProvider.sol`)

### Summary

`AnchoredPriceProvider._getBidAndAskPrice()` computes a synthetic cross-pair mid price as `Math.mulDiv(mid, ORACLE_DECIMALS, mid2)` (i.e., `mid * 1e8 / mid2`). Both `mid` and `mid2` are 8-decimal oracle prices. When the base token price is sufficiently small relative to the quote token price, integer division truncates the result to 0. The code does not check for a zero ratio after the division; it passes `mid = 0` directly into `_computeBidAsk`, which returns the `(0, type(uint128).max)` sentinel, causing `getBidAndAskPrice()` to revert with `FeedStalled` on every subsequent swap. The pool becomes permanently unusable for swaps.

### Finding Description

In `_getBidAndAskPrice()`, the synthetic ratio path is:

```solidity
mid = Math.mulDiv(mid, ORACLE_DECIMALS, mid2);
spreadBps += spreadBps2;
// no zero-check on mid here
return _computeBidAsk(mid, spreadBps);
``` [1](#0-0) 

`ORACLE_DECIMALS` is `1e8`. The division is:

```
mid_ratio = mid_base * 1e8 / mid_quote
```

Both prices are stored in 8-decimal format. If `mid_base * 1e8 < mid_quote`, the result is 0 due to integer truncation. This happens whenever the base token price is more than `1e8` times smaller than the quote token price in USD terms — for example:

- Base: SHIB/USD at $0.00001 → `mid = 1_000` (8-dec)
- Quote: BTC/USD at $60,000 → `mid2 = 6_000_000_000_000` (8-dec)
- Ratio: `1_000 * 1e8 / 6_000_000_000_000 = 1e11 / 6e12 = 0`

The `_readLeg` guard only checks that each individual leg's price is non-zero:

```solidity
if (mid == 0 || spreadBps >= ORACLE_BPS) return (mid, spreadBps, refTime, false);
``` [2](#0-1) 

Both legs pass this check with valid non-zero prices. The zero only appears after the ratio division. `_computeBidAsk(0, spreadBps)` then computes `_bandEdge(0, ...) = 0`, hits the `refBid == 0` guard, and returns the sentinel:

```solidity
uint256 refBid = _bandEdge(mid, BPS_BASE_U - half, Math.Rounding.Floor);
...
if (refBid == 0 || refAsk > type(uint128).max || refBid >= refAsk) {
    return (0, type(uint128).max);
}
``` [3](#0-2) 

`getBidAndAskPrice()` then reverts:

```solidity
if (bid == 0 || ask == type(uint128).max) revert FeedStalled();
``` [4](#0-3) 

Every pool swap calls `getBidAndAskPrice()`, so all swaps revert permanently.

### Impact Explanation

The pool's swap functionality is permanently broken. No swap can execute while the price ratio remains below `1e-8`. LPs cannot earn fees and the pool is effectively dead. This matches the allowed impact: "Broken core pool functionality causing loss of funds or unusable withdraw/swap/liquidity flows."

### Likelihood Explanation

The condition `mid_base * 1e8 < mid_quote` is reachable in two ways:

1. **At deployment**: A pool is created with a base token whose price is already below the threshold (e.g., any meme coin paired synthetically against BTC or ETH).
2. **Post-deployment price crash**: A pool is created with a valid ratio, but the base token price crashes below the threshold. This is the exact scenario described in the WooFi report. Once the price drops below the threshold, every swap reverts and the pool cannot recover until the price recovers above the threshold.

No privileged action is required — the trigger is normal market price movement. The `AnchoredPriceProvider` explicitly supports synthetic ratio mode for production pools.

### Recommendation

Add a zero-check on the computed ratio before passing it to `_computeBidAsk`:

```solidity
mid = Math.mulDiv(mid, ORACLE_DECIMALS, mid2);
if (mid == 0) return (0, type(uint128).max); // precision loss: ratio truncated to zero
spreadBps += spreadBps2;
```

Alternatively, increase the intermediate precision by scaling the numerator before dividing, e.g., using a higher-precision intermediate (similar to the WooFi recommendation of moving from 8 to 18 decimals for the ratio computation).

### Proof of Concept

```solidity
// SHIB/USD = 0.00001 → mid = 1_000 (8-dec)
// BTC/USD  = 60_000  → mid2 = 6_000_000_000_000 (8-dec)
oracle.setData(FEED1, uint64(1_000), 3, 0, block.timestamp);           // SHIB/USD
oracle.setData(FEED2, uint64(6_000_000_000_000), 5, 0, block.timestamp); // BTC/USD

// Math.mulDiv(1_000, 1e8, 6_000_000_000_000) = 1e11 / 6e12 = 0
// _computeBidAsk(0, 8) → refBid = 0 → returns (0, type(uint128).max)
// getBidAndAskPrice() → revert FeedStalled()

vm.expectRevert(AnchoredPriceProvider.FeedStalled.selector);
provider.getBidAndAskPrice(); // every swap on this pool reverts
``` [5](#0-4) [6](#0-5)

### Citations

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L216-216)
```text
        if (bid == 0 || ask == type(uint128).max) revert FeedStalled();
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

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L287-287)
```text
        if (mid == 0 || spreadBps >= ORACLE_BPS) return (mid, spreadBps, refTime, false);
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L299-313)
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
```
