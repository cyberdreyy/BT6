### Title
`confidenceParam` Zero-Default Silently Discards Oracle Spread, Returning a Hardcoded-Margin Bid/Ask to Pools — (File: `smart-contracts-poc/contracts/PriceProvider.sol`)

---

### Summary

`PriceProvider` zero-initialises `confidenceParam` (Solidity default). Until the factory explicitly calls `setConfidenceParam`, the oracle's reported spread is multiplied by zero and discarded. The bid/ask returned to every pool swap is computed from the immutable `marginStep` alone — a static, construction-time constant — regardless of how wide the oracle's actual uncertainty is. This is the direct analog of the hardcoded `1e18` exchange rate: a fixed value substitutes for a live market signal, allowing swaps to execute at prices that do not reflect real uncertainty.

---

### Finding Description

In `PriceProvider._getBidAndAskPrice()`:

```solidity
// line 216
uint256 adjustedSpread = spread * confidenceParam;
// line 217
(uint256 bid, uint256 ask) = _getBidAskFrom(mid, adjustedSpread);
``` [1](#0-0) 

`confidenceParam` is a plain storage slot, zero-initialised by the EVM. The factory may call `setConfidenceParam` later, but there is no constructor requirement, no deployment guard, and no revert path that prevents the provider from being attached to a live pool while `confidenceParam == 0`. [2](#0-1) 

When `confidenceParam == 0`:

```
adjustedSpread = spread * 0 = 0
delta          = mid * 0 / CONFIDENCE_BASE = 0
bid            = mid - 0 = mid
ask            = mid + 0 = mid
``` [3](#0-2) 

Both legs then pass through `_applyBidAdjustments` / `_applyAskAdjustments`, which apply the immutable `stepBidFactor = BPS_BASE_U − marginStep` and `stepAskFactor = BPS_BASE_U + marginStep`:

```
bidOut = mid × Q64 × (BPS_BASE_U − marginStep) / STEP_DENOM
askOut = mid × Q64 × (BPS_BASE_U + marginStep) / STEP_DENOM
``` [4](#0-3) 

Provided `marginStep > 0`, `bidOut < askOut` and the function returns a valid price — **without ever using the oracle's spread**. The oracle spread is read and validated (stall-marker check at line 203), but its value is then multiplied by zero and thrown away. [5](#0-4) 

`AnchoredPriceProvider` does **not** share this flaw: it derives the reference band directly from `spreadBps` before any confidence shaping, so the band is always at least `mid ± (spreadBps + minMargin)` regardless of `confidenceParam`. [6](#0-5) 

---

### Impact Explanation

LPs in any pool backed by a `PriceProvider` with `confidenceParam == 0` quote a spread of `2 × marginStep` around mid, regardless of oracle uncertainty. If the oracle reports `spreadBps = 500` (5 %) but `marginStep = 100 bps` (1 %), the pool's effective bid is `mid × 0.99` while the oracle-correct bid would be `mid × 0.95`. An informed trader can sell token0 to the pool at `mid × 0.99` when the real market price is `mid × 0.95`, extracting `4 %` of notional per swap from LP reserves. Repeated across many swaps this drains LP principal — a direct, quantifiable loss matching the "bad-price execution" and "pool insolvency" impact categories.

---

### Likelihood Explanation

The trigger is the **default deployment state**: `confidenceParam` is zero until the factory acts. Any pool attached to a freshly deployed `PriceProvider` before `setConfidenceParam` is called is immediately vulnerable. No privileged attacker action is required — any ordinary swap suffices. The factory's `CONFIDENCE_COOLDOWN` (1 minute) means there is always at least a 1-minute window of exposure after deployment. [7](#0-6) 

---

### Recommendation

Require a non-zero `confidenceParam` at construction, or revert in `getBidAndAskPrice` when `confidenceParam == 0`:

```solidity
// Option A — constructor enforcement
constructor(..., uint256 _initialConfidence, ...) {
    require(_initialConfidence > 0 && _initialConfidence <= CONFIDENCE_MAX);
    confidenceParam = _initialConfidence;
    lastConfidenceUpdate = block.timestamp;
}

// Option B — read-time guard
if (confidenceParam == 0) return (0, type(uint128).max); // fail closed
```

Alternatively, mirror `AnchoredPriceProvider`'s design: derive the band from `spreadBps` unconditionally and let `confidenceParam` only shape within that band, so a zero value degenerates to the band edge rather than to a zero-spread quote.

---

### Proof of Concept

1. Deploy `PriceProvider` with `marginStep = 100 bps` (1 %). Do **not** call `setConfidenceParam`. `confidenceParam == 0`.
2. Oracle pushes `mid = 1 000 USD, spreadBps = 500` (5 % uncertainty).
3. Pool calls `getBidAndAskPrice()`:
   - `adjustedSpread = 500 × 0 = 0`
   - `bid = 1 000`, `ask = 1 000` (pre-step)
   - `bidOut ≈ 990` (Q64-scaled), `askOut ≈ 1 010`
4. Real market price falls to `950` (within the oracle's 5 % band, outside the 1 % `marginStep` band).
5. Attacker calls `swap(token0 → token1)`. Pool accepts token0 at bid `≈ 990`, paying 990 token1 per token0.
6. Attacker immediately sells token0 on the open market at `950`. Net gain per token0: `990 − 950 = 40 token1`, funded entirely by LP reserves.
7. The oracle's `spreadBps = 500` would have set bid at `≈ 950`, preventing the trade — but it was silently discarded. [8](#0-7)

### Citations

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L40-41)
```text
    uint256 public confidenceParam;
    uint256 public lastConfidenceUpdate;
```

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L92-104)
```text
    function setConfidenceParam(uint256 newValue) external {
        require(msg.sender == factory, OnlyFactory());
        if (newValue > CONFIDENCE_MAX) {
            revert ConfidenceParamOutOfBounds();
        }
        if (block.timestamp < lastConfidenceUpdate + CONFIDENCE_COOLDOWN) {
            revert CooldownNotElapsed();
        }

        confidenceParam = newValue;
        lastConfidenceUpdate = block.timestamp;
        emit ConfidenceParamSet(newValue);
    }
```

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L137-141)
```text
    function _getBidAskFrom(uint256 midPrice, uint256 confidence) internal pure returns (uint256 bid, uint256 ask) {
        uint256 delta = midPrice * confidence / CONFIDENCE_BASE;
        bid = delta >= midPrice ? 0 : midPrice - delta;
        ask = midPrice + delta;
    }
```

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L147-159)
```text
    function _applyBidAdjustments(
        uint256 price
    ) internal view returns (uint256 out, bool ok) {
        return _applyStepAdjustment(price, stepBidFactor, Math.Rounding.Floor);
    }

    /// @notice Ask adjustment: rounds UP (ceil).
    ///         out = price * Q64 * stepAskFactor / 1e26
    function _applyAskAdjustments(
        uint256 price
    ) internal view returns (uint256 out, bool ok) {
        return _applyStepAdjustment(price, stepAskFactor, Math.Rounding.Ceil);
    }
```

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L191-231)
```text
    function _getBidAndAskPrice() internal returns (uint128, uint128) {
        // 1. Read via the unified price(feedId, pool) path, forwarding the pool (msg.sender).
        //    refTime is already in seconds.
        (uint256 mid, uint256 spread, , uint256 refTime) =
            IPricedOracle(address(offchainOracle)).price(offchainFeedId, msg.sender);

        // 2. Staleness check
        if (_isStale(refTime, block.timestamp, MAX_TIME_DELTA)) {
            return (0, type(uint128).max);
        }

        // 3. Basic validity — price must be positive, spread must not be stalled marker
        if (mid == 0 || spread >= ORACLE_BPS) {
            return (0, type(uint128).max);
        }

        // 4. Price guard check (moved from oracle)
        (uint128 guardMin, uint128 guardMax) = offchainOracle.priceGuard(offchainFeedId);
        guardMax = guardMax == 0 ? type(uint128).max : guardMax;
        if (mid < guardMin || mid > guardMax) {
            return (0, type(uint128).max);
        }

        // 5. Compute bid/ask from mid + confidence-adjusted spread
        //    confidenceParam multiplies oracle spread; 0 means no spread
        uint256 adjustedSpread = spread * confidenceParam;
        (uint256 bid, uint256 ask) = _getBidAskFrom(mid, adjustedSpread);

        // 6. Apply marginStep adjustment
        (uint256 bidOut, bool bidOk) = _applyBidAdjustments(bid);
        if (!bidOk || bidOut > type(uint128).max) return (0, type(uint128).max);

        (uint256 askOut, bool askOk) = _applyAskAdjustments(ask);
        if (!askOk || askOut > type(uint128).max) return (0, type(uint128).max);

        // 7. Hard invariant: bid must be strictly less than ask.
        //    Can be violated when marginStep < 0 and confidence is too small.
        if (bidOut >= askOut) return (0, type(uint128).max);

        return (uint128(bidOut), uint128(askOut));
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
