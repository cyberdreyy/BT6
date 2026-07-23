### Title
Pusher Delegation Revocation Bypassed by Signature Replay Within Deadline Window - (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

### Summary
`allowPushers` contains no nonce or used-signature tracking. A creator who holds a pusher's signed consent can replay that exact signature an unlimited number of times within the deadline window, re-establishing delegation immediately after the pusher calls `revokePusher()`. The pusher's revocation is therefore not final, and the creator can silently redirect the pusher's price writes back into the creator's namespace against the pusher's explicit will.

### Finding Description

`allowPushers` verifies a pusher's EIP-191 consent signature and writes `namespaceRemapping[pusher] = msg.sender`: [1](#0-0) 

The signed message covers `(chainid, address(this), deadline, pusher, creator)`. The only replay guard is the deadline check: [2](#0-1) 

There is no nonce, no per-pusher revocation counter, and no consumed-signature set. `revokePusher` clears the mapping: [3](#0-2) 

But clearing the mapping does not invalidate the original signature. As long as `block.timestamp <= deadline`, the creator can call `allowPushers` again with the identical `(deadline, [pusher], [sig])` arguments, writing `namespaceRemapping[pusher] = creator` again. The protocol's own documentation acknowledges the concern ("an undated signature could re-establish a delegation AFTER the pusher revoked it") and cites the deadline as the mitigation, but the deadline only prevents replay *after* expiry — within the window it provides zero protection against repeated re-delegation. [4](#0-3) 

The `fallback` push path resolves the namespace at call time: [5](#0-4) 

So every push the pusher makes after the creator's replay lands in the creator's namespace, not the pusher's own.

### Impact Explanation

A pusher who revokes (e.g., because their signing key is suspected compromised, or because they want to stop providing data to a particular creator) cannot achieve a permanent revocation within the deadline window. The creator can immediately re-establish delegation. Any subsequent pushes from the pusher — including pushes the pusher believes are going to their own namespace — are silently redirected into the creator's namespace and consumed by pools that read from that namespace. This constitutes a bad-price execution path: a pusher who has explicitly withdrawn consent continues to feed prices into a creator's namespace and, transitively, into live pool swaps.

### Likelihood Explanation

The creator is a semi-trusted party who already holds the pusher's signed consent. Replaying it requires a single on-chain call with no additional privileges. The window is as long as the original deadline (commonly days to weeks). The pusher has no on-chain mechanism to permanently block re-delegation before the deadline expires.

### Recommendation

Track consumed signatures with a per-pusher nonce or a `mapping(bytes32 => bool) usedSignatures` set. Alternatively, include a per-pusher revocation counter in the signed message so that any revocation increments the counter and invalidates all previously issued signatures for that pusher/creator pair. Either approach ensures that `revokePusher()` permanently terminates the delegation represented by any previously issued signature.

### Proof of Concept

```solidity
// 1. Pusher signs consent with deadline = block.timestamp + 7 days
uint256 deadline = block.timestamp + 7 days;
bytes memory sig = pusher.sign(
    keccak256(abi.encode(chainid, oracle, deadline, pusherAddr, creatorAddr))
);

// 2. Creator establishes delegation
oracle.allowPushers(deadline, [pusherAddr], [sig]);
// namespaceRemapping[pusher] == creator  ✓

// 3. Pusher revokes — believes they are now pushing to their own namespace
vm.prank(pusherAddr);
oracle.revokePusher();
// namespaceRemapping[pusher] == address(0)  ✓

// 4. Creator replays the SAME signature (deadline still valid)
oracle.allowPushers(deadline, [pusherAddr], [sig]);
// namespaceRemapping[pusher] == creator  ← revocation undone

// 5. Pusher's next fallback push lands in creator's namespace, not their own
vm.prank(pusherAddr);
(bool ok,) = address(oracle).call(slotWord);
// oracle.getOracleData(feedIdOf(creator, slot, pos)).price == pusher's price
// oracle.getOracleData(feedIdOf(pusherAddr, slot, pos)).price == 0
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
