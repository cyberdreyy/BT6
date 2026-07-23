Based on my investigation of the `CompressedOracle` contract and its push/read paths, I found a direct analog to the Holdefi `removeMarket` / `marketsList` inconsistency.

---

### Title
Deactivated CompressedOracle feeds remain fully readable and writable, allowing stale or manipulated prices to reach pool swaps — (File: `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

### Summary
`CompressedOracle` maintains two independent state representations for a feed: an active/inactive flag (queryable via `isFeedActive`, settable via `deactivateFeeds`) and the packed price data in slot storage (read by `price()` → `_price()` → `getOracleData()`). Neither the read path (`price()`) nor the write paths (`updateBySignature`, fallback) check `isFeedActive`. When an admin calls `deactivateFeeds`, the active flag is set but the price data is not cleared and neither path is gated. Any pusher can continue writing new prices to a deactivated feed, and any pool configured to use that feed will continue to receive and execute swaps at those prices.

### Finding Description
`CompressedOracle` has two data structures tracking feed state — exactly the Holdefi pattern:

**Data structure 1 — active flag:** Set by `deactivateFeeds(bytes32[])`, queried by `isFeedActive(bytes32)`.

**Data structure 2 — price slot storage:** Written by `updateBySignature` and the fallback push path; read by `price()` → `_price()` → `getOracleData()`.

The `price()` function:

```solidity
// CompressedOracle.sol lines 163-168
function price(bytes32 feedId, address /* pool */)
    external
    view
    returns (uint256 mid, uint256 spread, uint16 spread1, uint256 refTime)
{
    return _price(feedId);
}
```

calls `_price()`:

```solidity
// CompressedOracle.sol lines 171-178
function _price(bytes32 feedId)
    internal
    view
    returns (uint256 mid, uint256 spread, uint16 spread1, uint256 refTime)
{
    OracleData memory data = getOracleData(feedId);
    return (uint256(data.price), uint256(data.spread0), data.spread1, data.timestampMs.toSeconds());
}
```

There is no `isFeedActive` check anywhere in this path. [1](#0-0) 

Similarly, `updateBySignature` writes new slot data with no activity check:

```solidity
// CompressedOracle.sol lines 271-300
function updateBySignature(address feedCreator, uint256 newSlotValue, bytes calldata signature)
    external
    override
    returns (bool)
{
    require(feedCreator != address(0), InvalidNamespace());
    // ... timestamp monotonicity check, signature check ...
    // NO isFeedActive check before writing
    _writeStorage(key, bytes32(newSlotValue & ~uint256(0xff)));
    ...
}
``` [2](#0-1) 

The fallback push path (which resolves namespace via `namespaceRemapping`) also has no activity gate. [3](#0-2) 

The ABI confirms both `deactivateFeeds(bytes32[])` and `isFeedActive(bytes32)` exist as on-chain functions, and `FeedDeactivated` is emitted with `feedId`, `slotIndex`, and `positionIndex` — indicating the active flag is a real on-chain state, not a metadata-only annotation. [4](#0-3) 

This is the direct Holdefi analog: `deactivateFeeds` updates one data structure (the active flag) but the price read and write paths use the other data structure (slot storage) without consulting the flag. A "deactivated" feed behaves identically to an active one for all on-chain operations.

### Impact Explanation
A pool configured to use a deactivated `CompressedOracle` feed continues to receive prices from it. If the feed was deactivated because it was compromised, stale, or being migrated to a new slot, the pool executes swaps at incorrect bid/ask prices. This is a **bad-price execution** impact: traders can receive more output than the oracle/bin curve permits, or the pool fails to receive owed input, depending on the direction of the pushed price. Because `CompressedOracle.price()` is open (the `pool` parameter is explicitly unused), any pool reading the feed gets the bad price with no attribution gate. [5](#0-4) 

### Likelihood Explanation
Medium. The trigger requires an admin to call `deactivateFeeds` (privileged), but the consequence is fully unprivileged: the original creator or any delegated pusher can continue pushing fresh prices to the deactivated feed via `updateBySignature` or the fallback path, and any pool using that feed will consume those prices without any on-chain signal that the feed is inactive. The admin's intent — stopping the feed — is silently not achieved, mirroring exactly the Holdefi `removeMarket` scenario where the operator believed the market was removed but it remained in `marketsList`.

### Recommendation
1. In `_price(feedId)`, add `require(isFeedActive(feedId), FeedNotFound(feedId))` before reading slot data.
2. In `updateBySignature` and the fallback push path, add the same guard before writing, so pushers cannot update a deactivated feed.
3. Alternatively, if `deactivateFeeds` is intentionally metadata-only with no on-chain enforcement, rename it to `markFeedInactive` and add explicit NatSpec documenting that deactivated feeds remain readable and writable on-chain, so integrators and pool operators are not misled.

### Proof of Concept
1. Creator pushes a price to `feedId = feedIdOf(creator, 0, 0)` via the fallback path. `price(feedId, pool)` returns the price.
2. Admin calls `deactivateFeeds([feedId])`. `isFeedActive(feedId)` now returns `false`.
3. Creator calls `updateBySignature(creator, newSlotValue, sig)` with a fresh timestamp. **No revert** — the write succeeds because `updateBySignature` has no `isFeedActive` check.
4. A pool configured to use `feedId` calls `price(feedId, pool)`. It receives the newly pushed (potentially manipulated) price — **no revert**, because `price()` has no `isFeedActive` check.
5. The pool executes a swap at the price from the "deactivated" feed, exposing LPs to bad-price execution loss.

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L161-178)
```text
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L262-266)
```text
    /*
     *
     * Push paths
     *
     */
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

**File:** smart-contracts-poc/contract-registry/versions/registry.json (L4174-4190)
```json
            "deactivateFeeds(bytes32[])": "53bfbb75",
            "decimals()": "313ce567",
            "extsload(bytes32)": "1e2eaeaf",
            "extsload(bytes32,uint256)": "35fd631a",
            "extsload(bytes32[])": "dbd035ff",
            "getCompressedOracle(bytes32)": "89f7d2cc",
            "getOracleCount()": "3f4e4251",
            "getOracleData(bytes32)": "b5e4b813",
            "getOracleDataBulk(bytes32[])": "cb684f67",
            "getOracleIds(uint256,uint256)": "acf0ebbb",
            "getOracleInfo(bytes32)": "eeb8e988",
            "getRoleAdmin(bytes32)": "248a9ca3",
            "getSlotLayout(bytes32)": "38a43b07",
            "getSlotOccupancy(address,uint8)": "50e2230f",
            "grantRole(bytes32,address)": "2f2ff15d",
            "hasRole(bytes32,address)": "91d14854",
            "isFeedActive(bytes32)": "790d7aae",
```
