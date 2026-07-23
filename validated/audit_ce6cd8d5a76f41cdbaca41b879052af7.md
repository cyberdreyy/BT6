### Title
`spread1` Return Value Silently Discarded by All Price Providers — Ask Band Too Tight When Asymmetric Spreads Are Configured - (File: `smart-contracts-poc/contracts/PriceProvider.sol`, `ProtectedPriceProvider.sol`, `AnchoredPriceProvider.sol`)

---

### Summary

`IPricedOracle.price()` returns four values: `(uint256 mid, uint256 spread, uint16 spread1, uint256 refTime)`. The third return value — `spread1`, the ask-side spread — is silently discarded by every price provider in the system. When the `CompressedOracleV1` is configured with asymmetric spreads (`s1 > s0`), the ask band edge and all downstream ask prices are computed using only `spread0`, making the ask too tight. Traders can buy tokens at a price closer to mid than the oracle intends, causing the pool to receive less than the oracle-mandated price on every such swap.

---

### Finding Description

`IPricedOracle` is the attributed read interface used by all price providers:

```solidity
// smart-contracts-poc/contracts/interfaces/IPricedOracle.sol
function price(bytes32 feedId, address pool)
    external
    returns (uint256 mid, uint256 spread, uint16 spread1, uint256 refTime);
```

`CompressedOracleV1` stores two independent codebook-encoded spread indices per feed position — `s0` (bid-side) and `s1` (ask-side) — and returns both through `price()`:

```solidity
// CompressedOracle.sol _price()
return (uint256(data.price), uint256(data.spread0), data.spread1, data.timestampMs.toSeconds());
```

All three production price providers call `price()` and drop the third return value with a bare `,`:

**`PriceProvider.sol` line 194:**
```solidity
(uint256 mid, uint256 spread, , uint256 refTime) =
    IPricedOracle(address(offchainOracle)).price(offchainFeedId, msg.sender);
```

**`ProtectedPriceProvider.sol` line 182:**
```solidity
(uint256 mid, uint256 spread, , uint256 refTime) =
    IPricedOracle(address(offchainOracle)).price(offchainFeedId, msg.sender);
```

**`AnchoredPriceProvider._readLeg()` line 280:**
```solidity
(mid, spreadBps, , refTime) = IPricedOracle(address(offchainOracle)).price(feedId, msg.sender);
```

After discarding `spread1`, every provider uses only `spread` (`spread0`) symmetrically for both bid and ask. In `AnchoredPriceProvider._computeBidAsk()`:

```solidity
uint256 half = spreadBps * ONE_BPS_E18 + minMargin;
uint256 refBid = _bandEdge(mid, BPS_BASE_U - half, Math.Rounding.Floor);
uint256 refAsk = _bandEdge(mid, BPS_BASE_U + half, Math.Rounding.Ceil);
```

`half` is identical for both sides. If the oracle feed has `s1 > s0` (ask spread wider than bid spread), the correct `refAsk` should use `spread1` in `half`, but it uses `spread0` instead. The ask band edge is therefore too tight — closer to mid than the oracle intends.

The same flaw applies to `PriceProvider` and `ProtectedPriceProvider`, where `_getBidAskFrom(mid, adjustedSpread)` applies the same `delta` to both bid and ask using only `spread0 * confidenceParam`.

Additionally, the `AnchoredPriceProvider` circuit breaker only checks `spread0`:

```solidity
if (mid == 0 || spreadBps >= ORACLE_BPS) return (mid, spreadBps, refTime, false);
```

If `spread1 >= ORACLE_BPS` (the stall/off-hours sentinel) but `spread0 < ORACLE_BPS`, the stall is not detected and quoting continues with a broken ask-side spread.

---

### Impact Explanation

When `spread1 > spread0` for a feed:

- `refAsk` is computed as `mid × (1 + spread0 + minMargin)` instead of `mid × (1 + spread1 + minMargin)`.
- The pool's ask price is too low — closer to mid than the oracle mandates.
- Every swap that buys the base token executes at a price that is too favorable to the trader.
- The pool receives less quote token per unit of base token sold than the oracle-intended price.
- The shortfall per swap is proportional to `(spread1 − spread0) × mid × swapSize`, which can be material for large swaps or feeds with wide asymmetric spreads.

