### Title
Slot-Level Timestamp Shared Across All 4 Positions Allows Stale Per-Position Prices to Bypass Staleness Checks — (File: `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracleV1` packs 4 oracle price lanes and a **single shared 56-bit timestamp** into one 256-bit storage slot. When a pusher writes a new slot word — even to update only one position — the slot-level timestamp is refreshed for **all four positions simultaneously**. Price providers (`AnchoredPriceProvider`, `ProtectedPriceProvider`, `PriceProviderL2`) consume that shared timestamp as the `refTime` staleness indicator for each individual position. A semi-trusted pusher (or an accidentally partial updater) can therefore re-publish old prices for positions 1–3 under a fresh timestamp, causing those stale prices to pass the staleness gate and reach live pool swaps.

---

### Finding Description

**Slot layout — one timestamp for four lanes**

The 256-bit storage word is structured as:

```
bits 255…208 : oracle[0] (48 bits: 32-bit U64x32 price + 8-bit s0 + 8-bit s1)
bits 207…160 : oracle[1] (48 bits)
bits 159…112 : oracle[2] (48 bits)
bits 111… 64 : oracle[3] (48 bits)
bits  63…  8 : timestamp (uint56, unix milliseconds)  ← SHARED
bits   7…  0 : slotId / reserved
``` [1](#0-0) 

**`getOracleData` assigns the slot-level timestamp to every position**

```solidity
data.timestampMs = _layout.timestampMs;   // line 116 — same for all four positions
``` [2](#0-1) 

**`_price` surfaces that timestamp as `refTime`**

```solidity
return (uint256(data.price), uint256(data.spread0), data.spread1, data.timestampMs.toSeconds());
``` [3](#0-2) 

**`AnchoredPriceProvider._readLeg` trusts `refTime` as the per-feed freshness indicator**

```solidity
(mid, spreadBps, , refTime) = IPricedOracle(address(offchainOracle)).price(feedId, msg.sender);
if (_isStale(refTime, block.timestamp, MAX_REF_STALENESS)) return (mid, spreadBps, refTime, false);
``` [4](#0-3) 

**The `fallback` push path overwrites the entire slot word atomically**

The pusher supplies a complete 32-byte word containing all four lanes plus the timestamp. The only gate is monotonicity on the slot-level timestamp — there is no per-lane freshness check:

```solidity
bool newer = timestampMs.isAfter(oldTimestampMs);
if (!newer) continue;
_writeStorage(key, bytes32(bytes32(word & ~uint256(0xff))));
``` [5](#0-4) 

**The mismatch**

When a pusher wants to update only position 0 (e.g., because only that data source has a new reading), they must still supply a complete 32-byte word. The natural implementation is to read the current slot state and re-include the existing bytes for positions 1–3. The new word carries `timestamp = T_new` (current time), but positions 1–3 carry prices that were observed at `T_old < T_new`. After the write:

- `refTime` for positions 1–3 = `T_new` (fresh — passes `_isStale`)
- `mid` for positions 1–3 = price observed at `T_old` (stale in reality)

The actual age of those prices is `block.timestamp − T_old`, but the staleness check measures `block.timestamp − T_new`. If `T_new − T_old` is large enough, prices that should have been rejected as stale instead pass and reach the pool's bid/ask computation.

This is the direct analog of the CometBFT/application view mismatch: the **timestamp** (current-push view) and the **price data** (previous-observation view) are from different states, yet they are consumed together as if they were coherent.

---

### Impact Explanation

A stale price for any of positions 1–3 that passes the staleness gate is forwarded through `_computeBidAsk` → `getBidAndAskPrice` → `MetricOmmPool.swap`. The pool executes the swap at the stale bid/ask, meaning:

- A trader can receive more output tokens than the true oracle price permits (loss to LPs / pool insolvency on that leg), or
- A trader is forced to pay more than the true price (loss to the trader).

Both outcomes are direct loss of user principal or LP assets, satisfying the Critical/High/Medium impact gate.

---

### Likelihood Explanation

The trigger is a **semi-trusted pusher** — any EOA or contract delegated via `allowPushers` / `allowContractPushers`. Partial updates are the normal operational pattern when one of four co-packed feeds has a data-source outage or a different update cadence. No malicious intent is required; the mismatch arises automatically whenever a pusher refreshes one lane while re-including the unchanged bytes of the others. A malicious delegated pusher can also exploit this deliberately by freezing a price lane at a favorable value while keeping the slot timestamp fresh via position-0 updates. [6](#0-5) 

---

### Recommendation

Replace the single slot-level timestamp with **per-lane timestamps**. Each 48-bit lane currently uses 32 bits for price and 16 bits for spread indices; one option is to embed a compact per-lane timestamp delta or to widen the slot to two storage words (one for prices, one for per-lane timestamps). Alternatively, enforce at the push layer that all four lanes in a slot must be updated simultaneously with fresh data, and reject any word where a lane's price is unchanged from the previous slot value unless the pusher explicitly zeroes it (triggering the existing stale-zero rejection path in consumers).

---

### Proof of Concept

```
Setup
─────
• CompressedOracleV1 deployed with MAX_TIME_DRIFT = 0, MAX_REF_STALENESS = 3600 s (1 h).
• AnchoredPriceProvider configured with offchainOracle = CompressedOracleV1,
  baseFeedId = feedIdOf(creator, slot=0, pos=1)  ← the victim feed.
• Creator pushes slot 0 at T_old = 1_000_000 s:
    word = [pos0_data | pos1_data_REAL | pos2_data | pos3_data | ts=T_old*1000 | slotId=0]
  All four positions have fresh, valid prices. Staleness check passes.

Attack
──────
1. At T_new = T_old + 3599 s (1 second before pos1 would go stale):
   Creator/pusher pushes slot 0 again:
     word = [pos0_data_NEW | pos1_data_REAL (unchanged) | pos2_data | pos3_data
             | ts = T_new * 1000 | slotId = 0]
   The slot timestamp is now T_new. pos1's price is still the T_old observation.

2. At T_new + 2 s (= T_old + 3601 s — pos1 price is now 3601 s old, past MAX_REF_STALENESS):
   AnchoredPriceProvider._readLeg(feedId_pos1) calls oracle.price(feedId_pos1, pool).
   refTime = T_new (from the slot timestamp).
   _isStale(T_new, T_new+2, 3600) → (T_new+2 − T_new) = 2 ≤ 3600 → NOT stale.
   mid = pos1_data_REAL.price (3601 s old, stale in reality).
   ok = true → stale price reaches getBidAndAskPrice → pool swap executes at wrong price.

Expected (correct) behavior
───────────────────────────
refTime should reflect when pos1's price was actually observed (T_old).
_isStale(T_old, T_old+3601, 3600) → 3601 > 3600 → stale → feed halted.
```

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L101-117)
```text
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L192-212)
```text
    function allowPushers(uint256 deadline, address[] calldata pushers, bytes[] memory signatures) external {
        _ensureDeadline(deadline);

        uint256 l = pushers.length;
        require(l == signatures.length);
        for (uint256 i; i < l; i++) {
            address pusher = pushers[i];

            if (pusher == msg.sender) {
                revert NoSelfRemapping();
            }

            bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
                keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
            );
            require(pusher == ECDSA.recover(hash, signatures[i]));

            namespaceRemapping[pusher] = msg.sender;
            emit PusherAuthorized(pusher, msg.sender);
        }
    }
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

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L280-283)
```text
        (mid, spreadBps, , refTime) = IPricedOracle(address(offchainOracle)).price(feedId, msg.sender);

        // Stale reference → not ok. Clamping to a stale anchor is the one false-safety case.
        if (_isStale(refTime, block.timestamp, MAX_REF_STALENESS)) return (mid, spreadBps, refTime, false);
```
