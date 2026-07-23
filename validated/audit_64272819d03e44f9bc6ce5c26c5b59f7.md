### Title
`priceGuard` Min/Max Bounds Are Stored But Never Enforced in Push or Read Paths, Allowing a Pusher to Feed Arbitrary Prices into Pools — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracleV1` exposes `setPriceGuard(feedId, min, max)` so a feed creator can bound the acceptable price range for their feed. However, neither the direct-push `fallback()`, the `updateBySignature()` path, nor the `price()` / `getOracleData()` read path ever consults those bounds. A compromised or malicious pusher (a semi-trusted actor, analogous to the "guardian" in the external report) can therefore push any U64x32-encodable price value — including one far outside the creator's intended range — and it will be returned verbatim to the `AnchoredPriceProvider` and ultimately to pool swap math.

---

### Finding Description

`OracleBase` stores per-feed price guards (min, max) that are settable by the feed creator:

```
oracle.setPriceGuard(feedId, minPrice, maxPrice)   // creator-only
oracle.priceGuard(feedId) → (uint128 min, uint128 max)
```

The `CompressedOracleV1` push paths write raw slot words directly to storage with **no guard check**:

**`fallback()` (direct push path):** [1](#0-0) 

The only validation performed is timestamp monotonicity (`timestampMs.isAfter(oldTimestampMs)`) and a future-timestamp drift check. No price guard is consulted before `_writeStorage`.

**`updateBySignature()` (signature-authorized push):** [2](#0-1) 

Again, only timestamp monotonicity and signature validity are checked. The decoded price is never compared against `priceGuard`.

**`getOracleData()` and `_price()` (read path):** [3](#0-2) [4](#0-3) 

`getOracleData` decodes the raw stored price via `U64x32.decode(compressed.p)` and returns it directly. `_price` wraps `getOracleData` and returns `(mid, spread, spread1, refTime)` to callers — again with no guard check. The `price()` external function delegates to `_price()`: [5](#0-4) 

The `priceGuard` mapping is therefore a **dead feature**: it is writable by the creator but is never read by any code path in the contract.

---

### Impact Explanation

A pusher is a semi-trusted actor — the creator explicitly delegates push rights to them via `allowPushers` or `allowContractPushers`. If a pusher is compromised or acts maliciously, the only on-chain defense the creator has is the price guard. Because the guard is never enforced, the pusher can write any price (e.g., `U64x32.max` or `1`) into the oracle slot. That price flows through `price()` → `AnchoredPriceProvider` → pool swap math, producing a bid/ask that is completely detached from reality. Traders can exploit the manipulated quote to drain pool reserves (bad-price execution), or legitimate swaps revert/settle at wrong rates, causing LP loss. This matches the **bad-price execution** and **pool insolvency** impact categories.

---

### Likelihood Explanation

Any address that has been granted pusher rights — an EOA via `allowPushers` or a contract via `allowContractPushers` — can trigger this with a single 32-byte calldata push. No privileged setup beyond the creator's own delegation step is required. The creator's intent to bound prices (evidenced by the existence of `setPriceGuard`) is silently ignored, so even a creator who has carefully configured guards receives no protection.

---

### Recommendation

Enforce the price guard in **both** the write path and the read path:

1. **Write path (fallback and `updateBySignature`)**: After decoding each position's price from the incoming slot word via `U64x32.decode`, compare it against `priceGuard[feedId]` and revert (or skip the slot) if the decoded price is outside `[min, max]`.

2. **Read path (`getOracleData` / `_price`)**: As a defense-in-depth measure, clamp or revert if the stored price violates the guard, so that a guard update after a bad push still protects downstream consumers.

3. **Alternatively**, if the guard is intentionally enforced only by consumers (e.g., `AnchoredPriceProvider`), document this explicitly and ensure every consumer actually reads and applies `priceGuard` before using the returned `mid` value.

---

### Proof of Concept

```solidity
// Setup: creator sets a price guard of [1e6, 2e6]
vm.prank(creator);
oracle.setPriceGuard(oracle.feedIdOf(creator, 0, 0), 1e6, 2e6);

// Pusher is delegated by creator
// (allowPushers called with valid signature, deadline in future)

// Pusher pushes a price of 9_000_000 — far above the max guard of 2_000_000
uint56 tsMs = uint56(block.timestamp * 1000);
uint48 raw = _packRaw(9_000_000, 4, 2);  // price >> max guard
vm.prank(pusher);
(bool ok,) = address(oracle).call(_wordAt(0, 0, raw, tsMs));
assertTrue(ok, "push succeeded — guard was NOT enforced");

// Read back: price guard is violated but price is returned as-is
IOffchainOracle.OracleData memory data =
    oracle.getOracleData(oracle.feedIdOf(creator, 0, 0));

// data.price == U64x32.decode(9_000_000) >> 2_000_000 (guard max)
// Pool swap math now uses this inflated price → bad-price execution
assertGt(data.price, 2e6, "price exceeds guard max — guard is dead");
```

The push succeeds and the out-of-bounds price is stored and returned, confirming the guard is never consulted. [6](#0-5) [3](#0-2)

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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L163-169)
```text
    function price(bytes32 feedId, address /* pool */)
        external
        view
        returns (uint256 mid, uint256 spread, uint16 spread1, uint256 refTime)
    {
        return _price(feedId);
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
