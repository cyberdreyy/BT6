### Title
Incomplete Stall-Sentinel Check in `CompressedOracleV1::getOracleData` Allows a Pusher to Permanently DoS Pool Swaps - (File: `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

### Summary

`CompressedOracleV1::getOracleData` only recognizes the stall sentinel when **both** `s0 == 0xFF` and `s1 == 0xFF`. If a pusher writes a slot where exactly one of the two spread indices is `0xFF`, the stall guard is skipped and `_decodeCodebookIndex(0xFF)` is called. Because `Codebook256` has 255 entries (indices 0–254), index `0xFF = 255` is out of range and `decode` returns `ok = false`, causing `_decodeCodebookIndex` to revert with `CodebookDecodeFailed(255)`. This revert propagates through `price()` → `PriceProvider`/`AnchoredPriceProvider::getBidAndAskPrice()` → pool `swap()`, permanently bricking swaps for every pool that reads the affected feed.

---

### Finding Description

**Root cause — incomplete sentinel in `getOracleData`:** [1](#0-0) 

```solidity
if (compressed.s1 == 0xff && compressed.s0 == 0xff) {   // ← AND, not OR
    data.spread1 = BPS_BASE;
    data.spread0 = BPS_BASE;
    return data;
}
data.price  = U64x32.decode(compressed.p);
data.spread0 = _decodeCodebookIndex(compressed.s0);      // reverts if s0 == 0xFF
data.spread1 = _decodeCodebookIndex(compressed.s1);      // reverts if s1 == 0xFF
```

**`_decodeCodebookIndex` reverts on index 255:** [2](#0-1) 

**`Codebook256.decode` returns `ok = false` for index ≥ `entryCount`:** [3](#0-2) 

`getTable()` allocates `new uint16[](MAX_INDEX)` = `new uint16[](255)`, confirming the table holds exactly 255 entries (indices 0–254). Index `0xFF = 255` is therefore always out of range. [4](#0-3) 

**The `fallback` push path writes raw slot words with no codebook validation:** [5](#0-4) 

Any authorized pusher (creator or delegated EOA/contract) can write a slot word where one lane has `s0 = 0xFF, s1 = 0x00` (or vice versa). The monotonicity check only compares timestamps; it never inspects codebook indices.

**The revert propagates unhandled through both price providers:**

`PriceProvider._getBidAndAskPrice` calls `price(feedId, pool)` with no `try/catch`: [6](#0-5) 

`AnchoredPriceProvider._readLeg` does the same: [7](#0-6) 

Both providers expose `getBidAndAskPrice()` which the pool calls during every swap. A revert here makes every swap revert. [8](#0-7) 

---

### Impact Explanation

A delegated pusher writes one malformed 32-byte slot word (e.g., position 0 with `s0 = 0xFF, s1 = 0x00`). From that moment, every call to `getOracleData` for any feed in that slot reverts. Because neither `PriceProvider` nor `AnchoredPriceProvider` wraps the oracle call in `try/catch`, every pool swap that reads the feed reverts. The pool is completely bricked for swaps. Recovery requires the creator to overwrite the slot with a valid word at a strictly newer timestamp — but if the pusher is the only authorized updater, or if the creator's key is unavailable, the pool may be permanently DoS'd.

This matches the allowed impact gate: **broken core pool functionality / unusable swap flows**.

---

### Likelihood Explanation

- Any delegated EOA or contract pusher (authorized via `allowPushers` / `allowContractPushers`) can write the malformed word. Delegation is a normal operational pattern.
- The `fallback` path performs zero codebook validation; the malformed word is accepted silently.
- A single 32-byte calldata word is sufficient to trigger the condition.
- The condition is permanent until overwritten with a newer timestamp by an authorized party.

---

### Recommendation

1. **Fix the sentinel check to handle each spread index independently:**

```solidity
data.spread0 = compressed.s0 == 0xff ? BPS_BASE : _decodeCodebookIndex(compressed.s0);
data.spread1 = compressed.s1 == 0xff ? BPS_BASE : _decodeCodebookIndex(compressed.s1);
```

Or treat any lane where either index is `0xFF` as stalled (fail-closed).

2. **Validate codebook indices in the `fallback` push path** before writing, rejecting words that contain a single-sided `0xFF` spread index.

3. **Wrap oracle calls in `try/catch` in `PriceProvider` and `AnchoredPriceProvider`** so that an unexpected oracle revert returns the `(0, type(uint128).max)` stall sentinel rather than propagating the revert to the pool swap — mirroring the exact fix recommended in the external report.

---

### Proof of Concept

```solidity
// 1. Creator delegates pusher
uint256 deadline = block.timestamp + 1 days;
bytes memory sig = _signConsent(PUSHER_KEY, deadline, pusher, creator);
address[] memory pushers = new address[](1); pushers[0] = pusher;
bytes[] memory sigs = new bytes[](1); sigs[0] = sig;
vm.prank(creator);
oracle.allowPushers(deadline, pushers, sigs);

// 2. Pusher writes a slot with s0=0xFF, s1=0x00 for position 0
// Lane bits [255:208]: p=0, s0=0xFF, s1=0x00 → raw48 = 0x00_FF_00 << 16 = 0x0000FF0000
// Full word: oracle0 lane | timestamp | slotId
uint56 tsMs = uint56(block.timestamp * 1000);
uint48 malformedLane = (uint48(0xFF) << 8) | uint48(0x00); // s0=0xFF, s1=0x00
uint256 word = (uint256(malformedLane) << 208) | (uint256(tsMs) << 8) | uint256(slotId);
vm.prank(pusher);
(bool ok,) = address(oracle).call(abi.encodePacked(word));
assertTrue(ok); // push succeeds — no validation

// 3. getOracleData now reverts
bytes32 feedId = oracle.feedIdOf(creator, slotId, 0);
vm.expectRevert(); // CodebookDecodeFailed(255)
oracle.getOracleData(feedId);

// 4. price() reverts → getBidAndAskPrice() reverts → pool swap reverts
vm.expectRevert();
priceProvider.getBidAndAskPrice();
```

### Citations

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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L155-159)
```text
    function _decodeCodebookIndex(uint8 index) internal pure returns (uint16 value) {
        bool ok;
        (value, ok) = Codebook256.decode(index);
        if (!ok) revert CodebookDecodeFailed(index);
    }
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L311-344)
```text
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

**File:** smart-contracts-poc/contracts/oracles/utils/Codebook256.sol (L13-18)
```text
    function getTable() external pure returns (uint16[] memory t) {
        t = new uint16[](MAX_INDEX);
        for (uint256 i; i < MAX_INDEX; i++) {
            t[i] = _valueAt(i);
        }
    }
```

**File:** smart-contracts-poc/contracts/oracles/utils/Codebook256.sol (L20-23)
```text
    function decode(uint8 index) internal pure returns (uint16 value, bool ok) {
        uint256 entryCount = TABLE.length / 2;
        if (entryCount == 0 || index > MAX_INDEX || index >= entryCount) return (0, false);
        return (_valueAt(index), true);
```

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L194-196)
```text
        (uint256 mid, uint256 spread, , uint256 refTime) =
            IPricedOracle(address(offchainOracle)).price(offchainFeedId, msg.sender);

```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L214-217)
```text
    function getBidAndAskPrice() external override returns (uint128 bid, uint128 ask) {
        (bid, ask) = _getBidAndAskPrice();
        if (bid == 0 || ask == type(uint128).max) revert FeedStalled();
    }
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L280-281)
```text
        (mid, spreadBps, , refTime) = IPricedOracle(address(offchainOracle)).price(feedId, msg.sender);

```