This is a direct loss of pool principal on every buy-side swap while the misconfigured feed is active. It matches the "bad-price execution: unclamped bid/ask quote reaches a pool swap" and "swap conservation failure" impact categories.

---

### Likelihood Explanation

- The `CompressedOracleV1` slot word explicitly encodes two independent 8-bit codebook indices (`s0`, `s1`) per feed position, and the oracle interface explicitly returns both. Asymmetric spreads are a supported, documented configuration.
- Any authorized pusher (delegated via `allowPushers` or `allowContractPushers`) can push a slot word with `s1 > s0`. The pusher does not need to be malicious — a legitimate pusher providing correct asymmetric market data (e.g., wider ask spread during low liquidity) triggers the bug automatically.
- No special swap parameters are needed. Any trader executing a normal buy-side swap against an affected pool benefits from the too-tight ask.
- The bug is present in all three provider contracts and is not gated by any configuration flag.

---

### Recommendation

1. Propagate `spread1` through `_readLeg` and all internal pricing functions.
2. In `AnchoredPriceProvider._computeBidAsk`, use `spread0` for the bid half-width and `spread1` for the ask half-width:
   ```solidity
   uint256 bidHalf = spreadBps0 * ONE_BPS_E18 + minMargin;
   uint256 askHalf = spreadBps1 * ONE_BPS_E18 + minMargin;
   uint256 refBid = _bandEdge(mid, BPS_BASE_U - bidHalf, Math.Rounding.Floor);
   uint256 refAsk = _bandEdge(mid, BPS_BASE_U + askHalf, Math.Rounding.Ceil);
   ```
3. In `PriceProvider` and `ProtectedPriceProvider`, apply `spread0` to the bid delta and `spread1` to the ask delta in `_getBidAskFrom`.
4. Extend the stall check to also halt when `spread1 >= ORACLE_BPS`.

---

### Proof of Concept

1. Deploy `CompressedOracleV1`. Push a slot word for feed position 0 with `s0 = 5` (≈5 bps bid spread) and `s1 = 200` (≈500 bps ask spread) at the current timestamp.
2. Deploy `AnchoredPriceProvider` pointing at this oracle feed, with `minMargin = 0`, `MAX_SPREAD_BPS = 1000`, `MAX_REF_STALENESS = 1 hour`.
3. Call `getBidAndAskPrice()` from a registered pool.
4. Observe that `refAsk` is computed using `spread0 = 5 bps` instead of `spread1 = 500 bps`. The ask is `mid × 1.0005` instead of `mid × 1.05` — 99.5% tighter than intended.
5. A trader swapping against this pool buys the base token at `mid × 1.0005` instead of `mid × 1.05`, extracting ~4.95% of mid price per unit from the pool on every buy-side swap.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** smart-contracts-poc/contracts/interfaces/IPricedOracle.sol (L11-13)
```text
    function price(bytes32 feedId, address pool)
        external
        returns (uint256 mid, uint256 spread, uint16 spread1, uint256 refTime);
```

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L194-195)
```text
        (uint256 mid, uint256 spread, , uint256 refTime) =
            IPricedOracle(address(offchainOracle)).price(offchainFeedId, msg.sender);
```

**File:** smart-contracts-poc/contracts/ProtectedPriceProvider.sol (L182-183)
```text
        (uint256 mid, uint256 spread, , uint256 refTime) =
            IPricedOracle(address(offchainOracle)).price(offchainFeedId, msg.sender);
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L280-280)
```text
        (mid, spreadBps, , refTime) = IPricedOracle(address(offchainOracle)).price(feedId, msg.sender);
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L107-117)
```text
        if (compressed.s1 == 0xff && compressed.s0 == 0xff) {
            data.spread1 = BPS_BASE;
            data.spread0 = BPS_BASE;
            return data;
        }

        data.price = U64x32.decode(compressed.p);
        data.spread0 = _decodeCodebookIndex(compressed.s0);
        data.spread1 = _decodeCodebookIndex(compressed.s1);
        data.timestampMs = _layout.timestampMs;
    }
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L171-178)
```text
    function _price(bytes32 feedId)
        internal
        view
        returns (uint256 mid, uint256 spread, uint16 spread1, uint256 refTime)
    {
        OracleData memory data = getOracleData(feedId);
        return (uint256(data.price), uint256(data.spread0), data.spread1, data.timestampMs.toSeconds());
    }
```
