### Title
`revokePusher` Revocation Bypassed via Signature Replay — Creator Re-establishes Delegation Before Deadline Expires - (File: `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` does not track whether a pusher has already revoked their delegation. A creator who holds a still-valid (pre-deadline) pusher signature can call `allowPushers` a second time after the pusher calls `revokePusher`, silently re-establishing the mapping. The pusher's self-revocation is therefore ineffective for the entire remaining lifetime of the signed deadline, redirecting the pusher's price updates away from their own namespace and starving any pool that reads from the pusher's own feeds.

---

### Finding Description

`revokePusher` clears the mapping: [1](#0-0) 

```solidity
function revokePusher() external {
    address creator = namespaceRemapping[msg.sender];
    if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
    namespaceRemapping[msg.sender] = address(0);
    emit PusherRevoked(msg.sender, creator);
}
```

`allowPushers` only validates two things — that the deadline has not expired and that the ECDSA signature is authentic — before unconditionally overwriting `namespaceRemapping[pusher]`: [2](#0-1) 

```solidity
function allowPushers(uint256 deadline, address[] calldata pushers, bytes[] memory signatures) external {
    _ensureDeadline(deadline);
    ...
    require(pusher == ECDSA.recover(hash, signatures[i]));
    namespaceRemapping[pusher] = msg.sender;   // ← overwrites the cleared mapping
    emit PusherAuthorized(pusher, msg.sender);
}
```

There is no nonce, no "revoked" flag, and no check that `namespaceRemapping[pusher]` is currently `address(0)`. The signed message commits only to `(chainid, oracle, deadline, pusher, creator)`: [3](#0-2) 

Because the signature carries no revocation state, the creator can replay the identical bytes any number of times before `deadline` elapses.

The code comment acknowledges the replay concern but only for the post-expiry case: [4](#0-3) 

> *"an undated signature could re-establish a delegation AFTER the pusher revoked it"*

The pre-expiry window — which can be hours or days — is left unguarded.

---

### Impact Explanation

Once the creator re-establishes the mapping, the `fallback` push path resolves the namespace from `namespaceRemapping[msg.sender]`: [5](#0-4) 

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
```

Every slot word the pusher sends is written into the **creator's** namespace, not the pusher's own. The pusher's own feeds receive no updates. Any `PriceProvider` or `AnchoredPriceProvider` bound to a feed in the pusher's namespace will observe a timestamp that no longer advances. The staleness check: [6](#0-5) 

```solidity
if (_isStale(refTime, block.timestamp, MAX_TIME_DELTA)) {
    return (0, type(uint128).max);
}
```

returns the stall sentinel, causing `getBidAndAskPrice` to revert with `FeedStalled`: [7](#0-6) 

Every pool that relies on those feeds becomes unable to execute swaps for the entire window until the deadline expires and the pusher can permanently revoke.

---

### Likelihood Explanation

- The pusher must have previously signed a consent message with a future deadline (standard operational practice for any long-running pusher bot).
- The creator needs only to replay the same calldata they used in the original `allowPushers` call — no new signature is required.
- The pusher has no on-chain mechanism to invalidate the outstanding signature short of waiting for the deadline.
- A creator who loses trust with a pusher (or a compromised creator key) can exploit this to lock the pusher's own feeds into a stale state for the full deadline window.

---

### Recommendation

Introduce a per-pusher revocation nonce or a boolean `revokedPushers` mapping. Before writing `namespaceRemapping[pusher] = msg.sender`, verify that the pusher has not self-revoked since the signature was issued:

```solidity
mapping(address => uint256) public pusherNonce; // incremented on revokePusher

// In allowPushers: include pusherNonce[pusher] in the signed hash
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender, pusherNonce[pusher]))

// In revokePusher: increment the nonce
pusherNonce[msg.sender]++;
namespaceRemapping[msg.sender] = address(0);
```

This makes every previously issued signature invalid the moment the pusher revokes, regardless of the deadline.

---

### Proof of Concept

```
1. Pusher signs: keccak256(abi.encode(chainid, oracle, deadline=T+1day, pusher, creator))
2. Creator calls allowPushers(T+1day, [pusher], [sig])
   → namespaceRemapping[pusher] = creator  ✓

3. Pusher calls revokePusher()
   → namespaceRemapping[pusher] = address(0)  ✓ (pusher believes they are free)

4. Creator immediately calls allowPushers(T+1day, [pusher], [sig])  ← same calldata
   → _ensureDeadline passes (T+1day still in future)
   → ECDSA.recover returns pusher  (signature unchanged, still valid)
   → namespaceRemapping[pusher] = creator  ← revocation silently overwritten

5. Pusher's subsequent fallback pushes land in creator's namespace.
   Pusher's own feeds (feedIdOf(pusher, slotIndex, positionIndex)) receive no updates.
   After MAX_TIME_DELTA seconds, PriceProvider._isStale() returns true.
   getBidAndAskPrice() reverts FeedStalled on every pool bound to pusher's feeds.
   Swaps on those pools are blocked until deadline T+1day expires.
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L315-316)
```text
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;
```

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L118-120)
```text
        (bid, ask) = _getBidAndAskPrice();
        if (bid == 0 || ask == type(uint128).max) revert FeedStalled();
    }
```

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L197-199)
```text
        // 2. Staleness check
        if (_isStale(refTime, block.timestamp, MAX_TIME_DELTA)) {
            return (0, type(uint128).max);
```
