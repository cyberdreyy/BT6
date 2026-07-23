### Title
Shared Slot Timestamp in `CompressedOracleV1` Allows a Never-Pushed Lane to Appear Non-Stale, Breaking the Documented Safety Invariant — (File: `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracleV1` packs four oracle feeds into a single 256-bit storage slot with **one shared 56-bit timestamp** for all four lanes. The contract's own documentation and code comments assert: *"A never-pushed position reads as price 0 / timestamp 0, which every consumer already rejects as stale."* This invariant is false whenever any sibling lane in the same slot has been pushed. A push to lane 1 updates the slot's shared timestamp, causing lane 0 (never written, price = 0) to surface a **fresh, non-stale timestamp** to every consumer. A malicious delegated pusher, or an operational push-system bug, can exploit this to deliver `price = 0` with a live timestamp to a `PriceProvider`, DoS-ing all swaps on any pool that reads that feed.

---

### Finding Description

**Slot layout** — each 256-bit storage word holds:

```
bits 255…208 : oracle[0] (48 bits: p=32, s0=8, s1=8)
bits 207…160 : oracle[1] (48 bits)
bits 159…112 : oracle[2] (48 bits)
bits 111… 64 : oracle[3] (48 bits)
bits  63…  8 : timestamp (uint56, unix milliseconds) ← SHARED
bits   7…  0 : reserved / slotId in calldata
``` [1](#0-0) 

Every push via `fallback()` or `updateBySignature()` **overwrites the entire slot word**, including the shared timestamp. The monotonicity guard only checks that the incoming timestamp is strictly newer than the stored one; it does not validate that every lane in the word carries a non-zero price. [2](#0-1) 

`getOracleData` then returns `data.timestampMs = _layout.timestampMs` — the **slot-level** timestamp — for every lane, including lanes whose 48-bit price field is all-zero: [3](#0-2) 

A never-pushed lane has `p = 0, s0 = 0, s1 = 0`. Because `s0 != 0xff || s1 != 0xff`, the early-return sentinel branch is **not** taken, so the function falls through and sets `data.timestampMs` to the shared slot timestamp — which is fresh if any sibling lane was recently pushed. The result is `price = 0` paired with a live, non-stale timestamp.

The test suite explicitly acknowledges this behaviour but does not treat it as a defect: [4](#0-3) 

The documentation repeats the false safety claim verbatim: [5](#0-4) 

---

### Impact Explanation

Any `PriceProvider` (or `AnchoredPriceProvider`) that reads a lane whose 48-bit price field is zero but whose slot timestamp is fresh will receive `price = 0` with a staleness check that **passes**. Two downstream outcomes:

1. **Direct DoS on swaps** — the pool calls `getBidAndAskPrice()` on the provider; a zero mid-price produces `bid = 0`. The pool reverts with `BidIsZero`, making every swap on that pool permanently unusable until the creator pushes a corrective update. [6](#0-5) 

2. **AnchoredPriceProvider deviation breach** — if the pool uses an anchored provider, the 100 % deviation between `price = 0` and the Chainlink/Pyth anchor triggers the deviation guard, causing the provider to revert. The pool surfaces `PriceProviderFailed`, again DoS-ing all swaps. [7](#0-6) 

In both cases the pool's swap path is rendered unusable — a broken core functionality impact within the allowed scope.

---

### Likelihood Explanation

Two realistic trigger paths exist:

**Path A — Operational error (no attacker required).** A creator operates multiple feeds packed into the same slot. Their off-chain push system has a bug and emits a slot word where the lane used by the pool's `PriceProvider` is left as zero bytes while the other lanes carry valid data. The shared timestamp is updated; the zero-price lane now appears fresh. No adversary is needed.

**Path B — Malicious delegated pusher (semi-trusted).** `allowPushers` lets the creator delegate push rights to third-party addresses. A delegated pusher is semi-trusted: they are authorised to write data but are not the creator. A malicious pusher submits a slot word with the target lane zeroed out and a timestamp strictly greater than the current stored value. The monotonicity check passes; the slot is overwritten; the target lane now reads `price = 0` with a fresh timestamp. [8](#0-7) 

Both paths are reachable without privileged factory-owner or oracle-admin actions.

---

### Recommendation

1. **Per-lane timestamp tracking** — store a separate timestamp per 48-bit lane rather than one per slot. This eliminates the cross-lane timestamp leakage entirely, at the cost of additional storage.

2. **Zero-price guard in `getOracleData`** — if `compressed.p == 0` (and the sentinel `0xff/0xff` branch was not taken), treat the lane as unpushed and return `data.timestampMs = TimeMs.wrap(0)`. This restores the documented invariant without changing the storage layout.

3. **Zero-price guard in `PriceProvider`** — independently of the oracle fix, the provider should explicitly reject `price = 0` before computing bid/ask, reverting with a descriptive error rather than propagating a zero price downstream.

4. **Documentation correction** — the comment *"A never-pushed position reads as price 0 / timestamp 0"* must be qualified: this is only true for slots that have **never** been pushed. Once any lane in a slot is pushed, all sibling lanes inherit the fresh timestamp.

---

### Proof of Concept

```
Setup
─────
• CompressedOracleV1 deployed; creator = 0xALICE.
• PriceProvider configured to read feedIdOf(0xALICE, slotId=5, positionIndex=0).
• Pool P uses that PriceProvider.

Step 1 — Legitimate push (all four lanes valid)
────────────────────────────────────────────────
Alice pushes slot 5 with:
  lane[0] = packRaw(price=1_000_000, s0=3, s1=3)   ← used by PriceProvider
  lane[1] = packRaw(price=2_000_000, s0=4, s1=4)
  lane[2] = packRaw(price=3_000_000, s0=5, s1=5)
  lane[3] = packRaw(price=4_000_000, s0=6, s1=6)
  timestamp = T1
Pool P swaps work normally.

Step 2 — Malicious / buggy push (lane[0] zeroed)
─────────────────────────────────────────────────
Delegated pusher (or Alice's buggy push system) submits slot 5 with:
  lane[0] = 0x000000000000   ← price=0, s0=0, s1=0
  lane[1] = packRaw(price=2_100_000, s0=4, s1=4)
  lane[2] = packRaw(price=3_100_000, s0=5, s1=5)
  lane[3] = packRaw(price=4_100_000, s0=6, s1=6)
  timestamp = T2 > T1        ← monotonicity check passes

Slot 5 is overwritten. Stored slot timestamp = T2.

Step 3 — PriceProvider reads lane[0]
──────────────────────────────────────
getOracleData(feedIdOf(0xALICE, 5, 0)) returns:
  data.price       = U64x32.decode(0) = 0
  data.spread0     = codebook[0]      (non-zero, valid)
  data.spread1     = codebook[0]      (non-zero, valid)
  data.timestampMs = T2               ← FRESH, staleness check passes

Step 4 — Pool swap reverts
───────────────────────────
Pool calls PriceProvider.getBidAndAskPrice().
Provider computes bid = 0 * (1 − spread) = 0.
Pool reverts: BidIsZero.
All swaps on pool P are DoS'd until Alice pushes a corrective update.
``` [9](#0-8) [10](#0-9)

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L1-27)
```text
// SPDX-License-Identifier: BUSL-1.1
pragma solidity ^0.8.28;

import {ECDSA} from "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import {MessageHashUtils} from "@openzeppelin/contracts/utils/cryptography/MessageHashUtils.sol";
import {Codebook256} from "../utils/Codebook256.sol";
import {U64x32} from "../utils/U64x32.sol";

import {OracleBase} from "./OracleBase.sol";

import {TimeMs, toTimeMs} from "../utils/TimeMs.sol";
import {ICompressedOracleV1} from "../../interfaces/ICompressedOracleV1.sol";

/// @notice Registrationless compressed oracle: a feed's LOCATION is its identity.
///         There is no feed registry — the feedId packs (creator, chainid, slotIndex,
///         positionIndex) and every read decodes its coordinates straight from the id:
///
///           feedId = creator << 96 | block.chainid << 16 | slotIndex << 8 | positionIndex
///
///         A creator owns 256 slots × 4 positions in their own namespace and simply
///         pushes into them (directly, or through pushers delegated via `allowPushers`).
///         A never-pushed position reads as price 0 / timestamp 0, which every consumer
///         already rejects as stale — no seeding or creation step is needed.
contract CompressedOracleV1 is OracleBase, ICompressedOracleV1 {
    /// @notice Oracle family discriminator for off-chain introspection (matches the
    ///         pusher/console `kind` vocabulary).
    string public constant kind = "compressed";
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L88-117)
```text
    function getCompressedOracle(bytes32 feedId)
        external
        view
        override
        returns (CompressedOracleData memory data, TimeMs timestamp)
    {
        (address creator, uint8 slotIndex, uint8 positionIndex) = _unpackFeedId(feedId);
        SlotLayout memory _layout = _loadSlotLayout(_oracleSlot(creator, slotIndex));

        data = _selectCompressedData(_layout, positionIndex);
        timestamp = _layout.timestampMs;
    }

    function getOracleData(bytes32 feedId) public view override returns (OracleData memory data) {
        (address creator, uint8 slotIndex, uint8 positionIndex) = _unpackFeedId(feedId);

        SlotLayout memory _layout = _loadSlotLayout(_oracleSlot(creator, slotIndex));
        CompressedOracleData memory compressed = _selectCompressedData(_layout, positionIndex);

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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L305-330)
```text
    /// @notice Push path. Calldata = N × 32-byte slot words:
    ///         [data0:6][data1:6][data2:6][data3:6][ts:7][slotId:1]
    ///         The sender pushes into `namespaceRemapping[msg.sender]`, falling back to
    ///         its OWN namespace — a creator needs zero setup transactions to start
    ///         pushing. Each word carries its own timestamp (monotonicity is the only
    ///         freshness gate), so there is no deadline prefix.
    fallback() override external {
        uint256 end;
        uint256 namespace;

        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;

        assembly ("memory-safe") {
            end := calldatasize()
            namespace := shl(96, creator) // [creator:20][zeros:12]
        }

        // 4 * 6 + 7 + 1 = 32 bytes per slot
        if (end == 0 || end % 32 != 0) revert BadCalldataLength();

        for (uint256 ptr = 0; ptr < end; ptr += 32) {
            uint256 word;
            assembly ("memory-safe") {
                word := calldataload(ptr)
            }
```

**File:** smart-contracts-poc/test/oracles/compressed/CompressedOracleRegistrationless.t.sol (L113-120)
```text
        // A sibling lane in the SAME slot shares the slot's 56-bit timestamp word, but its own
        // 48-bit price lane was never written → price 0 (the timestamp is per-slot, not per-lane).
        IOffchainOracle.OracleData memory sibling = oracle.getOracleData(oracle.feedIdOf(creator, 8, 0));
        assertEq(sibling.price, 0, "never-pushed lane price should be zero");

        // A feed in a DIFFERENT, never-pushed slot reads all-zero (ts 0 ⇒ stale to consumers).
        IOffchainOracle.OracleData memory untouched = oracle.getOracleData(oracle.feedIdOf(creator, 9, 0));
        assertEq(untouched.price, 0, "never-pushed price should be zero");
```

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L130-132)
```text
  /// @notice Bid price was zero, so mid/spread cannot be formed.
  /// @dev On `swap`, from the live provider; on `simulateSwapAndRevert`, from `bidPriceX64` you passed.
  error BidIsZero();
```

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L134-136)
```text
  /// @notice External call to the active `IPriceProvider` reverted or bubbled a failure.
  /// @param reason Opaque revert data from the provider (decode off-chain if the provider documents errors).
  error PriceProviderFailed(bytes reason);
```

**File:** smart-contracts-poc/contracts/interfaces/ICompressedOracleV1.sol (L14-20)
```text
    struct SlotLayout {
        CompressedOracleData oracle0;
        CompressedOracleData oracle1;
        CompressedOracleData oracle2;
        CompressedOracleData oracle3;
        TimeMs timestampMs; // unix timestamp in milliseconds
    } // 48*4 + 64 = 248
```
