### Title
Shared Slot-Level Timestamp in `CompressedOracleV1` Allows Stale Per-Position Prices to Pass Staleness Checks, Enabling Bad-Price Execution in Pools — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracleV1` packs four oracle feeds into a single 256-bit storage slot with one shared timestamp. When a pusher updates any position in a slot, the entire slot — including all four positions — is atomically overwritten with the new timestamp. A `PriceProvider` consuming a specific position receives the slot-level `timestampMs` as `refTime`, which can be fresh even when that position's price data has not changed. This allows a stale bid/ask quote to pass the `PriceProvider` staleness check and reach a pool swap.

---

### Finding Description

**Slot layout — one timestamp for four feeds:**

`CompressedOracleV1` stores four oracle lanes in a single EVM storage word:

```
bits 255…208 : oracle[0] (48 bits: price U64x32 + s0 + s1)
bits 207…160 : oracle[1]
bits 159…112 : oracle[2]
bits 111… 64 : oracle[3]
bits  63…  8 : timestamp (uint56, unix milliseconds)  ← SHARED
bits   7…  0 : reserved
``` [1](#0-0) 

The `_loadSlotLayout` function reads the single slot word and assigns the same `timestampMs` to all four positions:

```solidity
_layout.timestampMs = toTimeMs(slotValue >> 8 & X56);
_layout.oracle0 = _decodeCompressedOracleData(uint48((slotValue >> 208) & X48));
_layout.oracle1 = _decodeCompressedOracleData(uint48((slotValue >> 160) & X48));
_layout.oracle2 = _decodeCompressedOracleData(uint48((slotValue >> 112) & X48));
_layout.oracle3 = _decodeCompressedOracleData(uint48((slotValue >> 64) & X48));
``` [1](#0-0) 

`getOracleData()` then returns this slot-level timestamp for whichever position is queried:

```solidity
data.timestampMs = _layout.timestampMs;   // slot timestamp, not per-position
``` [2](#0-1) 

**Push path — atomic full-slot overwrite:**

The `fallback()` push path accepts one or more 32-byte slot words. Each word encodes all four positions plus the timestamp. The entire slot is overwritten on every push; there is no mechanism to update a single position independently:

```solidity
// 4 * 6 + 7 + 1 = 32 bytes per slot
if (end == 0 || end % 32 != 0) revert BadCalldataLength();
for (uint256 ptr = 0; ptr < end; ptr += 32) { ... }
``` [3](#0-2) 

The only freshness gate is monotonicity: the new timestamp must be strictly greater than the stored one. There is no per-position timestamp and no per-position monotonicity check. [4](#0-3) 

**Staleness check in `PriceProvider` uses the slot timestamp:**

`PriceProvider._getBidAndAskPrice()` calls `oracle.price(feedId, pool)`, which returns `refTime` derived from `timestampMs` (the slot-level timestamp). The staleness check then compares this against `block.timestamp`:

```solidity
(uint256 mid, uint256 spread, , uint256 refTime) =
    IPricedOracle(address(offchainOracle)).price(offchainFeedId, msg.sender);

