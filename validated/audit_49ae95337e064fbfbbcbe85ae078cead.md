### Title
Intra-block price update sandwich allows risk-free arbitrage against LPs via permissionless Pyth Lazer push — (`smart-contracts-poc/contracts/oracles/utils/LazerConsumer.sol`)

---

### Summary

The Pyth Lazer oracle path (`LazerConsumer._verifyAndStore`) accepts price updates from **any caller** holding a valid Lazer-signed payload. Because the `PriceProvider` reads the oracle price fresh on every `getBidAndAsk` call with no block-level price lock, an attacker can: (1) swap at the currently stored stale price, (2) push a newer signed payload that advances the stored price, and (3) swap in the opposite direction at the new price — all within the same block or transaction — extracting the full price delta from LP reserves with zero market risk.

---

### Finding Description

`LazerConsumer._verifyAndStore` is explicitly designed as a **registrationless** push path: the Lazer network's ECDSA signature is the sole trust anchor, and any caller who possesses a valid signed payload may submit it on-chain at any time: [1](#0-0) 

The monotonicity guard only prevents *older* timestamps from overwriting *newer* ones; it does not prevent a *newer* timestamp from overwriting the current stored price mid-block: [2](#0-1) 

On the consumer side, `PriceProvider._getBidAndAskPrice` reads the oracle price live on every invocation with no block-number or block-timestamp snapshot: [3](#0-2) 

The staleness check (`_isStale`) only rejects prices that are **too old** relative to `block.timestamp`; it does not detect or reject a price that is **newer than the price used earlier in the same block**: [4](#0-3) 

There is no block-level price cache anywhere in the pool or provider stack. The pool's transient reentrancy guard (`MetricReentrancyGuardTransient`) only prevents re-entrant calls *into the same pool*; it does not prevent a router or multicall contract from sequencing two separate pool swaps with an oracle push in between.

---

### Impact Explanation

LPs suffer a direct, quantifiable loss of principal equal to `Δprice × swap_amount − fees` per exploit. The attacker extracts the full price-move delta from the pool's reserves. Because the attack is atomic (single transaction via a helper contract) and requires no capital at risk beyond gas, it is equivalent to a guaranteed drain of LP value whenever the oracle price moves between two Lazer-signed payloads that the attacker holds simultaneously. This meets the **Critical/High direct loss of LP assets** threshold.

---

### Likelihood Explanation

Pyth Lazer signed payloads are publicly available to any subscriber of the Lazer WebSocket feed. An attacker needs only to:
- Subscribe to the Lazer feed and buffer the last two signed payloads for a feed.
- Detect a price move between the buffered payload and the current on-chain stored price.
- Execute the sandwich atomically.

No privileged role, no malicious setup, and no non-standard token behavior is required. The attack is fully executable by any EOA or contract with Lazer feed access. Likelihood is **Medium-High** given the public availability of Lazer payloads and the straightforward atomicity of the exploit.

---

### Recommendation

Implement a **block-level price snapshot** in `PriceProvider` (or in `OracleBase`/`CompressedOracleV1`): on the first `getBidAndAsk` call in a given block, cache the returned `(bid, ask, refTime)` keyed by `block.number`. All subsequent calls within the same block return the cached value, preventing any intra-block oracle update from changing the effective price. This is the direct analog of the recommendation in the external M-10 report.

Alternatively, enforce that the `refTime` of the price used for a swap equals the `refTime` of the price stored at the start of the block (i.e., reject any oracle update whose `timestampMs` is strictly greater than the `timestampMs` read at the first swap of the block).

---

### Proof of Concept

**Setup:**
- Pool: WBTC/USDC, oracle backed by Pyth Lazer.
- At off-chain time `T`: Lazer publishes signed payload P1 with WBTC = $50,000. This is the currently stored on-chain price.
- At off-chain time `T+10s`: Lazer publishes signed payload P2 with WBTC = $51,000. Attacker buffers P2 but does **not** submit it.

**Attack (single atomic transaction via helper contract):**

1. **Step 1 — Swap at old price:** Call `pool.swap(...)` to buy 1 WBTC for 50,000 USDC. `PriceProvider._getBidAndAskPrice` reads the stored price (P1 = $50,000). Swap executes at $50,000.

2. **Step 2 — Push new price:** Submit payload P2 to the Lazer oracle fallback. `_verifyAndStore` validates the Lazer signature, finds `ts(P2) > ts(P1)`, and overwrites the stored price to $51,000. [5](#0-4) 

3. **Step 3 — Swap at new price:** Call `pool.swap(...)` to sell 1 WBTC for 51,000 USDC. `PriceProvider._getBidAndAskPrice` now reads the updated price (P2 = $51,000). Swap executes at $51,000.

**Result:** Attacker nets 1,000 USDC minus fees. LPs bear the full loss. The attack is profitable whenever `Δprice × amount > total_fees`. At a 2% fee tier, any price move above 2% is exploitable.

### Citations

**File:** smart-contracts-poc/contracts/oracles/utils/LazerConsumer.sol (L131-138)
```text
    // Registrationless: every feed id in the VERIFIED payload is stored — the Lazer
    // signature (checked in _verifyPayload) is the trust anchor, not a registry.
    function _verifyAndStore(
        mapping(bytes32 => IOffchainOracle.OracleData) storage __data,
        uint32[] memory feedIds,
        bytes memory priceUpdate
    ) internal {
        (uint256[] memory raw, uint256 pos, uint256 payloadLen) = _verifyPayload(feedIds, priceUpdate);
```

**File:** smart-contracts-poc/contracts/oracles/utils/LazerConsumer.sol (L162-171)
```text
                ts.revertIfAfterBlockTimeWithDrift(MAX_TIME_DRIFT);

                if (ts.isAfter(__data[feedId].timestampMs)) {
                    __data[feedId] = IOffchainOracle.OracleData({
                        price: normPrice,
                        spread0: spreadU.toUint16(),
                        spread1: 0xFFFF,
                        timestampMs: ts
                    });
                }
```

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L191-200)
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
```
