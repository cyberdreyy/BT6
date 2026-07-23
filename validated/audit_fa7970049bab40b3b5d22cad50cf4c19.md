### Title
`revokePusher()` Self-Revocation Is Ineffective Within the Deadline Window Due to Creator Signature Replay — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

A pusher who calls `revokePusher()` to self-revoke their delegation can have the revocation immediately undone by the creator replaying the original `allowPushers` consent signature (which remains cryptographically valid until the deadline expires). Because there is no nonce or one-time-use guard on the consent signature, the creator can re-establish `namespaceRemapping[pusher] = creator` an unlimited number of times within the deadline window. While delegated, the pusher's fallback pushes are unconditionally redirected to the creator's namespace, so the pusher cannot update their own feeds. Any pool whose `AnchoredPriceProvider` reads a feed in the pusher's own namespace will receive a stale price and halt.

---

### Finding Description

`allowPushers` verifies a pusher's EIP-191 consent signature whose preimage is:

```
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
``` [1](#0-0) 

There is no nonce, no consumed-flag, and no per-pusher revocation counter. The only replay bound is the deadline itself. The code comment acknowledges the risk:

> *"the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."* [2](#0-1) 

However, the deadline only caps the replay window — it does not prevent replay **within** the window. After `revokePusher()` clears `namespaceRemapping[pusher]` to `address(0)`: [3](#0-2) 

…the creator can immediately call `allowPushers` again with the identical `(deadline, pusher, signature)` tuple. The hash is identical, ECDSA recovery succeeds, and `namespaceRemapping[pusher]` is written back to `creator`. This cycle can repeat indefinitely until `block.timestamp > deadline`.

While `namespaceRemapping[pusher] == creator`, the fallback push path resolves the namespace to the creator's address: [4](#0-3) 

Every slot word the pusher sends lands in the creator's namespace. The pusher's own namespace (identified by `feedIdOf(pusher, slotIndex, positionIndex)`) receives no updates.

---

### Impact Explanation

When a pusher is also a creator of their own feeds that an `AnchoredPriceProvider` reads, the delegation lock causes those feeds to go stale. `_readLeg` in `AnchoredPriceProvider` rejects any reference whose age exceeds `MAX_REF_STALENESS`: [5](#0-4) 

A stale reference causes `_getBidAndAskPrice` to return `(0, type(uint128).max)`, which `getBidAndAskPrice` surfaces as `FeedStalled`: [6](#0-5) 

Every swap through any pool bound to that provider reverts for the entire duration of the deadline window. The pusher cannot escape the delegation to resume updating their own feeds until the deadline expires — which may be hours or days away depending on the deadline chosen at consent time.

---

### Likelihood Explanation

- The consent signature is intentionally permissionless and off-chain; long deadlines (e.g., 7 days) are operationally normal.
- Any creator who depends on a pusher's data has an economic incentive to prevent the pusher from revoking.
- The replay requires no special privilege — the creator already holds the original signed bytes from the initial delegation flow.
- The pusher's only mitigation is to stop pushing entirely, which itself causes the creator's feeds to go stale and the creator's pools to halt — a mutual-harm outcome that does not restore the pusher's own feeds.

---

### Recommendation

Add a per-pusher revocation nonce to the consent signature preimage and increment it on every successful `allowPushers` call (or on every `revokePusher` / `removePushers` call). A consumed-signature bitmap keyed on `keccak256(signature)` is a simpler alternative. Either approach ensures that once a pusher revokes, the original consent bytes are permanently invalidated and cannot be replayed by the creator.

```solidity
// Example: nonce-based invalidation
mapping(address => uint256) public pusherNonce;

// In allowPushers:
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline,
                         pusher, msg.sender, pusherNonce[pusher]))
);
pusherNonce[pusher]++;   // invalidates all prior signatures for this pusher

// In revokePusher / removePushers: also increment pusherNonce[pusher]
```

---

### Proof of Concept

```
T=0   Pusher P signs consent for creator C with deadline = T+7days
T=0   C calls allowPushers(T+7days, [P], [sig])
        → namespaceRemapping[P] = C
        → P's fallback pushes land in C's namespace; P's own feeds receive no updates

T=1   P calls revokePusher()
        → namespaceRemapping[P] = address(0)

T=1   C calls allowPushers(T+7days, [P], [sig])   ← same bytes, still valid
        → namespaceRemapping[P] = C  (re-established)

T=2   P calls revokePusher() again → cleared
T=2   C replays again → re-established
      ... repeats until T+7days

      During this entire window:
        - feedIdOf(P, slotIndex, positionIndex) timestamp stays at T=0 (stale)
        - AnchoredPriceProvider._readLeg returns ok=false (stale reference)
        - getBidAndAskPrice() reverts FeedStalled
        - All swaps through pools bound to P's feeds are blocked
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L204-210)
```text
            bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
                keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
            );
            require(pusher == ECDSA.recover(hash, signatures[i]));

            namespaceRemapping[pusher] = msg.sender;
            emit PusherAuthorized(pusher, msg.sender);
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L331-344)
```text
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

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L214-217)
```text
    function getBidAndAskPrice() external override returns (uint128 bid, uint128 ask) {
        (bid, ask) = _getBidAndAskPrice();
        if (bid == 0 || ask == type(uint128).max) revert FeedStalled();
    }
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L282-283)
```text
        // Stale reference → not ok. Clamping to a stale anchor is the one false-safety case.
        if (_isStale(refTime, block.timestamp, MAX_REF_STALENESS)) return (mid, spreadBps, refTime, false);
```
