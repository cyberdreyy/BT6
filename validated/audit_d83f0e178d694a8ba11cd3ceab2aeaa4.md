### Title
`allowPushers` consent signature has no nonce, allowing creator to replay a revoked delegation within the deadline window — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracleV1.allowPushers` accepts a pusher's EIP-191 consent signature and sets `namespaceRemapping[pusher] = creator`. The function's only replay guard is a deadline check (`block.timestamp <= deadline`). There is no nonce, no used-signature bitmap, and no per-pusher revocation counter. A creator who holds a still-valid signature can call `allowPushers` again with the identical arguments immediately after the pusher calls `revokePusher()`, atomically re-establishing the delegation. The pusher's self-revocation is therefore ineffective for the entire remaining lifetime of the deadline window.

---

### Finding Description

The code comment on `allowPushers` explicitly acknowledges the threat:

> "The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."

The comment implies the deadline solves the problem. It does not. The deadline only prevents replay *after* it expires; within the window it provides zero protection against re-establishment. [1](#0-0) 

The signature commits to `(block.chainid, address(this), deadline, pusher, msg.sender)`. None of those fields change between the original call and a replay. There is no nonce field, no `usedSignatures` mapping, and no per-pusher revocation counter anywhere in the contract. [2](#0-1) 

`revokePusher` clears `namespaceRemapping[msg.sender]` to `address(0)`: [3](#0-2) 

But the creator can immediately call `allowPushers` again with the same `(deadline, [pusher], [sig])` tuple. `_ensureDeadline` passes (deadline has not expired), ECDSA recovery succeeds (the signature is still cryptographically valid), and `namespaceRemapping[pusher]` is set back to `creator`. The pusher's revocation is undone in a single transaction.

The `fallback` push path resolves the namespace at call time: [4](#0-3) 

So every subsequent push the pusher makes — even after calling `revokePusher()` — lands in the creator's namespace as long as the creator keeps replaying the delegation.

---

### Impact Explanation

**Admin-boundary break / broken oracle-write authority revocation.**

A pusher who wants to stop writing into a creator's namespace (e.g., because the creator is malicious, the pusher's key is at risk, or the pusher is switching to a different operator) cannot do so until the deadline the creator chose expires. During that window:

1. Every push the pusher makes is silently redirected to the creator's namespace instead of the pusher's own namespace.
2. The pusher's own namespace feeds remain at their last value (stale). Any pool backed by a `feedIdOf(pusher, …)` feed will see a stale oracle and halt swaps (`FeedStalled`).
3. The creator's pool continues to receive live price updates from a pusher who has explicitly tried to revoke consent — a direct violation of the documented revocation guarantee.

The slot-structure documentation states the invariant: "Delegation clean-up is a public surface because any stale remapping after revoke is effectively latent write authority." [5](#0-4) 

That invariant is broken: the remapping is not cleaned up — it is immediately restored.

---

### Likelihood Explanation

**High.** The creator already holds the pusher's signed consent (they needed it to call `allowPushers` the first time). Replaying it costs one transaction and zero additional off-chain work. The window can be as long as the creator chose when they originally set the deadline (the code imposes no upper bound on `deadline`). Any creator who wants to retain a pusher against the pusher's will can do so trivially.

---

### Recommendation

Track consumed consent signatures with a per-pusher nonce or a `usedSignatures` mapping so that each signed consent can only establish one delegation:

```solidity
// Add to storage:
mapping(address => uint256) public pusherNonce;

// In allowPushers, include the nonce in the signed message:
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(
        block.chainid, address(this), deadline,
        pusherNonce[pusher],   // <-- binds to current revocation count
        pusher, msg.sender
    ))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
pusherNonce[pusher]++;         // invalidate this consent on use
namespaceRemapping[pusher] = msg.sender;
```

`revokePusher` and `removePushers` already clear `namespaceRemapping`; incrementing `pusherNonce` there as well ensures any previously issued consent is invalidated even if the deadline has not expired.

---

### Proof of Concept

```solidity
// 1. Creator obtains pusher's signed consent (deadline = now + 1 day).
bytes memory sig = _signConsent(PUSHER_KEY, deadline, pusher, creator);
address[] memory pushers = new address[](1); pushers[0] = pusher;
bytes[] memory sigs    = new bytes[](1);    sigs[0]    = sig;

// 2. Creator establishes delegation.
vm.prank(creator);
oracle.allowPushers(deadline, pushers, sigs);
assertEq(oracle.namespaceRemapping(pusher), creator); // delegated

// 3. Pusher revokes.
vm.prank(pusher);
oracle.revokePusher();
assertEq(oracle.namespaceRemapping(pusher), address(0)); // cleared

// 4. Creator replays the SAME call — deadline still valid, sig still valid.
vm.prank(creator);
oracle.allowPushers(deadline, pushers, sigs);           // succeeds — no revert
assertEq(oracle.namespaceRemapping(pusher), creator);   // delegation restored!

// 5. Pusher's next push still lands in creator's namespace, not their own.
vm.prank(pusher);
(bool ok,) = address(oracle).call(_wordAt(0, 0, _packRaw(999_000, 1, 1), tsMs));
assertTrue(ok);
assertEq(oracle.getOracleData(oracle.feedIdOf(creator, 0, 0)).price,
         U64x32.decode(999_000), "push went to creator — revocation was ineffective");
assertEq(oracle.getOracleData(oracle.feedIdOf(pusher,  0, 0)).price, 0,
         "pusher's own namespace is still empty");
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L315-316)
```text
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;
```

**File:** smart-contracts-poc/contracts/oracles/compressed/OracleBase.sol (L124-126)
```text
    function _ensureDeadline(uint256 deadline) internal view virtual {
        require(block.timestamp <= deadline, DeadlineExceeded());
    }
```

**File:** smart-contracts-poc/contracts/oracles/compressed/docs/en/slot-structure.md (L25-29)
```markdown
A wallet writes into `namespaceRemapping[msg.sender]`'s namespace, falling back to its
**own** namespace when no delegation is set — a creator needs zero setup transactions.
Delegation (`allowPushers`) requires each pusher's EIP-191 signature (and a deadline:
the signed consent has no data timestamp, so an undated signature could re-establish a
delegation after the pusher revoked it).
```
