### Title
`priceGuard` Min/Max Bounds Are Configured But Never Enforced in Push or Read Paths, Allowing Unbounded Prices to Reach Pool Swaps — (`smart-contracts-poc/contracts/oracles/compressed/OracleBase.sol`, `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

The `priceGuard` mechanism — a per-feed min/max price bound configurable by the feed creator — exists in both the compressed oracle and the providers oracle, but is **never checked** in the push path (`CompressedOracle.fallback()`, `updateBySignature()`) or the read path (`CompressedOracle.price()`, `OracleBase.price()`). A delegated pusher can write any price value into the oracle; it will be stored and returned to pools without bounds validation, enabling bad-price execution.

---

### Finding Description

`OracleBase` (compressed) exposes `setPriceGuard`, callable by the feed creator, to configure a `PriceGuard{min, max}` for a feed: [1](#0-0) 

The push path in `CompressedOracle.fallback()` resolves the creator namespace, validates timestamp monotonicity and drift, then writes the slot word directly to storage: [2](#0-1) 

There is **no check** against `priceGuard[feedId]` at any point in this loop. The same omission exists in `updateBySignature()`: [3](#0-2) 

The read path is equally unguarded. `CompressedOracle.price()` calls `_price()` → `getOracleData()`, which decodes and returns the raw stored value with no bounds check: [4](#0-3) [5](#0-4) 

The same pattern holds in the providers oracle: `ChainlinkOracle._store()` checks timestamp validity and monotonicity but never consults `priceGuard`: [6](#0-5) 

And `OracleBase._readPrice()` returns raw stored data without bounds validation: [7](#0-6) 

**Invariant broken**: `priceGuard` is the protocol's own declared mechanism for bounding feed prices. Its existence implies the invariant `min ≤ price ≤ max` must hold for any price served to a pool. Because neither the write path nor the read path enforces this, the invariant is never actually upheld — `priceGuard` is dead code.

**Analog to the seed bug**: Exactly as `vest()` in StHEU.sol does not check `migrationMode` before allowing new vests during a sensitive state transition, `CompressedOracle.fallback()` (and `updateBySignature()`) do not check `priceGuard` before storing a new price, allowing an out-of-bounds price to persist in oracle state and be consumed by pools.

---

### Impact Explanation

A price outside the configured `priceGuard` bounds — including a near-zero price or an extreme high — is stored and returned verbatim by `CompressedOracle.price()`. The pool's swap math consumes this value as the bid/ask anchor. A manipulated price causes the pool to:

- Accept far less input than the true market rate (trader receives more than permitted), or
- Pay out far more output than the oracle/bin curve permits,

resulting in **direct loss of LP principal** and **swap conservation failure** — both contest-relevant impacts above Sherlock thresholds.

---

### Likelihood Explanation

The trigger is a **delegated pusher** — a semi-trusted address authorized by the feed creator via `allowPushers` or `allowContractPushers`: [8](#0-7) 

A compromised or malicious delegated pusher can craft a slot word with an arbitrary price field (the 32-bit `p` field in the packed layout) and push it via the `fallback()`. No privileged role is required beyond the delegation already granted. The feed creator configured `priceGuard` precisely to limit this damage surface — but since it is never enforced, the protection is illusory.

---

### Recommendation

Enforce `priceGuard` at the point of storage. In `CompressedOracle.fallback()` and `updateBySignature()`, after decoding the price from the slot word, check it against the configured bounds before writing:

```solidity
uint256 decodedPrice = U64x32.decode(uint32(word >> (64 + slotOffset)));
PriceGuard memory guard = priceGuard[feedId];
if (guard.max != 0) {
    require(decodedPrice >= guard.min && decodedPrice <= guard.max, PriceOutOfBounds());
}
```

Apply the same check in `ChainlinkOracle._store()` after `_decodeReport`. Alternatively, enforce bounds in the read path (`getOracleData` / `_readPrice`) so that an out-of-bounds stored price is rejected before it reaches a pool.

---

### Proof of Concept

1. Feed creator calls `setPriceGuard(feedId, 1_000e8, 10_000e8)` — intending to bound ETH/USD between \$1,000 and \$10,000.
2. A delegated pusher (authorized via `allowPushers`) constructs a 32-byte slot word with the price field set to `U64x32.encode(1)` (≈ \$0.00000001 in 8-decimal format) and a fresh timestamp.
3. The pusher calls `CompressedOracle` with this calldata, triggering `fallback()`.
4. `fallback()` validates only timestamp monotonicity and drift — **no `priceGuard` check** — and writes the slot to storage.
5. A pool calls `CompressedOracle.price(feedId, pool)`, which returns `mid = 1`.
6. The pool's swap math uses `mid = 1` as the oracle anchor; a trader swaps and receives output valued at the true market price while paying input priced at \$0.00000001, draining LP reserves. [9](#0-8) [1](#0-0)

### Citations

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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L163-178)
```text
    function price(bytes32 feedId, address /* pool */)
        external
        view
        returns (uint256 mid, uint256 spread, uint16 spread1, uint256 refTime)
    {
        return _price(feedId);
    }

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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L311-345)
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
    }
```

**File:** smart-contracts-poc/contracts/oracles/providers/ChainlinkOracle.sol (L85-95)
```text
    function _store(bytes memory reportData) internal {
        (bytes32 feedId, OracleData memory d) = _decodeReport(reportData);

        d.timestampMs.revertIfZero();
        d.timestampMs.revertIfAfterBlockTimeWithDrift(MAX_TIME_DRIFT);

        if (d.timestampMs.isAfter(oracleData[feedId].timestampMs)) {
            oracleData[feedId] = d;
            emit ReportStored(feedId, d.price, d.spread0, d.timestampMs);
        }
    }
```

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L187-194)
```text
    function _readPrice(bytes32 feedId)
        internal
        view
        returns (uint256 mid, uint256 spread, uint16 spread1, uint256 refTime)
    {
        OracleData memory data = _oracleDataRaw(feedId);
        return (uint256(data.price), uint256(data.spread0), data.spread1, data.timestampMs.toSeconds());
    }
```
