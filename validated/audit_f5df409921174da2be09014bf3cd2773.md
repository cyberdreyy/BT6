### Title
`deactivateFeeds` sets an inactive flag that is never checked in the push or read path, allowing pushers to continue updating deactivated feeds whose prices are still consumed by pools — (File: `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

The `CompressedOracleV1` exposes a `deactivateFeeds(bytes32[])` function and an `isFeedActive(bytes32)` view, but neither the compressed push path (fallback handler) nor the `updateBySignature` path checks `isFeedActive` before writing a new price. The `price(feedId, pool)` read path likewise never checks the flag. The result is a direct analog to the external report: the deactivation mechanism sets a boolean that has no enforcement effect, so any pusher can continue injecting prices into a "deactivated" feed and any pool registered for that feed will consume those prices during swaps.

---

### Finding Description

The `CompressedOracleV1` contract exposes two deactivation-related entry points visible in the on-chain registry:

- `deactivateFeeds(bytes32[])` — selector `53bfbb75`
- `isFeedActive(bytes32)` — selector `790d7aae`

The intent is that once a feed is deactivated (e.g., because the underlying asset is deprecated or the feed is compromised), no further price updates should be accepted and no pool should be able to read from it.

However, the `updateBySignature` push path performs only three checks before writing a new slot value:

1. `feedCreator != address(0)` — namespace validity
2. `timestampMs.isAfter(oldTimestampMs)` — monotonicity
3. `feedCreator == ECDSA.recover(hash, signature)` — signature validity [1](#0-0) 

There is no `require(isFeedActive(feedId))` guard anywhere in this path. The same omission applies to the fallback-based compressed push handler (the primary push path for packed slot words), which also resolves the namespace and writes storage without consulting the active flag.

The `price(feedId, pool)` read path in the `CompressedOracle` is equally unguarded:

```solidity
function price(bytes32 feedId, address /* pool */)
    external view
    returns (uint256 mid, uint256 spread, uint16 spread1, uint256 refTime)
{
    return _price(feedId);   // calls getOracleData — no isFeedActive check
}
``` [2](#0-1) 

`_price` calls `getOracleData(feedId)` and returns the raw stored values. No staleness check beyond `timestampMs` and no active-flag check are performed.

The `OracleBase` (providers path) `price()` function enforces `feedExists`, `notBlacklisted`, `inSwap`, and `registeredPool`, but not `isFeedActive`: [3](#0-2) 

---

### Impact Explanation

A pool registered for a feed that has been deactivated continues to call `price(feedId, pool)` during every swap. Because the push path does not check `isFeedActive`, any pusher (the push path is permissionless for one's own namespace, and delegated pushers operate under the creator's namespace) can write a new price into the deactivated feed at any time. The pool's `AnchoredPriceProvider` or `ProtectedPriceProvider` will read that price, compute bid/ask from it, and execute swaps against it. This satisfies the allowed-impact gate:

- **Bad-price execution**: a stale, inverted, or attacker-controlled bid/ask quote reaches a pool swap.
- **Pool insolvency / swap conservation failure**: if the injected price deviates sufficiently from fair value, traders extract more value than the pool is owed, draining LP principal.

---

### Likelihood Explanation

The `CompressedOracle` push path is permissionless for any address pushing into its own namespace. A creator whose feed has been deactivated (or any delegated pusher still mapped to that creator via `namespaceRemapping`) can immediately push a new price. No privileged role, no special setup, and no malicious initial configuration is required — the attacker only needs to be a valid pusher for the feed's namespace, which is the normal operational state before deactivation. [4](#0-3) 

---

### Recommendation

Add an `isFeedActive` check at the top of every write path. The fix mirrors the recommendation in the external report:

**In the fallback push handler and `updateBySignature`:**
```solidity
require(isFeedActive(feedId), FeedDeactivated(feedId));
```

**In `price(feedId, pool)` and `_price`:**
```solidity
require(isFeedActive(feedId), FeedDeactivated(feedId));
```

This ensures that once `deactivateFeeds` is called, the flag has actual enforcement effect on both the write and read paths, consistent with the intent of the deactivation mechanism.

---

### Proof of Concept

1. Admin calls `deactivateFeeds([feedId])` — `isFeedActive(feedId)` now returns `false`.
2. A pool is still registered for `feedId` via `registeredPool[feedId][pool] = true`.
3. Attacker (a valid pusher for the feed's namespace) calls the fallback push handler with a crafted slot word encoding an extreme price (e.g., `U64x32`-encoded value near `type(uint32).max`). The monotonicity check passes because the new `timestampMs` is greater than the stored one.
4. The price is written to storage. `isFeedActive` is never consulted.
5. A trader calls `swap()` on the pool. The pool calls `getQuotes()` → `price(feedId, pool)`. The `CompressedOracle.price()` returns the attacker-injected price without checking `isFeedActive`.
6. `SwapMath` computes a bid/ask from the manipulated mid price. The trader receives more output tokens than fair value, draining LP funds. [5](#0-4)

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L151-178)
```text
    function _encodeCompressedOracleData(CompressedOracleData memory data) internal pure returns (uint48 raw) {
        raw = (uint48(data.p) << 16) | (uint48(data.s0) << 8) | uint48(data.s1);
    }

    function _decodeCodebookIndex(uint8 index) internal pure returns (uint16 value) {
        bool ok;
        (value, ok) = Codebook256.decode(index);
        if (!ok) revert CodebookDecodeFailed(index);
    }

    /// @notice Unified read path shared with the providers oracle. The compressed oracle is open, so
    ///         `pool` is unused (no in-swap binding) and reads are permissionless.
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L271-300)
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
```

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L160-172)
```text
    function price(bytes32 feedId, address pool)
        external
        feedExists(feedId)
        notBlacklisted
        returns (uint256 mid, uint256 spread, uint16 spread1, uint256 refTime)
    {
        require(pool != address(0) && IPool(pool).inSwap() == msg.sender, InvalidInSwap());
        require(!blacklisted[pool], Blacklisted(pool));
        require(registeredPool[feedId][pool], NotRegistered(feedId, pool));

        (mid, spread, spread1, refTime) = _readPrice(feedId);
        emit PriceRead(pool, feedId);
    }
```