if (_isStale(refTime, block.timestamp, MAX_TIME_DELTA)) {
    return (0, type(uint128).max);
}
``` [5](#0-4) 

Because `refTime` is the slot-level timestamp — not a per-position timestamp — a position whose price has not changed since the last push will appear fresh to this check whenever any other position in the same slot is updated.

**The natural trigger:**

A pusher who manages a slot with four feeds of different update frequencies (e.g., ETH/USD updated every 30 s, BTC/USD every 5 min) must write all four positions atomically on every push. When they push a fresh ETH/USD price, they must also write the current (unchanged) BTC/USD price. The slot timestamp advances to "now," but the BTC/USD price is the same value it had 4.5 minutes ago. The `PriceProvider` staleness check for the BTC/USD feed passes because it sees the fresh slot timestamp, not the age of the BTC/USD price itself.

This is structurally identical to the external bug: just as `PeerPoolInfo.balance` is the last-known destination balance (stale due to cross-chain asynchronicity), the per-position price in a `CompressedOracleV1` slot is the last-known price for that position (stale due to the atomic slot-overwrite constraint), yet both pass their respective freshness checks.

---

### Impact Explanation

A pool whose `PriceProvider` is backed by a `CompressedOracleV1` feed can execute swaps at a stale bid/ask price that has passed the staleness check. This constitutes **bad-price execution**: the pool's quoted prices lag the true market, exposing LPs to arbitrage losses and giving traders execution at incorrect prices. The loss is direct and proportional to the price drift during the staleness window.

---

### Likelihood Explanation

The trigger is a natural consequence of the slot design, not a malicious act. Any deployment where multiple feeds with different update cadences share a slot — the expected use case given the 4-position-per-slot layout — will exhibit this behavior on every push that refreshes a fast-moving feed while a slower-moving feed's price remains unchanged. The pusher need not be malicious; negligent or routine operation is sufficient.

---

### Recommendation

1. **Per-position timestamps**: Redesign the slot layout to include a per-position timestamp (e.g., reduce each lane from 48 bits to 40 bits and allocate 8 bits per lane for a coarse timestamp delta, or use a second storage slot for timestamps).
2. **Enforce uniform update cadence**: Require that all positions in a slot be updated simultaneously with fresh data, and enforce this at the oracle level by rejecting pushes where any position's price is unchanged from the stored value.
3. **Document the constraint**: At minimum, document that all positions sharing a slot must have the same update frequency, and that `PriceProvider.MAX_TIME_DELTA` must be set to the *shortest* acceptable staleness window across all positions in the slot — not the longest.

---

### Proof of Concept

```
Setup:
  - CompressedOracleV1 deployed with MAX_TIME_DRIFT = 5 s
  - Slot 0 of creator Alice holds:
      pos 0 → ETH/USD  (updated every 30 s)
      pos 1 → BTC/USD  (updated every 5 min)
  - PriceProvider_BTC configured with offchainFeedId = feedIdOf(Alice, 0, 1)
    and MAX_TIME_DELTA = 120 s
  - Pool_BTC uses PriceProvider_BTC

Attack trace:
  T=0:    Alice's pusher writes slot 0 with fresh ETH=$3000, BTC=$60000, ts=0.
  T=30s:  ETH moves to $3050. Pusher writes slot 0: ETH=$3050, BTC=$60000, ts=30s.
          → slot timestamp = 30s; BTC price unchanged.
  T=60s:  ETH moves to $3100. Pusher writes slot 0: ETH=$3100, BTC=$60000, ts=60s.
          → slot timestamp = 60s; BTC price still $60000.
  T=90s:  BTC moves to $63000 (+5%). No push yet (cadence is 5 min).
  T=90s:  ETH moves to $3150. Pusher writes slot 0: ETH=$3150, BTC=$60000, ts=90s.
          → slot timestamp = 90s; BTC price still $60000 (90s stale in market terms).
  T=90s:  Pool_BTC.swap() called.
          PriceProvider_BTC calls oracle.price(btcFeedId, pool).
          Oracle returns: mid=$60000, refTime=90s.
          _isStale(90, 90, 120) → false  ← passes!
          Pool quotes BTC at $60000 instead of $63000.
          Arbitrageur buys BTC from pool at $60000, sells on market at $63000.
          LP loss: $3000 per BTC traded.
``` [1](#0-0) [2](#0-1) [5](#0-4) [6](#0-5)

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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L287-293)
```text
        uint256 old = uint256(_loadStorage(key));
        TimeMs oldTimestampMs = toTimeMs(old >> 8 & X56);

        bool newer = timestampMs.isAfter(oldTimestampMs);
        if (!newer) {
            return false;
        }
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L311-330)
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
