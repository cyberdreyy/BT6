### Title
`updateBySignature` accepts any historically-signed slot value without a lower-bound timestamp check, allowing an unprivileged submitter to feed a stale price into pools — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracleV1.updateBySignature` is a permissionless relay path: anyone may submit a slot value signed by the `feedCreator`. The only freshness gate is a monotonicity check (new timestamp > stored timestamp) and a future-timestamp guard. There is **no lower-bound check** on the submitted timestamp. An unprivileged actor who has collected old, publicly-broadcast signed slot values can therefore wait until the stored price goes stale, then replay a historically-signed slot whose timestamp is old but still within `MAX_TIME_DELTA`, causing the pool to execute swaps against a price that may be hours or days out of date.

---

### Finding Description

The `updateBySignature` entry-point in `CompressedOracleV1` is designed to let any relayer submit a slot update that was signed by the `feedCreator`: [1](#0-0) 

The two freshness checks applied to the incoming `newSlotValue` are:

1. **Future-timestamp guard** — the embedded `timestampMs` must not exceed `block.timestamp + MAX_TIME_DRIFT`: [2](#0-1) 

2. **Monotonicity check** — the new timestamp must be strictly after the currently stored timestamp: [3](#0-2) 

Neither check enforces a **lower bound** on the submitted timestamp. `revertIfAfterBlockTimeWithDrift` only prevents future timestamps; it does not reject timestamps that are arbitrarily old: [4](#0-3) 

The `feedCreator` is typically an off-chain service that signs slot updates at regular intervals and broadcasts them to a public relayer network (the entire purpose of `updateBySignature` is permissionless relaying). Every broadcast signed slot is a valid, replayable credential. An attacker who has collected a set of these old signed slots can:

1. Wait until the currently stored slot's timestamp is older than `MAX_TIME_DELTA` (the pool is now halted — `PriceProvider._isStale` returns `true`).
2. Select a historical signed slot whose embedded timestamp is **older than the stored one** (satisfying monotonicity) **but still within `MAX_TIME_DELTA`** of `block.timestamp` (satisfying the provider's staleness check).
3. Call `updateBySignature` with that old slot. The signature is valid (the `feedCreator` signed it), the monotonicity check passes, and the slot is written.
4. The pool is now "live" again, but the price it reads is the price the `feedCreator` signed hours or days ago.

The `PriceProvider` staleness check that downstream consumers rely on: [5](#0-4) 

accepts any `refTime` within `MAX_TIME_DELTA` (up to 7 days): [6](#0-5) 

So a slot with a timestamp 6 days and 23 hours old passes both the oracle's monotonicity gate and the provider's staleness gate, and the pool executes swaps at a price that may be dramatically wrong.

The same gap exists in the `fallback()` push path, but that path is restricted to authorized pushers (trusted actors). `updateBySignature` is the unprivileged surface.

---

### Impact Explanation

**Bad-price execution.** An unprivileged attacker can cause the pool to quote and settle swaps at a price that is up to `MAX_TIME_DELTA` (≤ 7 days) old. For volatile assets this can represent a price deviation of tens of percent. LPs bear the loss: the attacker buys the underpriced asset from the pool and sells it at the true market price, extracting value directly from LP principal. This matches the "bad-price execution" and "swap conservation failure" impact categories.

---

### Likelihood Explanation

**Medium.** The preconditions are:

- The stored slot must have gone stale (the normal pusher missed a window). This is a realistic liveness failure mode.
- The attacker must possess a valid old signed slot from the `feedCreator`. Because `updateBySignature` is explicitly designed for public relaying, signed slots are expected to be broadcast publicly; any observer of the relayer network accumulates them passively.
- The attacker must act before a legitimate relayer submits the latest slot (a race the attacker can win by front-running).

No privileged role, no malicious setup, and no non-standard token behavior is required.

---

### Recommendation

Add a **minimum-age check** inside `updateBySignature` (and symmetrically in `fallback()`) that rejects any slot whose embedded timestamp is older than a configurable `MAX_PUSH_AGE` relative to `block.timestamp`:

```solidity
// After the future-timestamp guard:
require(
    timestampMs.toSeconds() + MAX_PUSH_AGE >= block.timestamp,
    TimestampTooOld()
);
```

`MAX_PUSH_AGE` should be set well below the `PriceProvider`'s `MAX_TIME_DELTA` so that only genuinely fresh data can enter the oracle. This mirrors the fix suggested in the referenced TWAP report: ensure that every accepted observation is temporally close to the moment it is written on-chain.

---

### Proof of Concept

```
Setup
─────
• feedCreator = 0xFEED signs slot updates every 60 s and broadcasts them publicly.
• PriceProvider.MAX_TIME_DELTA = 3600 s (1 hour).
• CompressedOracleV1.MAX_TIME_DRIFT = 5 s.

