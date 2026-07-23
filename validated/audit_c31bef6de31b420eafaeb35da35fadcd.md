### Title
Zero-Initialized `confidenceParam` Silently Discards Oracle Spread, Allowing Bad-Price Execution Against Pools — (`smart-contracts-poc/contracts/PriceProvider.sol`, `smart-contracts-poc/contracts/ProtectedPriceProvider.sol`)

---

### Summary

`PriceProvider` and `ProtectedPriceProvider` both zero-initialize `confidenceParam` (Solidity default). When `confidenceParam == 0`, the oracle's spread/confidence interval is multiplied to zero before it reaches the bid/ask computation. The pool then quotes a spread derived solely from the immutable `marginStep`, completely ignoring the oracle's own uncertainty signal. During high-volatility or low-confidence oracle periods, the pool quotes prices that are far tighter than the oracle's reported uncertainty warrants, enabling traders to execute swaps at prices that do not account for that uncertainty — a direct bad-price execution path.

---

### Finding Description

In `PriceProvider._getBidAndAskPrice()`:

```solidity
// PriceProvider.sol line 216
uint256 adjustedSpread = spread * confidenceParam;
(uint256 bid, uint256 ask) = _getBidAskFrom(mid, adjustedSpread);
``` [1](#0-0) 

`confidenceParam` is a plain storage variable with no constructor initialization, so it starts at `0`.

```solidity
// PriceProvider.sol line 40-41
uint256 public confidenceParam;
uint256 public lastConfidenceUpdate;
``` [2](#0-1) 

When `confidenceParam == 0`:

```
adjustedSpread = spread * 0 = 0
delta          = mid * 0 / CONFIDENCE_BASE = 0
bid            = mid - 0 = mid
ask            = mid + 0 = mid
```

The oracle's `spread` (Pyth confidence interval, in bps) is completely discarded. `_getBidAskFrom` returns `bid == ask == mid`. The only spread that survives into the final Q64 output comes from the immutable `marginStep` via `_applyBidAdjustments` / `_applyAskAdjustments`:

```solidity
// PriceProvider.sol lines 220-228
(uint256 bidOut, bool bidOk) = _applyBidAdjustments(bid);
...
(uint256 askOut, bool askOk) = _applyAskAdjustments(ask);
...
if (bidOut >= askOut) return (0, type(uint128).max);
``` [3](#0-2) 

If `marginStep > 0`, the provider returns a valid `(bid, ask)` pair with a fixed spread of `≈ 2 * marginStep / BPS_BASE_U`, regardless of how wide the oracle's confidence interval is at that moment.

The identical pattern exists in `ProtectedPriceProvider`:

```solidity
// ProtectedPriceProvider.sol line 209
uint256 adjustedSpread = spread * confidenceParam;
``` [4](#0-3) 

with the same zero-initialized storage:

```solidity
// ProtectedPriceProvider.sol lines 44-45
uint256 public confidenceParam;
uint256 public lastConfidenceUpdate;
``` [5](#0-4) 

Note the contrast with `AnchoredPriceProvider`, which uses the oracle spread directly and unconditionally in the reference band computation:

```solidity
// AnchoredPriceProvider.sol line 308
uint256 half = spreadBps * ONE_BPS_E18 + minMargin;
``` [6](#0-5) 

`AnchoredPriceProvider` is immune; `PriceProvider` and `ProtectedPriceProvider` are not.

---

### Impact Explanation

The oracle spread (Pyth confidence interval) is the on-chain signal that the reported mid price is uncertain. When it is large, the pool should widen its bid/ask to avoid adverse selection. With `confidenceParam == 0`, the pool quotes a fixed-width spread (`marginStep`-only) even when the oracle is reporting high uncertainty. A trader who observes a large oracle confidence interval can:

1. Identify that the true market price has diverged from the oracle mid by more than `marginStep`
2. Execute a swap against the pool at the artificially tight bid or ask
3. Immediately close the position on an external venue at the true market price

The pool (and its LPs) absorb the loss. This is a direct loss of LP principal — a swap conservation failure where the pool receives the correct token input but quotes a price that does not reflect the oracle's own uncertainty, allowing the trader to extract value the pool was not designed to give.

**Severity: Medium** (High impact per LP loss; Low-Medium likelihood because it requires the oracle to report a meaningfully wide spread while `confidenceParam` remains at zero).

---

### Likelihood Explanation

- `confidenceParam` is zero by default in both contracts. Every newly deployed `PriceProvider` or `ProtectedPriceProvider` is immediately in the vulnerable state.
- The factory must call `setConfidenceParam` with a non-zero value to activate the oracle spread. There is no constructor parameter, no deployment-time enforcement, and no revert if it is never set.
- The `CONFIDENCE_COOLDOWN` (1 minute) means even a factory that tries to set it immediately after deployment has a window of exposure.
- Any pool that goes live before the factory sets `confidenceParam` is exploitable for the duration of that window.

---

### Recommendation

Require a non-zero `confidenceParam` at construction time, or initialize it to a safe default (e.g., `CONFIDENCE_BASE / spread_typical`) in the constructor. At minimum, add a constructor parameter:

```solidity
constructor(
    ...
    uint256 _confidenceParam,
    ...
) {
    require(_confidenceParam > 0 && _confidenceParam <= CONFIDENCE_MAX, "bad confidence");
    confidenceParam = _confidenceParam;
    lastConfidenceUpdate = block.timestamp;
}
```

This eliminates the zero-spread default state and ensures the oracle's confidence interval is always factored into the bid/ask from the first swap.

---

### Proof of Concept

1. Deploy `PriceProvider` with `marginStep = 5e15` (0.5%), `confidenceParam` left at default `0`.
2. Pyth oracle reports `mid = 2000e8` (ETH/USD), `spread = 500` (5% confidence interval — high volatility).
3. Call `getBidAndAskPrice()`:
   - `adjustedSpread = 500 * 0 = 0`
   - `delta = 0`
   - `bid_8dec = ask_8dec = 2000e8`
   - After step: `bidOut ≈ 2000e8 * (1e18 - 5e15) / 1e26 * Q64`, `askOut ≈ 2000e8 * (1e18 + 5e15) / 1e26 * Q64`
   - Effective spread: **1%** (marginStep only)
4. True market price (per Pyth confidence interval) could be anywhere in `[1900, 2100]` — a 10% range.
5. Attacker swaps `token0 → token1` at the pool's ask (≈ `2000 * 1.005 = 2010`), then sells on a CEX at `2100` — extracting `≈ 90 USD` per ETH from LP funds, with the pool's spread providing no protection against the oracle's reported uncertainty.

### Citations

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L40-41)
```text
    uint256 public confidenceParam;
    uint256 public lastConfidenceUpdate;
```

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L216-217)
```text
        uint256 adjustedSpread = spread * confidenceParam;
        (uint256 bid, uint256 ask) = _getBidAskFrom(mid, adjustedSpread);
```

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L220-228)
```text
        (uint256 bidOut, bool bidOk) = _applyBidAdjustments(bid);
        if (!bidOk || bidOut > type(uint128).max) return (0, type(uint128).max);

        (uint256 askOut, bool askOk) = _applyAskAdjustments(ask);
        if (!askOk || askOut > type(uint128).max) return (0, type(uint128).max);

        // 7. Hard invariant: bid must be strictly less than ask.
        //    Can be violated when marginStep < 0 and confidence is too small.
        if (bidOut >= askOut) return (0, type(uint128).max);
```

**File:** smart-contracts-poc/contracts/ProtectedPriceProvider.sol (L44-45)
```text
    uint256 public confidenceParam;
    uint256 public lastConfidenceUpdate;
```

**File:** smart-contracts-poc/contracts/ProtectedPriceProvider.sol (L209-210)
```text
        uint256 adjustedSpread = spread * confidenceParam;
        (uint256 bid, uint256 ask) = _getBidAskFrom(price, adjustedSpread);
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L308-308)
```text
        uint256 half = spreadBps * ONE_BPS_E18 + minMargin; // < BPS_BASE_U by construction (spreadBps <= MAX_SPREAD_BPS here)
```
