### Title
`revokePusher()` Is Ineffective Within the Deadline Window Due to Missing Nonce in Delegation Signature — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` verifies an EIP-191 signature from the pusher wallet but includes no nonce in the signed payload — only a `deadline`. After a pusher calls `revokePusher()`, the creator can immediately replay the same unexpired signature to re-establish the delegation, making revocation impossible until the deadline passes.

---

### Finding Description

In `CompressedOracleV1.allowPushers`, the signed consent message is:

```solidity
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
``` [1](#0-0) 

There is no nonce, no used-signature bitmap, and no per-pusher revocation counter. The only replay guard is `_ensureDeadline(deadline)`. [2](#0-1) 

`revokePusher()` sets `namespaceRemapping[msg.sender] = address(0)`: [3](#0-2) 

But because the original signature is still valid (same `chainid`, same oracle address, same `deadline`, same `pusher`, same `creator`), the creator can call `allowPushers` again with the identical signature bytes in the very next block, atomically undoing the revocation. The pusher has no on-chain mechanism to invalidate the old signature before the deadline expires.

The code comment itself acknowledges the replay concern but treats `deadline` as the complete mitigation:

> *"the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it. The deadline is likewise required"* [4](#0-3) 

The deadline prevents *indefinite* replay but does not prevent replay *within* the deadline window — which is exactly when a pusher would want to revoke.

---

### Impact Explanation

A pusher who discovers a creator is using their price feed maliciously (e.g., to anchor a pool at a manipulated price) calls `revokePusher()` to stop their updates from flowing into the creator's namespace. The creator immediately replays the old signature via `allowPushers`, re-establishing the delegation. The pusher's automated price-push system continues writing into the creator's namespace. The creator's pools continue to receive the pusher's price updates, which the creator can exploit (e.g., by setting adversarial spread/codebook parameters around a legitimate price). The pusher's only recourse is to shut down their entire push infrastructure, which also stops them from serving their own namespace.

This breaks the core invariant that `revokePusher()` guarantees termination of delegation, and can sustain bad-price execution in pools anchored to the creator's feeds.

---

### Likelihood Explanation

Any creator who holds an unexpired pusher signature — which is the normal state for any active delegation — can perform this replay. No special privilege is required beyond being the creator who originally called `allowPushers`. The attack is a single on-chain transaction with zero cost beyond gas.

---

### Recommendation

Add a per-pusher nonce to the signed payload and store it on-chain. Increment it on each successful `allowPushers` call and on each `revokePusher` call:

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
pusherNonce[msg.sender]++;    // <-- invalidate all outstanding signatures
namespaceRemapping[msg.sender] = address(0);
```

This mirrors the standard EIP-2612 pattern: the nonce makes every previously signed consent stale the moment the pusher revokes.

---

### Proof of Concept

```solidity
// 1. Pusher signs consent with deadline = block.timestamp + 365 days
bytes memory sig = _signConsent(PUSHER_KEY, deadline, pusher, creator);

// 2. Creator establishes delegation
vm.prank(creator);
oracle.allowPushers(deadline, _arr(pusher), _sigs(sig));
assertEq(oracle.namespaceRemapping(pusher), creator);

// 3. Pusher revokes
vm.prank(pusher);
oracle.revokePusher();
assertEq(oracle.namespaceRemapping(pusher), address(0));

// 4. Creator replays the SAME signature — revocation undone
vm.prank(creator);
oracle.allowPushers(deadline, _arr(pusher), _sigs(sig));
// Pusher is delegated again despite having revoked
assertEq(oracle.namespaceRemapping(pusher), creator); // passes
```

The pusher's subsequent price pushes continue to land in the creator's namespace, feeding the creator's pool oracle with prices the pusher intended to stop providing.

### Citations

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
