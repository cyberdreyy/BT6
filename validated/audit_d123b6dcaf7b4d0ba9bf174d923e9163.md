### Title
Per-Slot Timestamp Masks Stale Per-Lane Price Data, Bypassing Provider Staleness Guard — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracleV1` packs four independent price feeds (positions 0–3) into a single 256-bit storage slot and stores **one shared 56-bit timestamp** for the entire slot. Any slot update overwrites all four lanes simultaneously. When a pusher refreshes one lane, the shared timestamp advances for all four lanes — including lanes whose price bytes were copied verbatim from stale storage. The staleness guard in every price provider (`PriceProvider`, `PriceProviderL2`, `AnchoredPriceProvider`, `ProtectedPriceProvider`) reads only `refTime` (the slot-level timestamp) and cannot distinguish a lane whose price was genuinely refreshed from one whose price bytes are stale. A stale price with a fresh timestamp passes every provider guard and reaches pool swaps.

---

### Finding Description

**Slot layout (CompressedOracle.sol, lines 119–131):**

```
bits 255…208 : oracle[0] (48 bits: 32-bit price + 8-bit s0 + 8-bit s1)
bits 207…160 : oracle[1]
bits 159…112 : oracle[2]
bits 111…64  : oracle[3]
bits  63…8   : timestamp (uint56, unix milliseconds) ← SHARED
bits   7…0   : reserved
```

The `fallback()` push path writes the entire 256-bit word atomically:

```solidity
_writeStorage(key, bytes32(bytes32(word & ~uint256(0xff))));
``` [1](#0-0) 

The documentation explicitly states: *"The entire slot is overwritten on update: if you update a single lane, you must still supply correct values for the other lanes."* [2](#0-1) 

`getOracleData` returns the slot-level timestamp for every position:

```solidity
data.timestampMs = _layout.timestampMs;   // slot-level, not per-lane
``` [3](#0-2) 

`price()` converts that shared timestamp to seconds and returns it as `refTime`:

```solidity
return (uint256(data.price), uint256(data.spread0), data.spread1, data.timestampMs.toSeconds());
``` [4](#0-3) 

Every provider's staleness guard checks only `refTime`:

```solidity
if (_isStale(refTime, block.timestamp, MAX_REF_STALENESS)) return (mid, spreadBps, refTime, false);
``` [5](#0-4) 

```solidity
if (_isStale(refTime, block.timestamp, MAX_TIME_DELTA)) { return (0, type(uint128).max); }
``` [6](#0-5) 

**The invariant break:** There is no per-lane timestamp. When a pusher constructs a slot word to update lane 1, they must embed values for lanes 0, 2, and 3. If those three lanes carry the same price bytes that were in storage before the update (copied from the previous slot word), those lanes now have:

- **Price**: the value from the previous push (potentially minutes or hours old in market time)
- **Timestamp**: the current block time (fresh)

The staleness guard sees a fresh `refTime` and passes. The price guard and spread guard see whatever bytes were in the old lane. The pool executes swaps at the stale price.

---

### Impact Explanation

A pool whose `IPriceProvider` reads from position 0 of a slot will receive a stale mid-price whenever a pusher updates position 1 (or 2 or 3) of the same slot and re-uses the old bytes for position 0. The stale bid/ask quote reaches `MetricOmmPool` swap execution. A trader who monitors the real market price can:

1. Observe that the on-chain quote lags the market (e.g., ETH price moved 2% but the pool still quotes the old price).
2. Execute a swap at the stale favorable price, extracting value from LP reserves.
3. Repeat on every slot update that touches a sibling lane.

This is a direct loss of LP principal — the pool pays out more than the oracle-anchored curve permits.

---

### Likelihood Explanation

The trigger is any slot update that touches fewer than all four lanes. This is the normal operating mode: a pusher running four independent feeds in one slot will routinely have fresh data for some feeds and not others at any given update interval. Off-chain infrastructure that batches updates per-slot (the design intent) will naturally re-use the last-known bytes for lanes it is not actively refreshing. No attacker action is required beyond monitoring the mempool for slot updates and front-running or back-running them.

A compromised or malicious pusher can also deliberately supply stale bytes for target lanes while advancing the timestamp, making the staleness window arbitrarily large.

---

### Recommendation

1. **Per-lane timestamps**: Extend the slot layout to store a 14-bit or 16-bit delta timestamp per lane (relative to a slot base), so each lane carries its own freshness indicator. Providers should read the per-lane timestamp, not the slot-level one.

2. **Alternatively, enforce full-slot freshness at the push layer**: Require that all four lanes in a pushed slot word carry a timestamp within a tight window of the current block time, rejecting words where any lane's price is demonstrably stale (requires an on-chain price-age registry per lane, which is expensive).

3. **Minimum viable fix**: Document that pools must not share a slot with feeds that have different update frequencies, and enforce at the `AnchoredProviderFactory` / `PriceProviderFactory` level that a feedId's slot contains only feeds belonging to the same pool or update cadence.

---

### Proof of Concept

```
Setup:
  - CompressedOracleV1 deployed, maxTimeDrift = 60s
  - Slot 5 holds:
      position 0 → ETH/USD feed (used by Pool A's AnchoredPriceProvider)
      position 1 → BTC/USD feed (used by Pool B)
  - At T=0: pusher pushes slot 5 with ETH=2000, BTC=60000, timestamp=T0

At T=300 (5 minutes later, ETH real price = 2060):
  - Pusher has fresh BTC data (BTC=61000) but no fresh ETH data.
  - Pusher constructs slot word:
      lane 0: ETH price bytes = same as T=0 (price=2000, stale)
      lane 1: BTC price bytes = fresh (price=61000)
      timestamp = T=300 (fresh)
  - Pusher calls fallback() with this word.
  - Storage is overwritten: ETH lane has price=2000, timestamp=T=300.

Pool A's AnchoredPriceProvider._readLeg():
  - oracle.price(ETH_feedId, pool) returns mid=2000, refTime=T=300
  - _isStale(T=300, block.timestamp=T=300, MAX_REF_STALENESS) → false (fresh!)
  - mid=2000 passes all guards
  - Pool A quotes ETH at 2000 while market is 2060

Attacker:
  - Buys ETH from Pool A at 2000 (pool sells ETH at stale low price)
  - Sells ETH on external market at 2060
  - Profit: 60 per ETH, extracted from Pool A's LP reserves
``` [7](#0-6) [8](#0-7) [9](#0-8)

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L116-116)
```text
        data.timestampMs = _layout.timestampMs;
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L119-131)
```text
    function _loadSlotLayout(bytes32 slotIndex) internal view returns (SlotLayout memory _layout) {
        uint256 slotValue;
        assembly ("memory-safe") {
            slotValue := sload(slotIndex)
        }

        _layout.timestampMs = toTimeMs(slotValue >> 8 & X56);

        _layout.oracle0 = _decodeCompressedOracleData(uint48((slotValue >> 208) & X48));
        _layout.oracle1 = _decodeCompressedOracleData(uint48((slotValue >> 160) & X48));
        _layout.oracle2 = _decodeCompressedOracleData(uint48((slotValue >> 112) & X48));
        _layout.oracle3 = _decodeCompressedOracleData(uint48((slotValue >> 64) & X48));
    }
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L177-177)
```text
        return (uint256(data.price), uint256(data.spread0), data.spread1, data.timestampMs.toSeconds());
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L326-344)
```text
        for (uint256 ptr = 0; ptr < end; ptr += 32) {
            uint256 word;
            assembly ("memory-safe") {
                word := calldataload(ptr)
            }
            // casting to 'uint8' is safe we want LSB
            // forge-lint: disable-next-line(unsafe-typecast)
            uint8 slotId = uint8(word);
            TimeMs timestampMs = toTimeMs(word >> 8 & X56);
            timestampMs.revertIfAfterBlockTimeWithDrift(MAX_TIME_DRIFT);
            bytes32 key = bytes32(namespace | uint256(slotId));
            uint256 old = uint256(_loadStorage(key));
            TimeMs oldTimestampMs = toTimeMs(old >> 8 & X56);

            bool newer = timestampMs.isAfter(oldTimestampMs);
            if (!newer) continue;

            _writeStorage(key, bytes32(bytes32(word & ~uint256(0xff))));
        }
```

