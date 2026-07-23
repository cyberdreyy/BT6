### Title
Signed Delegation Replay After `revokePusher` Permanently Locks Pusher in Creator Namespace Until Deadline — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

In `CompressedOracleV1`, a pusher who has signed a delegation message with a future deadline cannot permanently escape the creator's namespace by calling `revokePusher()`. The creator can immediately replay the original signed delegation (still valid until the deadline) to re-establish the mapping. This is the direct Metric OMM analog of the SKALE "stuck delegation" bug: the pusher (like a delegator) cannot switch to their own namespace (like a different validator) until the deadline expires, because the only revocation mechanism (`revokePusher`) is undone by the creator replaying the same signature.

---

### Finding Description

`allowPushers` accepts a pusher's EIP-191 signature over the tuple `(block.chainid, address(this), deadline, pusher, creator)`. [1](#0-0) 

The **only** replay protection is the deadline: once `block.timestamp <= deadline`, the same signature is unconditionally valid. There is no nonce, no one-time-use flag, and no mechanism to invalidate a signed delegation before its deadline.

`revokePusher` clears the mapping: [2](#0-1) 

But immediately after the pusher calls `revokePusher()`, the creator can call `allowPushers` again with the **identical** signature and deadline. Because `_ensureDeadline` only checks `block.timestamp <= deadline` and the signature verification is stateless, the delegation is silently re-established: [3](#0-2) 

The pusher is locked in the creator's namespace for the full remaining lifetime of the signed message — potentially hours or days depending on the deadline chosen at signing time.

---

### Impact Explanation

While re-delegated against their will, all `fallback()` pushes from the pusher are routed to the creator's namespace: [4](#0-3) 

The pusher's **own** namespace feeds receive no updates via the `fallback()` path. Any pool whose `PriceProvider` or `AnchoredPriceProvider` is bound to a `feedId` in the pusher's own namespace will read a stale `timestampMs`. The staleness check in the provider layer: [5](#0-4) 

returns `(0, type(uint128).max)`, causing `getBidAndAskPrice()` to revert with `FeedStalled()` and halting all swaps in those pools for the duration of the lock.

The pusher can use `updateBySignature` as a workaround to push to their own namespace, but this path requires signing each slot update individually (one ECDSA signature per slot per update), which is operationally incompatible with automated batch-push infrastructure that relies on the `fallback()` path. [6](#0-5) 

---

### Likelihood Explanation

Medium. The scenario requires: (1) a pusher who signed a delegation with a non-trivial future deadline (common in production where long-lived keys are used), and (2) a creator who is adversarial or economically motivated to retain the pusher's data stream. The creator's cost is a single transaction per revocation attempt; the pusher has no on-chain defense until the deadline expires.

---

### Recommendation

Introduce a per-pusher revocation nonce stored in the contract. Include the nonce in the signed message and increment it on every successful `revokePusher()` call. Any previously signed delegation (carrying the old nonce) becomes permanently invalid after revocation, regardless of its deadline.

```solidity
mapping(address => uint256) public pusherNonce;

// In allowPushers:
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender, pusherNonce[pusher]))

// In revokePusher:
pusherNonce[msg.sender]++;
namespaceRemapping[msg.sender] = address(0);
```

---

### Proof of Concept

```
T=0:  Pusher signs: keccak256(abi.encode(chainid, oracle, deadline=T+1h, pusher, creator))
T=1:  Creator calls allowPushers(T+1h, [pusher], [sig])
        → namespaceRemapping[pusher] = creator  ✓
T=2:  Pusher calls revokePusher()
        → namespaceRemapping[pusher] = address(0)  ✓
T=3:  Creator calls allowPushers(T+1h, [pusher], [sig])  // SAME sig, deadline still valid
        → namespaceRemapping[pusher] = creator  ← pusher re-locked
T=4:  Pusher's fallback() pushes → creator's namespace (pusher's own feeds go stale)
T=5:  Pool reads pusher's feedId → timestampMs stale → FeedStalled() → swaps halt
...
T=1h: Deadline expires — pusher can finally escape
```

### Citations

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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L238-243)
```text
    function revokePusher() external {
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
        namespaceRemapping[msg.sender] = address(0);
        emit PusherRevoked(msg.sender, creator);
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L315-316)
```text
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;
```

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L197-200)
```text
        // 2. Staleness check
        if (_isStale(refTime, block.timestamp, MAX_TIME_DELTA)) {
            return (0, type(uint128).max);
        }
```
