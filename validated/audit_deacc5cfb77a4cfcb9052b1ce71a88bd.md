### Title
Creator Can Re-Establish Revoked Pusher Delegation Using Original Signature, Nullifying `revokePusher()` Before Deadline Expiry — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` in `CompressedOracleV1` verifies a pusher's EIP-191 signature and sets `namespaceRemapping[pusher] = creator`. After a pusher calls `revokePusher()`, the creator can immediately replay the original signature (deadline still valid) to re-establish the delegation. The code comment acknowledges the risk but the deadline-only fix is insufficient: it prevents re-establishment only *after* the deadline expires, not before.

---

### Finding Description

`allowPushers` signs over `(chainid, oracle, deadline, pusher, creator)` and writes `namespaceRemapping[pusher] = creator`: [1](#0-0) 

`revokePusher` clears the mapping to `address(0)`: [2](#0-1) 

Because the signature domain contains no revocation nonce or one-time-use marker, the creator retains the original bytes and can call `allowPushers` a second time with the identical `(deadline, pusher, sig)` tuple. `_ensureDeadline` passes (deadline not yet expired), ECDSA recovery succeeds, and `namespaceRemapping[pusher]` is written back to `creator`. The pusher's revocation is silently undone.

The code comment at line 189–191 explicitly acknowledges the concern ("an undated signature could re-establish a delegation AFTER the pusher revoked it") but treats the deadline as the complete fix. The deadline only bounds the *outer* window; within that window the signature is fully replayable. [3](#0-2) 

The `fallback` push path resolves the namespace at call time: [4](#0-3) 

So every push the pusher makes after believing they have revoked still lands in the creator's namespace. The `feedIdOf` layout encodes `(creator, chainid, slotIndex, positionIndex)`: [5](#0-4) 

Pools that read `price(feedIdOf(creator, slot, pos), pool)` therefore receive whatever the pusher is now publishing — which may be prices for a completely different asset if the pusher has moved on to a new client. [6](#0-5) 

---

### Impact Explanation

A pusher who has revoked and begun publishing prices for a different feed (different asset, different creator) has those prices silently redirected into the original creator's namespace. Any pool that reads `price(feedIdOf(creator, …))` during a swap receives the wrong mid/spread values. This is a **bad-price execution** path: the pool's swap math consumes an inverted or wrong-asset quote, causing the pool to give traders more output than the correct price permits (pool insolvency) or to settle at a price the trader did not agree to.

---

### Likelihood Explanation

- The creator retains the original signature bytes off-chain (they submitted the transaction; the calldata is public).
- The deadline is typically set days or weeks in the future (the test suite uses `block.timestamp + 1 days`).
- The creator has a direct economic incentive to keep the pusher's price stream flowing into their namespace.
- No privileged role is required; `allowPushers` is fully permissionless for any `msg.sender` who holds a valid signature. [7](#0-6) 

---

### Recommendation

Introduce a per-pusher revocation nonce and include it in the signed digest. Increment the nonce inside `revokePusher` (and `removePushers`). Old signatures become invalid immediately after revocation regardless of deadline.

```solidity
// storage
mapping(address => uint256) public pusherNonce;

// allowPushers — add nonce to hash
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(
        block.chainid, address(this), deadline,
        pusher, msg.sender,
        pusherNonce[pusher]   // <-- new
    ))
);

// revokePusher — invalidate outstanding signatures
function revokePusher() external {
    address creator = namespaceRemapping[msg.sender];
    if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
    pusherNonce[msg.sender]++;          // <-- new
    namespaceRemapping[msg.sender] = address(0);
    emit PusherRevoked(msg.sender, creator);
}
```

---

### Proof of Concept

```
1. Pusher signs consent for Creator with deadline = now + 30 days.
   sig = sign(keccak256(chainid, oracle, deadline, pusher, creator))

2. Creator calls allowPushers(deadline, [pusher], [sig])
   → namespaceRemapping[pusher] = creator  ✓

3. Pusher calls revokePusher()
   → namespaceRemapping[pusher] = address(0)  (pusher believes delegation is gone)

4. Creator calls allowPushers(deadline, [pusher], [sig])  ← same sig, deadline still valid
   → _ensureDeadline passes
   → ECDSA.recover returns pusher  ← same hash, same sig
   → namespaceRemapping[pusher] = creator  ← revocation silently undone

5. Pusher, now serving CreatorB, pushes ETH/USDC prices via fallback().
   → namespaceRemapping[pusher] == creator (CreatorA)
   → prices land in CreatorA's namespace (wrong asset)

6. Pool reads price(feedIdOf(CreatorA, slot, pos), pool)
   → receives ETH/USDC price instead of the expected asset price
   → swap executes at wrong bid/ask → pool insolvency or bad-price execution
```

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L49-53)
```text
    function feedIdOf(address creator, uint8 slotIndex, uint8 positionIndex) public view returns (bytes32) {
        return bytes32(
            uint256(uint160(creator)) << 96 | block.chainid << 16 | uint256(slotIndex) << 8 | positionIndex
        );
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L238-243)
```text
    function revokePusher() external {
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
        namespaceRemapping[msg.sender] = address(0);
        emit PusherRevoked(msg.sender, creator);
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

**File:** smart-contracts-poc/test/oracles/compressed/CompressedOracle.t.sol (L339-356)
```text
    function testAllowPushersDelegatesNamespace() public {
        uint256 deadline = block.timestamp + 1 days;
        _allowPusher(deadline);
        assertEq(oracle.namespaceRemapping(pusher), creator, "pusher should map to creator");

        // delegated push lands in the CREATOR namespace, not the pusher's own
        uint56 tsMs = uint56(block.timestamp * 1000);
        uint48 raw = _packRaw(900_000, 5, 0);
        vm.prank(pusher);
        (bool ok,) = address(oracle).call(_wordAt(2, 3, raw, tsMs));
        assertTrue(ok, "delegated push failed");

        IOffchainOracle.OracleData memory data = oracle.getOracleData(oracle.feedIdOf(creator, 2, 3));
        assertEq(data.price, U64x32.decode(uint32(raw >> 16)), "delegated push should land in creator namespace");

        IOffchainOracle.OracleData memory own = oracle.getOracleData(oracle.feedIdOf(pusher, 2, 3));
        assertEq(own.price, 0, "pusher's own namespace must stay empty");
    }
```
