### Title
`priceGuard` Min/Max Bounds Are Stored But Never Enforced in the Oracle Read Path, Allowing Pushers to Feed Unclamped Prices Into Pools - (`smart-contracts-poc/contracts/oracles/compressed/OracleBase.sol`, `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracleV1` inherits a `setPriceGuard()` function from its `OracleBase` that lets the feed creator (or an accepted `stateGuard`) configure absolute `min`/`max` price bounds for any feed. However, neither `getOracleData()` nor `price()` — the two functions that downstream `PriceProvider` adapters call — ever read or enforce those bounds. A delegated pusher (authorized via `allowPushers` or `allowContractPushers`) can therefore push any price value, including one far outside the configured guard, and that value will be stored and returned to the pool verbatim.

---

### Finding Description

**Guard configuration (never enforced):**

`OracleBase.setPriceGuard()` stores a `PriceGuard{min, max}` struct keyed by `feedId`: [1](#0-0) 

The `checkRole` modifier resolves authority to the feed's creator (via `_defaultGuard`) when no explicit `stateGuard` is set: [2](#0-1) [3](#0-2) 

**Read path — no guard check:**

`getOracleData()` decodes the raw slot and returns the price directly, with no reference to `priceGuard`: [4](#0-3) 

`price()` (the function called by `PriceProvider` adapters) delegates to `_price()` → `getOracleData()`, again with no guard check. The `pool` parameter is explicitly discarded: [5](#0-4) 

**Push path — no guard check:**

The `fallback()` push path writes any slot word whose timestamp is newer than the stored one, with no price-bounds validation: [6](#0-5) 

`updateBySignature()` similarly writes the slot after verifying the creator's signature, but never consults `priceGuard`: [7](#0-6) 

**Delegation path — any authorized pusher can push any price:**

`allowPushers` maps a pusher wallet into the creator's namespace after verifying the pusher's EIP-191 consent signature: [8](#0-7) 

Once mapped, the pusher calls `fallback()` and the oracle resolves the target namespace from `namespaceRemapping[msg.sender]`: [9](#0-8) 

There is no step anywhere in this chain that reads `priceGuard[feedId]` and rejects or clamps a price that falls outside `[min, max]`.

**The `IOffchainOracle` interface does not expose `priceGuard`**, so downstream `PriceProvider` adapters cannot enforce it either — they receive only `(mid, spread, spread1, refTime)` from `price()`. [10](#0-9) 

---

### Impact Explanation

A creator who has configured a `priceGuard` (e.g., `min = 1_000_000_00`, `max = 2_000_000_00` for an asset expected to trade between $1,000 and $2,000) and delegated pushing to an automated bot has a reasonable expectation that the guard will prevent extreme prices from reaching the pool. Because the guard is never enforced:

- A compromised or malicious delegated pusher can push a price of `1` (effectively $0.00000001) or `type(uint128).max`.
- `PriceProvider` receives this value as `midPrice`, applies its `confidenceParam` and `marginStep` adjustments on top of the extreme base, and delivers a wildly wrong bid/ask pair to the pool.
- The pool executes swaps at the corrupted price, causing direct loss of LP principal or allowing a trader to drain the pool at a near-zero ask or near-infinite bid.

This matches the **bad-price execution** and **pool insolvency** impact categories in the allowed gate.

---

### Likelihood Explanation

The trigger requires a delegated pusher (authorized via `allowPushers` or `allowContractPushers`) to push an out-of-bounds price. This is a **semi-trusted** actor — not the creator themselves, but an address the creator explicitly authorized. Pusher key compromise, a malicious pusher contract, or a misconfigured off-chain bot are all realistic scenarios. The creator's belief that `priceGuard` provides a backstop makes this more likely to go unnoticed. No privileged admin action is required after delegation is established.

---

### Recommendation

Enforce the `priceGuard` bounds inside `getOracleData()` (or equivalently inside `_price()`) before returning the price to any caller:

```solidity
function getOracleData(bytes32 feedId) public view override returns (OracleData memory data) {
    // ... existing decode logic ...
    data.price = U64x32.decode(compressed.p);

    // ADD: enforce priceGuard if configured
    PriceGuard memory guard = priceGuard[feedId];
    if (guard.min != 0 || guard.max != 0) {
        require(data.price >= guard.min && data.price <= guard.max, PriceOutOfBounds(feedId, data.price));
    }

    data.spread0 = _decodeCodebookIndex(compressed.s0);
    data.spread1 = _decodeCodebookIndex(compressed.s1);
    data.timestampMs = _layout.timestampMs;
}
```

Alternatively, enforce the bounds in the `fallback()` and `updateBySignature()` write paths so that out-of-bounds slot words are rejected at ingestion time rather than at read time.

---

### Proof of Concept

```
1. Creator calls setPriceGuard(feedId, 1_000_000_00, 2_000_000_00)
   → priceGuard[feedId] = {min: 1e10, max: 2e10}  (stored, never read)

2. Creator calls allowPushers(deadline, [pusherAddr], [pusherSig])
   → namespaceRemapping[pusherAddr] = creator

3. Attacker (compromised pusher) calls fallback() with a slot word encoding:
   - price (U64x32) = encode(1)   // $0.00000001
   - timestamp = block.timestamp * 1000 + 1  // newer than stored

4. CompressedOracle.fallback():
   - creator = namespaceRemapping[msg.sender] = creator  ✓
   - timestampMs.isAfter(oldTimestampMs) = true  ✓
   - _writeStorage(key, word)  ← no priceGuard check

5. Pool calls PriceProvider.getBidAndAskPrice()
   → PriceProvider calls CompressedOracleV1.price(feedId, pool)
   → _price() → getOracleData() → returns price = 1  ← no priceGuard check
   → PriceProvider computes bid/ask around midPrice = 1
   → Pool executes swaps at near-zero ask price
   → Attacker drains pool token0 for effectively 0 token1
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L73-75)
```text
    function _defaultGuard(bytes32 feedId) internal view override returns (address creator) {
        (creator,,) = _unpackFeedId(feedId);
    }
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L315-321)
```text
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;

        assembly ("memory-safe") {
            end := calldatasize()
            namespace := shl(96, creator) // [creator:20][zeros:12]
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
