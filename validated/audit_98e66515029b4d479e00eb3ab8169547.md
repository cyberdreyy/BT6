### Title
`CompressedOracleV1.updateBySignature` Missing Deadline Allows Stale Signed Price Updates to Reach Pool Swaps - (File: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol)

### Summary

`CompressedOracleV1.updateBySignature` is a permissionless function that accepts a creator-signed slot word and writes it to oracle storage. The function has **no deadline parameter**, meaning a signed price update can be submitted by anyone at any future time. If the signed update sits in the mempool while the real market price moves, the stale price is eventually written to the oracle and consumed by pools during live swaps.

### Finding Description

The outer documentation, the ABI registry, and the inner docs contradict each other on whether `updateBySignature` requires a deadline.

**Outer docs** (`smart-contracts-poc/docs/en/oracle-packet-structure.md` lines 37, 45) specify:
```
updateBySignature(feedCreator, deadline, newSlotValue, signature)
keccak256(abi.encode(chainid, oracleAddress, feedCreator, deadline, newSlotValue))
- deadline must be in the future (DeadlineExceeded otherwise)
``` [1](#0-0) 

**ABI registry** (`smart-contracts-poc/contract-registry/versions/registry.json` line 4208) registers the function as the 4-parameter variant:
```json
"updateBySignature(address,uint256,uint256,bytes)": "78ce3ae1"
``` [2](#0-1) 

**Actual deployed source code** (`CompressedOracle.sol` lines 268–303) implements only a 3-parameter function with **no deadline**:

```solidity
/// @notice Single-slot update authorized by the creator's signature. The signed slot
///         word carries its own 56-bit timestamp, so replay is neutralized by the
///         monotonicity check below — no deadline is needed.
function updateBySignature(address feedCreator, uint256 newSlotValue, bytes calldata signature)
    external
    override
    returns (bool)
{
    ...
    bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
        keccak256(abi.encode(block.chainid, address(this), feedCreator, newSlotValue))
    );
    require(feedCreator == ECDSA.recover(hash, signature));
    _writeStorage(key, bytes32(newSlotValue & ~uint256(0xff)));
    return true;
}
``` [3](#0-2) 

The code's rationale is that the 56-bit timestamp embedded in the slot word provides replay protection via the monotonicity check:

```solidity
bool newer = timestampMs.isAfter(oldTimestampMs);
if (!newer) { return false; }
``` [4](#0-3) 

**The gap in this reasoning**: monotonicity only rejects updates whose embedded timestamp is ≤ the currently stored timestamp. If the creator has not yet pushed a newer update, the old signed word's timestamp is still "newer" than storage, so the stale price is accepted regardless of how much wall-clock time has elapsed since signing. The `FutureTimestamp` guard only rejects timestamps that exceed `block.timestamp + MAX_TIME_DRIFT`; since the signed timestamp was set at signing time T_sign ≤ T_exec, this guard is always satisfied at execution time.

By contrast, `allowPushers` — which also uses EIP-191 signatures — explicitly requires a deadline and the code comment explains exactly why:

> "The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it." [5](#0-4) 

The same temporal-validity problem applies to `updateBySignature`: without a deadline, a signed price update is valid indefinitely.

### Impact Explanation

A stale signed price update reaching oracle storage is directly consumed by `PriceProvider` / `PriceProviderL2` / `AnchoredPriceProvider` via `getBidAndAskPrice()`, which reads the oracle's stored price and timestamp:

```solidity
(uint256 mid, uint256 spread, , uint256 refTime) =
    IPricedOracle(address(offchainOracle)).price(offchainFeedId, msg.sender);
if (_isStale(refTime, block.timestamp, MAX_TIME_DELTA, FUTURE_TOLERANCE)) {
    return (0, type(uint128).max);
}
``` [6](#0-5) 

The staleness check uses `MAX_TIME_DELTA` (seconds since the stored `refTime`). A signed update that was delayed in the mempool carries a timestamp close to signing time T_sign. When it is eventually written, `refTime = T_sign` and `block.timestamp = T_exec >> T_sign`, so the staleness check may immediately reject it — **but only if `T_exec - T_sign > MAX_TIME_DELTA`**. If the delay is shorter than `MAX_TIME_DELTA` (e.g., a few minutes on a network with a 60-second drift window), the stale price passes the staleness check and reaches the pool's bid/ask computation, causing bad-price execution for every swap in that block.

### Likelihood Explanation

`updateBySignature` is permissionless — any address can submit a valid signed word. A creator who signs a slot word and broadcasts it with low gas may not realize the transaction is pending. A MEV searcher or any observer can hold the signed calldata and submit it at a strategically chosen moment (e.g., when the real price has moved significantly but the delay is still within `MAX_TIME_DELTA`). The `CompressedOracleV1` is the oracle backing production pools, so any accepted stale price directly affects live swap settlement.

### Recommendation

Add a `deadline` parameter to `updateBySignature`, include it in the signed hash, and enforce it before writing storage — exactly as the outer documentation and ABI registry already specify:

```solidity
function updateBySignature(
    address feedCreator,
    uint256 deadline,          // ← add
    uint256 newSlotValue,
    bytes calldata signature
) external override returns (bool) {
    _ensureDeadline(deadline); // ← add
    ...
    bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
        keccak256(abi.encode(block.chainid, address(this), feedCreator, deadline, newSlotValue))
    );
    ...
}
```

This matches the documented interface, the ABI registry entry, and the precedent set by `allowPushers`.

### Proof of Concept

1. Creator signs a slot word encoding price P1 at timestamp T1 (current block time × 1000 ms).
2. The signed calldata is broadcast with low gas; it sits in the mempool unincluded.
3. Real market price moves from P1 to P2 (e.g., 15% drop). The creator does not push a newer update via `fallback()`.
4. After `D` seconds (where `D < MAX_TIME_DELTA`), a searcher submits the old signed calldata to `updateBySignature(creator, oldSlotValue, sig)`.
5. Monotonicity check: stored timestamp is 0 (or T0 < T1) → `newer = true` → write proceeds.
6. `FutureTimestamp` check: T1 ≤ T_exec + MAX_TIME_DRIFT → passes.
7. Oracle storage now holds price P1 with timestamp T1.
8. `PriceProviderL2._getBidAndAskPrice()` reads `refTime = T1/1000`. Staleness: `T_exec - T1/1000 = D < MAX_TIME_DELTA` → not stale → P1 is returned as the live mid price.
9. Pool executes swaps at bid/ask derived from P1, not the real price P2. Traders receive incorrect amounts; the pool's conservation invariant is violated by the spread of P1 vs P2.

### Citations

**File:** smart-contracts-poc/docs/en/oracle-packet-structure.md (L37-45)
```markdown
`updateBySignature(feedCreator, deadline, newSlotValue, signature)` expects `newSlotValue` to be a single slot word (same layout), signed by `feedCreator` over:

```text
keccak256(abi.encode(chainid, oracleAddress, feedCreator, deadline, newSlotValue))
```

## Required Guards

- `deadline` must be in the future (`DeadlineExceeded` otherwise).
```

**File:** smart-contracts-poc/contract-registry/versions/registry.json (L4208-4208)
```json
            "updateBySignature(address,uint256,uint256,bytes)": "78ce3ae1"
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L186-212)
```text
    /// @notice Delegates pusher wallets into the caller's namespace. The pusher's EIP-191
    ///         signature is REQUIRED — without it anyone could remap a foreign pusher
    ///         wallet into their own namespace and silently swallow its pushes. The
    ///         deadline is likewise required: the signed consent carries no timestamp of
    ///         its own, so an undated signature could re-establish a delegation AFTER the
    ///         pusher revoked it.
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L268-303)
```text
    /// @notice Single-slot update authorized by the creator's signature. The signed slot
    ///         word carries its own 56-bit timestamp, so replay is neutralized by the
    ///         monotonicity check below — no deadline is needed.
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

**File:** smart-contracts-poc/contracts/PriceProviderL2.sol (L208-217)
```text
    function _getBidAndAskPrice() internal returns (uint128, uint128) {
        // 1. Read via the unified price(feedId, pool) path, forwarding the pool (msg.sender).
        //    refTime is already in seconds.
        (uint256 mid, uint256 spread, , uint256 refTime) =
            IPricedOracle(address(offchainOracle)).price(offchainFeedId, msg.sender);

        // 2. Staleness check
        if (_isStale(refTime, block.timestamp, MAX_TIME_DELTA, FUTURE_TOLERANCE)) {
            return (0, type(uint128).max);
        }
```
