### Title
Pusher Revocation Can Be Silently Re-established Within Deadline Window via Signature Replay in `allowPushers` — (File: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol)

### Summary
`allowPushers` uses a deadline to prevent post-expiry signature replay, but there is no nonce or used-signature tracking. A creator who holds a pusher's signed consent can replay that exact signature any number of times before the deadline expires — including immediately after the pusher calls `revokePusher()` — silently re-establishing the delegation the pusher just cancelled.

### Finding Description
`allowPushers` signs over `(chainid, oracle, deadline, pusher, creator)` and enforces `block.timestamp <= deadline` via `_ensureDeadline`. No mapping of consumed signatures exists. [1](#0-0) 

The code comment itself acknowledges the risk:

> "The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it." [2](#0-1) 

The deadline prevents replay *after* it expires, but not *within* the window. `revokePusher` clears `namespaceRemapping[msg.sender]` to `address(0)`: [3](#0-2) 

Because the same `(deadline, pusher, creator)` tuple is still valid, the creator immediately calls `allowPushers` again with the identical signature bytes, writing `namespaceRemapping[pusher] = creator` again. The pusher's revocation is silently overridden.

The `fallback` push path resolves the namespace at call time: [4](#0-3) 

So every subsequent push from the pusher lands in the creator's namespace, not the pusher's own.

### Impact Explanation
After the creator re-establishes the delegation, the pusher's fallback pushes continue to populate the creator's namespace. The pusher's own namespace (`feedIdOf(pusher, slotIndex, positionIndex)`) receives no data. Any pool or `ProtectedPriceProvider` / `AnchoredPriceProvider` bound to the pusher's own feed IDs reads `timestampMs = 0`, which every staleness check treats as stale, causing `FeedStalled` on every `getBidAndAskPrice()` call and making swaps on that pool permanently unusable until the pusher notices and re-pushes under a new key. Meanwhile the creator's pool continues to receive live prices, giving the creator an asymmetric advantage.

### Likelihood Explanation
The creator already holds the pusher's signature from the initial `allowPushers` call — no additional off-chain interaction is needed. The only precondition is that the deadline has not yet expired. Creators are incentivised to use long deadlines (e.g. 1 day) to avoid operational friction, giving them a large replay window. The pusher has no on-chain way to detect that the delegation was re-established after their `revokePusher` transaction.

### Recommendation
Track consumed signatures with a `mapping(bytes32 => bool) usedSignatures` keyed on the EIP-191 hash, and revert if the hash has already been used. Alternatively, include a per-pusher monotonic nonce in the signed message and store the last-used nonce so each consent can only be exercised once.

### Proof of Concept
```
// 1. Pusher signs consent for creator, deadline = now + 1 day
bytes memory sig = sign(PUSHER_KEY, keccak256(abi.encode(chainid, oracle, deadline, pusher, creator)));

// 2. Creator establishes delegation
oracle.allowPushers(deadline, [pusher], [sig]);
// namespaceRemapping[pusher] == creator  ✓

// 3. Pusher revokes
vm.prank(pusher);
oracle.revokePusher();
// namespaceRemapping[pusher] == address(0)  ✓

// 4. Creator replays the SAME signature — still within deadline
vm.prank(creator);
oracle.allowPushers(deadline, [pusher], [sig]);
// namespaceRemapping[pusher] == creator  ← revocation silently undone

// 5. Pusher's next fallback push lands in creator's namespace, not pusher's own
vm.prank(pusher);
(bool ok,) = address(oracle).call(wordAt(slotId, pos, raw, tsMs));
// oracle.getOracleData(feedIdOf(creator, slotId, pos)).price > 0  ← creator's pool fed
// oracle.getOracleData(feedIdOf(pusher,   slotId, pos)).price == 0 ← pusher's pool starved → FeedStalled
```

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L186-191)
```text
    /// @notice Delegates pusher wallets into the caller's namespace. The pusher's EIP-191
    ///         signature is REQUIRED — without it anyone could remap a foreign pusher
    ///         wallet into their own namespace and silently swallow its pushes. The
    ///         deadline is likewise required: the signed consent carries no timestamp of
    ///         its own, so an undated signature could re-establish a delegation AFTER the
    ///         pusher revoked it.
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L192-211)
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
