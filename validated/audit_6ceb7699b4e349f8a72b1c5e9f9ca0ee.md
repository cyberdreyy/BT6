### Title
Pusher delegation signature replay allows creator to silently re-establish a revoked `namespaceRemapping` within the deadline window — (File: `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` has no nonce or used-signature tracking. After a pusher calls `revokePusher()` to clear `namespaceRemapping[pusher]`, the old creator can immediately replay the identical EIP-191 signature to re-establish the delegation, as long as `block.timestamp < deadline`. The code comment explicitly acknowledges the risk but incorrectly claims the deadline prevents it; the deadline only limits the outer time window, not replay within that window.

---

### Finding Description

`allowPushers` verifies a pusher's EIP-191 signature over the tuple `(block.chainid, address(this), deadline, pusher, msg.sender)` and unconditionally writes `namespaceRemapping[pusher] = msg.sender`. [1](#0-0) 

There is no nonce, no used-signature bitmap, and no check that `namespaceRemapping[pusher]` is currently `address(0)` before writing. The code comment at line 186–191 explicitly acknowledges the replay risk:

> "an undated signature could re-establish a delegation AFTER the pusher revoked it"

and claims the deadline is the mitigation. But the deadline only prevents replay after it expires; within the window `[now, deadline)` the same signature is unconditionally accepted on every call. [2](#0-1) 

`revokePusher` clears the mapping: [3](#0-2) 

But immediately after the pusher's `revokePusher()` transaction lands, the creator can call `allowPushers` with the exact same `(deadline, [pusher], [sig])` arguments and restore `namespaceRemapping[pusher] = creator`. The pusher's revocation is silently undone.

The `fallback` push path reads `namespaceRemapping[msg.sender]` at the top of every call: [4](#0-3) 

So any push the pusher sends after their (apparently successful) revocation still lands in the creator's namespace, not their own.

This is the direct analog to `safeApprove` without zeroing first: just as calling `safeApprove(spender, newAmount)` without first calling `safeApprove(spender, 0)` leaves the old approval silently in force, calling `allowPushers` without invalidating the old signature leaves the old delegation silently re-establishable.

---

### Impact Explanation

A pusher who revokes their delegation cannot effectively stop their pushes from landing in the creator's namespace until the deadline expires. Concretely:

1. The pusher cannot redirect their pushes to a different creator's namespace — any fallback call still resolves to the old creator.
2. The pusher's only effective option is to stop pushing entirely, which makes the creator's pool's prices stale and causes all swaps against that pool to revert (`FeedStalled` / `maxTimeDelta` exceeded).
3. If the pusher signed a long-lived deadline (e.g., 1 year), the creator can maintain write authority over the creator's namespace for the entire remaining window without the pusher's fresh consent.

The broken invariant is: **a pusher's `revokePusher()` call is supposed to be final and immediately effective**. The `generate_scanned_questions.py` audit pivot confirms this is the intended security boundary: [5](#0-4) 

---

### Likelihood Explanation

Medium. The creator must have retained the pusher's original signature bytes (trivially true — they submitted the original `allowPushers` transaction and the calldata is public on-chain). The creator must also be actively malicious. The window is bounded by the deadline, but deadlines are typically set days to months in the future.

---

### Recommendation

Add a per-pusher nonce to the signature domain and increment it on every successful `allowPushers` or `revokePusher` call:

```solidity
mapping(address => uint256) public pusherNonce;

// In allowPushers:
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(
        block.chainid, address(this), deadline,
        pusher, msg.sender,
        pusherNonce[pusher]   // <-- add nonce
    ))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
pusherNonce[pusher]++;        // <-- invalidate on use
namespaceRemapping[pusher] = msg.sender;

// In revokePusher:
pusherNonce[msg.sender]++;    // <-- invalidate any outstanding signature
namespaceRemapping[msg.sender] = address(0);
```

Alternatively, check that `namespaceRemapping[pusher] == address(0)` before writing, so re-establishment requires an explicit `removePushers` by the creator first (forcing an on-chain state transition that the pusher can observe and front-run).

---

### Proof of Concept

```solidity
// 1. Pusher signs consent for creator with deadline = now + 365 days
bytes memory sig = _signConsent(PUSHER_KEY, deadline, pusher, creator);

// 2. Creator establishes delegation
vm.prank(creator);
oracle.allowPushers(deadline, _arr(pusher), _arr(sig));
assertEq(oracle.namespaceRemapping(pusher), creator);

// 3. Pusher revokes
vm.prank(pusher);
oracle.revokePusher();
assertEq(oracle.namespaceRemapping(pusher), address(0)); // cleared

// 4. Creator replays the SAME signature — no revert, delegation restored
vm.prank(creator);
oracle.allowPushers(deadline, _arr(pusher), _arr(sig));
assertEq(oracle.namespaceRemapping(pusher), creator);    // ← back to creator

// 5. Pusher's next push still lands in creator's namespace
uint56 tsMs = uint56(block.timestamp * 1000 + 1);
vm.prank(pusher);
(bool ok,) = address(oracle).call(_wordAt(0, 0, _packRaw(999_999, 1, 1), tsMs));
assertTrue(ok);
// Lands in creator namespace, not pusher's own
assertGt(oracle.getOracleData(oracle.feedIdOf(creator, 0, 0)).price, 0);
assertEq(oracle.getOracleData(oracle.feedIdOf(pusher,  0, 0)).price, 0);
```

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L186-211)
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

**File:** generate_scanned_questions.py (L1013-1016)
```python
            call_path="public revoke/remove -> namespaceRemapping clear -> later fallback pushes revert to creator or self namespace",
            values="the namespace actually revoked, any surviving stale delegation, and whether later pushes still land in the old namespace",
            control_hint="Delegation clean-up is a public surface because any stale remapping after revoke is effectively latent write authority.",
            validation_focus="Exercise revoke/remove interleavings and assert no later public push can still write into a namespace that should have been detached.",
```