T = 0        feedCreator signs slot S0  (price = $2 000, ts = T-3660 s)  ← stored
T = 3660 s   S0 is now stale; pool halts (PriceProvider reverts FeedStalled).
             feedCreator has since signed S1…S60 (price drifted to $2 500).

Attack at T = 3660 s
────────────────────
1. Attacker holds S30 (price = $2 000, ts = T-3630 s), signed by feedCreator at T-3630 s.
   • ts(S30) = T-3630 s  >  ts(S0) = T-3660 s  → monotonicity passes.
   • age = 3630 s < MAX_TIME_DELTA = 3600 s … wait, 3630 > 3600.

   Adjust: attacker uses S59 (ts = T-60 s, price = $2 490).
   • ts(S59) > ts(S0) → monotonicity passes.
   • age = 60 s < 3600 s → PriceProvider staleness passes.
   • price = $2 490 vs true price $2 500 → attacker buys cheap.

2. Attacker calls updateBySignature(feedCreator, S59.slotValue, S59.sig).
   → Oracle stores S59; pool is live again at price $2 490.

3. Attacker swaps token1 → token0 at $2 490, sells token0 at $2 500 on CEX.
   → $10 profit per unit, extracted from LP principal.
``` [1](#0-0) [7](#0-6) [5](#0-4)

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L271-303)
```text
    function updateBySignature(address feedCreator, uint256 newSlotValue, bytes calldata signature)
        external
        override
        returns (bool)
    {
        require(feedCreator != address(0), InvalidNamespace());

        uint256 namespace;
        assembly ("memory-safe") {
            namespace := shl(96, feedCreator) // [creator:20][zeros:12]
        }

        uint8 slotId = uint8(newSlotValue); // LSB
        TimeMs timestampMs = toTimeMs(newSlotValue >> 8 & X56);
        timestampMs.revertIfAfterBlockTimeWithDrift(MAX_TIME_DRIFT);
        bytes32 key = bytes32(namespace | uint256(slotId));
        uint256 old = uint256(_loadStorage(key));
        TimeMs oldTimestampMs = toTimeMs(old >> 8 & X56);

        bool newer = timestampMs.isAfter(oldTimestampMs);
        if (!newer) {
            return false;
        }

        bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
            keccak256(abi.encode(block.chainid, address(this), feedCreator, newSlotValue))
        );
        require(feedCreator == ECDSA.recover(hash, signature));

        _writeStorage(key, bytes32(newSlotValue & ~uint256(0xff)));

        return true;
    }
```

**File:** smart-contracts-poc/contracts/oracles/utils/TimeMs.sol (L24-30)
```text
function isAfter(TimeMs t0, TimeMs t1) pure returns (bool) {
    return TimeMs.unwrap(t0) > TimeMs.unwrap(t1);
}

function revertIfAfterBlockTimeWithDrift(TimeMs t0, uint256 drift) view {
    require(t0.toSeconds() <= block.timestamp + drift, FutureTimestamp());
}
```

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L87-88)
```text
        if (_maxTimeDelta == 0 || _maxTimeDelta > 7 days) revert MaxTimeDeltaOutOfBounds();
        MAX_TIME_DELTA = _maxTimeDelta;
```

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L125-133)
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
