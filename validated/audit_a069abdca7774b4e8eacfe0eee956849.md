### Title
Slot-level timestamp shared across all 4 packed positions allows per-position price staleness to be silently masked — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracleV1` packs four independent price feeds into one 256-bit storage slot but stores only **one slot-level timestamp** for all four positions. Any push that updates even a single position refreshes the shared timestamp for the entire slot. `PriceProvider` and `AnchoredPriceProvider` read that shared timestamp as the freshness proof for whichever position they are bound to. When positions in the same slot are updated at different rates — a normal operational pattern — the staleness check for the slower-updated positions is silently bypassed, and a stale bid/ask quote reaches the pool.

---

### Finding Description

**Slot layout — one timestamp, four prices**

`CompressedOracleV1` stores each slot as a single 256-bit word:

```
bits 255…208 : oracle[0] (48 bits)
bits 207…160 : oracle[1] (48 bits)
bits 159…112 : oracle[2] (48 bits)
bits 111… 64 : oracle[3] (48 bits)
bits  63…  8 : timestamp (uint56, unix milliseconds)  ← ONE timestamp for all four
bits   7…  0 : reserved
``` [1](#0-0) 

The push paths (`fallback` and `updateBySignature`) overwrite the **entire** slot word atomically. The documentation is explicit:

> "The entire slot is overwritten on update: if you update a single lane, you must still supply correct values for the other lanes." [2](#0-1) 

**Read path returns the slot timestamp for every position**

`getOracleData` assigns the slot-level `timestampMs` to the returned `OracleData` regardless of which position is selected:

```solidity
data.timestampMs = _layout.timestampMs;
``` [3](#0-2) 

`price()` converts that to seconds and returns it as `refTime`:

```solidity
return (uint256(data.price), uint256(data.spread0), data.spread1, data.timestampMs.toSeconds());
``` [4](#0-3) 

**Staleness check in the providers uses that `refTime`**

`PriceProvider._getBidAndAskPrice` and `AnchoredPriceProvider._readLeg` both call `oracle.price(feedId, pool)` and then gate on:

```solidity
if (_isStale(refTime, block.timestamp, MAX_TIME_DELTA)) {
    return (0, type(uint128).max);
}
``` [5](#0-4) [6](#0-5) 

`refTime` is the slot-level timestamp — not the time the specific position's price data was last changed.

**The staleness bypass**

Suppose a creator places two feeds in the same slot:

| Position | Asset | Update cadence |
|---|---|---|
| 0 | ETH/USD | every 5 s |
| 1 | BTC/USD | every 60 s |

`PriceProvider` for the BTC/USD pool is configured with `MAX_TIME_DELTA = 10 s`.

At `t = 65 s` the pusher has a fresh ETH price but no new BTC price. They push a slot word with:
- `oracle[0]` = fresh ETH price
- `oracle[1]` = last known BTC price (from `t = 0`)
- `timestamp` = `65_000 ms`

The slot is stored. When the BTC/USD `PriceProvider` reads position 1:

```
refTime  = 65_000 ms / 1000 = 65 s
nowTs    = 65 s
delta    = 65 - 65 = 0  ≤  MAX_TIME_DELTA (10 s)  → NOT stale
```

The staleness check passes. The price returned is the 65-second-old BTC value. The pool quotes and executes swaps against it. [7](#0-6) 

---

### Impact Explanation

A pool whose `PriceProvider` is bound to a `CompressedOracleV1` position that shares a slot with a more-frequently-updated position will receive stale bid/ask quotes during swaps. The staleness window is unbounded above `MAX_TIME_DELTA` — it grows with the difference in update cadences between positions in the same slot. A trader can exploit the stale quote to receive more output tokens than the current market price permits, draining the pool's reserves at the expense of LPs.

This matches the allowed impact: **bad-price execution — stale bid/ask quote reaches a pool swap**.

---

### Likelihood Explanation

Packing multiple feeds into one slot is the primary efficiency motivation of `CompressedOracleV1` (256 slots × 4 positions per creator). Feeds for different assets naturally have different update rates. A legitimate pusher who updates one position more frequently than another will inadvertently refresh the shared timestamp for all positions in the slot on every push. No malicious intent is required; the bypass is a structural consequence of the shared-timestamp design under normal operation.

---

### Recommendation

Replace the single slot-level timestamp with **per-position timestamps**. Each 48-bit lane currently encodes `p (32 bits) | s0 (8 bits) | s1 (8 bits)`. One option is to reduce price precision slightly and embed a per-lane timestamp delta, or to use a second storage slot for timestamps. Alternatively, enforce that all four positions in a slot must be updated together (same-cadence constraint), and document this as a hard invariant that the off-chain pusher infrastructure must respect.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import "forge-std/Test.sol";
import {CompressedOracleV1} from "contracts/oracles/compressed/CompressedOracle.sol";
import {IOffchainOracle} from "contracts/interfaces/IOffchainOracle.sol";
import {U64x32} from "contracts/oracles/utils/U64x32.sol";
import {TimeMs} from "contracts/oracles/utils/TimeMs.sol";

contract StalenessPoC is Test {
    CompressedOracleV1 oracle;
    address creator;
    uint256 constant CREATOR_KEY = 0xC0FFEE;

    // Simulate PriceProvider's staleness check
    uint256 constant MAX_TIME_DELTA = 10; // 10 seconds

    function _isStale(uint256 refTime, uint256 nowTs, uint256 maxDelta) internal pure returns (bool) {
        if (refTime == 0) return true;
        if (refTime > nowTs) return true;
        return (nowTs - refTime) > maxDelta;
    }

    function setUp() public {
        oracle = new CompressedOracleV1(address(this), 0);
        creator = vm.addr(CREATOR_KEY);
        vm.warp(1_700_000_000);
    }

    // Helper: pack a 32-byte slot word
    function _slotWord(uint8 slotId, uint32 p0, uint32 p1, uint56 tsMs) internal pure returns (bytes memory) {
        // oracle[0]=p0|0|0, oracle[1]=p1|0|0, oracle[2]=0, oracle[3]=0, ts, slotId
        uint256 word = (uint256(p0) << (208 + 16))
                     | (uint256(p1) << (160 + 16))
                     | (uint256(tsMs) << 8)
                     | slotId;
        return abi.encodePacked(word);
    }

    function testStalenessbypassViaSharedTimestamp() public {
        uint8 slotId = 0;

        // t=0: push both ETH (pos0) and BTC (pos1) with fresh prices
        uint56 t0Ms = uint56(block.timestamp * 1000);
        uint32 ethPrice0 = U64x32.encode(3000_00000000); // $3000
        uint32 btcPrice0 = U64x32.encode(50000_00000000); // $50000

        vm.prank(creator);
        (bool ok,) = address(oracle).call(_slotWord(slotId, ethPrice0, btcPrice0, t0Ms));
        assertTrue(ok);

        // Advance 65 seconds — BTC price should now be stale (MAX_TIME_DELTA = 10s)
        vm.warp(block.timestamp + 65);

        // t=65: pusher updates ETH (pos0) with fresh price, but BTC (pos1) unchanged
        uint56 t65Ms = uint56(block.timestamp * 1000);
        uint32 ethPrice65 = U64x32.encode(3100_00000000); // $3100 (fresh)
        // btcPrice0 is still $50000 — 65 seconds old, but pusher has no new data

        vm.prank(creator);
        (ok,) = address(oracle).call(_slotWord(slotId, ethPrice65, btcPrice0, t65Ms));
        assertTrue(ok);

        // Read BTC/USD (position 1)
        bytes32 btcFeedId = oracle.feedIdOf(creator, slotId, 1);
        IOffchainOracle.OracleData memory data = oracle.getOracleData(btcFeedId);

        uint256 refTime = TimeMs.unwrap(data.timestampMs) / 1000;

        // refTime = 65 (slot timestamp, refreshed by ETH update)
        // nowTs   = 65
        // delta   = 0 ≤ MAX_TIME_DELTA (10) → NOT stale
        bool stale = _isStale(refTime, block.timestamp, MAX_TIME_DELTA);

        // BUG: stale == false, but BTC price is 65 seconds old
        assertFalse(stale, "BTC price incorrectly passes staleness check");
        // The price returned is the 65-second-old $50000, not the current market price
        assertEq(data.price, U64x32.decode(btcPrice0));
    }
}
```

The test demonstrates that after 65 seconds, the BTC/USD position passes the `MAX_TIME_DELTA = 10 s` staleness check because the slot timestamp was refreshed by the ETH/USD update, even though the BTC price data is 65 seconds old. A `PriceProvider` or `AnchoredPriceProvider` bound to this feed will deliver the stale $50,000 BTC price to the pool swap.

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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L326-343)
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
```

**File:** smart-contracts-poc/contracts/oracles/compressed/docs/en/oracle-packet-structure.md (L23-23)
```markdown
- The entire slot is overwritten on update: if you update a single lane, you must still supply correct values for the other lanes.
```

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L197-199)
```text
        // 2. Staleness check
        if (_isStale(refTime, block.timestamp, MAX_TIME_DELTA)) {
            return (0, type(uint128).max);
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L282-283)
```text
        // Stale reference → not ok. Clamping to a stale anchor is the one false-safety case.
        if (_isStale(refTime, block.timestamp, MAX_REF_STALENESS)) return (mid, spreadBps, refTime, false);
```
