### Title
`toSeconds()` Floor-Division Truncation in `revertIfAfterBlockTimeWithDrift` Allows a Pusher to Store a Future-Dated Timestamp, Extending the Effective Staleness Window by Up to `MAX_TIME_DRIFT` Seconds — (File: `smart-contracts-poc/contracts/oracles/utils/TimeMs.sol`)

---

### Summary

`TimeMs.toSeconds()` performs floor division (`/ 1000`) when converting a millisecond timestamp to seconds. The future-timestamp guard `revertIfAfterBlockTimeWithDrift` calls `toSeconds()` before comparing against `block.timestamp + drift`. Because of the truncation, a pusher can store a timestamp up to `(block.timestamp + drift + 1) * 1000 − 1` ms — one full second beyond the intended ceiling. When that stored value is later read back through `toSeconds()`, it resolves to `block.timestamp_at_push + drift` seconds, making the price appear `drift` seconds fresher than it actually is. This silently extends the effective staleness window by `drift` seconds for every downstream price provider that relies on the returned `refTime`.

---

### Finding Description

**Root cause — `TimeMs.sol`** [1](#0-0) 

```
toSeconds(t)  =  floor(t_ms / 1000)

revertIfAfterBlockTimeWithDrift:
  require( floor(t_ms / 1000) <= block.timestamp + drift )
```

The maximum `t_ms` that passes the guard is therefore:

```
t_ms_max = (block.timestamp + drift + 1) * 1000 − 1
```

`toSeconds(t_ms_max)` = `block.timestamp + drift` — exactly `drift` seconds in the future.

**Storage path — `CompressedOracleV1.fallback()` and `updateBySignature()`**

Both push paths call `timestampMs.revertIfAfterBlockTimeWithDrift(MAX_TIME_DRIFT)` and then write the raw millisecond value to storage. [2](#0-1) 

The stored slot contains the raw millisecond timestamp. When the slot is later decoded, `_loadSlotLayout` extracts the 56-bit ms value and wraps it back into `TimeMs`. [3](#0-2) 

**Read path — `_price()` → `toSeconds()`** [4](#0-3) 

`refTime` returned to every price provider is `floor(stored_ms / 1000)`. If the stored ms was `(T_push + drift + 1)*1000 − 1`, the returned `refTime` is `T_push + drift`.

**Staleness check — `AnchoredPriceProvider._readLeg()`** [5](#0-4) 

The check is `(nowTs − refTime) > MAX_REF_STALENESS`. With `refTime = T_push + drift` and `nowTs = T_push + drift`, the difference is `0`, which is **not** greater than `MAX_REF_STALENESS` (even when `MAX_REF_STALENESS = 0`). The price passes as fresh.

The same staleness check exists in `PriceProvider`, `PriceProviderL2`, `ProtectedPriceProvider`, and `ProtectedPriceProviderL2`. [6](#0-5) 

---

### Impact Explanation

A feed creator (or any pusher they have delegated via `allowPushers`) can craft a slot word whose millisecond timestamp is `(block.timestamp + MAX_TIME_DRIFT + 1)*1000 − 1`. The push guard accepts it; the stored `refTime` (in seconds) is `block.timestamp + MAX_TIME_DRIFT`. At read time `T_read = T_push + MAX_TIME_DRIFT`, the staleness check passes even though the price data is `MAX_TIME_DRIFT` seconds old.

For `AnchoredPriceProvider` deployed with `MAX_REF_STALENESS = 0` (same-block freshness requirement, explicitly allowed by the constructor): [7](#0-6) 

…a price that is `MAX_TIME_DRIFT` seconds stale passes as current-block fresh. In a volatile market, `MAX_TIME_DRIFT` seconds of price drift (e.g., 5 s on a fast-moving asset) is enough for a trader to execute a swap at a stale bid/ask, extracting value from LPs — a direct loss of LP principal.

For providers with `MAX_TIME_DELTA > 0`, the effective staleness window silently widens from `maxDelta` to `maxDelta + MAX_TIME_DRIFT`, allowing correspondingly older prices to reach pool swaps.

---

### Likelihood Explanation

- **Trigger**: Any authorized pusher (creator or delegated EOA/contract) for a `CompressedOracleV1` feed can set the timestamp to the maximum allowed future value on every push — no special conditions required.
- **Configuration dependency**: Impact is proportional to `MAX_TIME_DRIFT`. A deployment with `MAX_TIME_DRIFT = 0` is unaffected; deployments with `MAX_TIME_DRIFT ≥ 1` are affected. The test suite explicitly demonstrates `MAX_TIME_DRIFT = 5` as a valid configuration. [8](#0-7) 

- **Semi-trusted actor**: Feed creators are trusted to push accurate prices, not to manipulate the staleness window. The protocol's own comment on `MAX_REF_STALENESS = 0` states the reference "must be in the current block" — a guarantee the truncation bug silently breaks.

---

### Recommendation

Replace the floor-division comparison in `revertIfAfterBlockTimeWithDrift` with a ceiling-division check so that any sub-second future overshoot is caught:

```solidity
// Before (floor — allows up to drift+1 seconds of future timestamp):
require(t0.toSeconds() <= block.timestamp + drift, FutureTimestamp());

// After (ceiling — enforces the intended drift bound in ms):
require(TimeMs.unwrap(t0) <= (block.timestamp + drift) * 1000, FutureTimestamp());
```

This compares the raw millisecond value against the ceiling of the allowed window, eliminating the truncation gap without changing the intended semantics.

---

### Proof of Concept

```
Setup:
  CompressedOracleV1 deployed with MAX_TIME_DRIFT = 5
  AnchoredPriceProvider deployed with MAX_REF_STALENESS = 0

Step 1 — Push at block.timestamp = T:
  Pusher crafts timestamp_ms = (T + 5 + 1) * 1000 − 1 = (T + 6) * 1000 − 1

Step 2 — Guard check (passes):
  toSeconds((T+6)*1000 − 1) = floor(((T+6)*1000 − 1) / 1000) = T + 5
  T + 5 <= T + 5  ✓  (no revert)

Step 3 — Stored refTime (in seconds):
  refTime = T + 5

Step 4 — Read at block.timestamp = T + 5:
  _isStale(T+5, T+5, 0):
    refTime == 0? No
    refTime > nowTs? No (T+5 == T+5)
    (T+5 − T+5) = 0 > 0? No  → NOT stale  ✓

Result:
  A price pushed 5 seconds ago passes the same-block freshness guard
  (MAX_REF_STALENESS = 0) and reaches the pool swap as a current-block quote.
  In a volatile market, the 5-second-old bid/ask can be exploited by a
  trader to extract value from LPs.
```

### Citations

**File:** smart-contracts-poc/contracts/oracles/utils/TimeMs.sol (L20-30)
```text
function toSeconds(TimeMs t) pure returns (uint56) {
    return TimeMs.unwrap(t) / 1000;
}

function isAfter(TimeMs t0, TimeMs t1) pure returns (bool) {
    return TimeMs.unwrap(t0) > TimeMs.unwrap(t1);
}

function revertIfAfterBlockTimeWithDrift(TimeMs t0, uint256 drift) view {
    require(t0.toSeconds() <= block.timestamp + drift, FutureTimestamp());
}
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L125-125)
```text
        _layout.timestampMs = toTimeMs(slotValue >> 8 & X56);
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L334-343)
```text
            TimeMs timestampMs = toTimeMs(word >> 8 & X56);
            timestampMs.revertIfAfterBlockTimeWithDrift(MAX_TIME_DRIFT);
            bytes32 key = bytes32(namespace | uint256(slotId));
            uint256 old = uint256(_loadStorage(key));
            TimeMs oldTimestampMs = toTimeMs(old >> 8 & X56);

            bool newer = timestampMs.isAfter(oldTimestampMs);
            if (!newer) continue;

            _writeStorage(key, bytes32(bytes32(word & ~uint256(0xff))));
```

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

**File:** smart-contracts-poc/test/oracles/compressed/CompressedOracle.t.sol (L218-226)
```text
    function testDriftAllowsBoundedFuture() public {
        CompressedOracleV1 drifty = new CompressedOracleV1(address(this), 5);

        uint56 within = uint56((block.timestamp + 5) * 1000);
        vm.prank(creator);
        (bool ok,) = address(drifty).call(_wordAt(0, 0, _packRaw(1_000_000, 3, 3), within));
        assertTrue(ok, "within-drift push should succeed");
        IOffchainOracle.OracleData memory data = drifty.getOracleData(drifty.feedIdOf(creator, 0, 0));
        assertEq(TimeMs.unwrap(data.timestampMs), uint256(within), "within-drift ts mismatch");
```