**File:** smart-contracts-poc/contracts/oracles/compressed/docs/en/oracle-packet-structure.md (L22-23)
```markdown
- `slotId` is used to derive the storage key, but is **cleared before storing** (the stored slot always has `0x00` in the lowest byte).
- The entire slot is overwritten on update: if you update a single lane, you must still supply correct values for the other lanes.
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L277-295)
```text
    function _readLeg(bytes32 feedId)
        internal returns (uint256 mid, uint256 spreadBps, uint256 refTime, bool ok)
    {
        (mid, spreadBps, , refTime) = IPricedOracle(address(offchainOracle)).price(feedId, msg.sender);

        // Stale reference → not ok. Clamping to a stale anchor is the one false-safety case.
        if (_isStale(refTime, block.timestamp, MAX_REF_STALENESS)) return (mid, spreadBps, refTime, false);

        // Basic validity — mid positive, spreadBps not the stalled/off-hours marker (the Chainlink oracle
        // writes spreadBps = ORACLE_BPS when an RWA market is closed).
        if (mid == 0 || spreadBps >= ORACLE_BPS) return (mid, spreadBps, refTime, false);

        // Per-leg price guard.
        (uint128 guardMin, uint128 guardMax) = offchainOracle.priceGuard(feedId);
        guardMax = guardMax == 0 ? type(uint128).max : guardMax;
        if (mid < guardMin || mid > guardMax) return (mid, spreadBps, refTime, false);

        ok = true;
    }
```

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L198-200)
```text
        if (_isStale(refTime, block.timestamp, MAX_TIME_DELTA)) {
            return (0, type(uint128).max);
        }
```
