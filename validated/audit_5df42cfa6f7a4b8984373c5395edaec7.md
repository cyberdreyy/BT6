### Title
Unenforced `priceGuard` in `CompressedOracleV1` allows feed creator to push unbounded prices into pool swaps — (File: `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`, `smart-contracts-poc/contracts/oracles/compressed/OracleBase.sol`)

---

### Summary

`OracleBase` stores a per-feed `priceGuard` (min/max price bounds) that is never read during either the write path (`fallback()` / `updateBySignature()`) or the read path (`getOracleData()` / `price()`). A feed creator — or any pusher they delegate — can therefore store an arbitrarily large or small price in the oracle with no on-chain bounds check, and that unclamped value flows directly to pool swap pricing.

---

### Finding Description

`OracleBase.setPriceGuard()` stores a `PriceGuard{min, max}` struct keyed by `feedId`: [1](#0-0) 

The guard is settable by the feed's creator (via `checkRole` → `_defaultGuard` → creator address decoded from `feedId`): [2](#0-1) 

However, `CompressedOracleV1.fallback()` — the primary push path — performs **only** a future-timestamp check and a monotonicity check before writing the raw slot word to storage: [3](#0-2) 

`priceGuard` is never consulted. Likewise, `getOracleData()` decodes and returns the stored price without any bounds check: [4](#0-3) 

And `price()` / `_price()` simply forward the result of `getOracleData()`: [5](#0-4) 

`updateBySignature()` has the same gap — it checks timestamp monotonicity and the creator's ECDSA signature, but never validates the price field against `priceGuard` before writing: [6](#0-5) 

The `U64x32` pseudo-float encoding allows a maximum decoded value of `(2^27 − 1) << 31 ≈ 2.88 × 10^17` (raw), which at 8 decimals represents a price of ~2.88 billion — orders of magnitude above any realistic asset price: [7](#0-6) 

---

### Impact Explanation

The price returned by `CompressedOracleV1.price()` is consumed by the price-provider chain (e.g., `AnchoredPriceProvider`) and ultimately drives bid/ask quotes in `MetricOmmPool` swaps. An unbounded price pushed by the creator causes:

- **Bad-price execution**: swaps execute at a wildly incorrect bid/ask, causing traders to receive far more or far less than the oracle-anchored curve permits.
- **Pool insolvency**: if the price is inflated, the pool pays out more of the quote token than it received; if deflated, the pool under-pays, breaking LP claims.
- **Broken swap conservation**: the swap math invariant (`trader receives ≤ oracle-permitted amount`) is violated.

---

### Likelihood Explanation

The feed creator is a semi-trusted party: the pool admin selects a `feedId` (and thus implicitly trusts the creator), but the creator is a separate entity. The `priceGuard` mechanism signals that the protocol designers intended an on-chain bound, yet it is entirely inert. Any creator — or a delegated pusher authorized via `allowPushers()` / `allowContractPushers()` — can push a maximally encoded `U64x32` price in a single `fallback()` call with no special privilege beyond namespace ownership. [8](#0-7) 

---

### Recommendation

Enforce `priceGuard` at both the write path and the read path:

1. **Write path** (`fallback()` and `updateBySignature()`): after decoding the price from the slot word via `U64x32.decode`, check it against `priceGuard[feedId]` and revert (or skip) if it falls outside `[min, max]`.
2. **Read path** (`getOracleData()` / `price()`): if a stored price violates the guard (e.g., due to a guard being set after the push), return a stale/zero sentinel or revert so consumers treat the feed as halted.
3. Alternatively, if the guard is intentionally enforced only at the provider layer (`AnchoredPriceProvider`), remove `setPriceGuard` from `OracleBase` entirely to eliminate the false sense of security.

---

### Proof of Concept

```solidity
// Creator pushes a maximally encoded U64x32 price (mantissa=0x7FFFFFF, exp=31)
// decoded value ≈ 2.88e17 (raw), i.e. ~2.88 billion at 8 decimals.
uint32 maxPrice = (uint32(31) << 27) | uint32((1 << 27) - 1); // 0xFFFFFFFF
uint48 raw = (uint48(maxPrice) << 16) | (uint48(3) << 8) | uint48(3); // valid spread indexes

uint56 tsMs = uint56(block.timestamp * 1000);
// Build slot word: oracle[0]=raw, others=0, timestamp=tsMs, slotId=0
uint256 word = (uint256(raw) << 208) | (uint256(tsMs) << 8) | 0;

// Creator pushes — only timestamp checks fire, priceGuard is never consulted
vm.prank(creator);
(bool ok,) = address(oracle).call(abi.encodePacked(word));
assert(ok);

// Price is stored and returned as-is
bytes32 feedId = oracle.feedIdOf(creator, 0, 0);
IOffchainOracle.OracleData memory data = oracle.getOracleData(feedId);
// data.price ≈ 2.88e17 — no revert, no clamping, priceGuard ignored
assert(data.price > 1e15);

// Pool swap now executes at this inflated price → bad-price execution
```

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/OracleBase.sol (L31-36)
```text
    modifier checkRole(bytes32 feedId) {
        address guard = stateGuard[feedId];
        if (guard == address(0)) guard = _defaultGuard(feedId);
        require(guard == msg.sender, InvalidGuard(msg.sender));
        _;
    }
```

**File:** smart-contracts-poc/contracts/oracles/compressed/OracleBase.sol (L49-57)
```text
    function setPriceGuard(bytes32 feedId, uint128 minPrice, uint128 maxPrice)
        external
        checkRole(feedId)
    {
        require(minPrice < maxPrice);

        priceGuard[feedId] = PriceGuard({min: minPrice, max: maxPrice});

        emit PriceGuardUpdated(feedId, minPrice, maxPrice);
```

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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L283-300)
```text
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
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L311-316)
```text
    fallback() override external {
        uint256 end;
        uint256 namespace;

        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;
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

**File:** smart-contracts-poc/contracts/oracles/utils/U64x32.sol (L14-20)
```text
    function decode(uint32 packed) internal pure returns (uint64 v) {
        uint64 m = uint64(uint32(packed) & uint32(MANT_MASK));
        uint64 e = uint64(uint32(packed) >> uint32(MANT_BITS));
        unchecked {
            v = uint64(uint256(m) << e);
        }
    }
```
