### Title
`allowPushers` Signature Replay Re-Establishes Revoked Delegation, Enabling Feed Staleness and Pool DoS — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` accepts an EIP-191 signature that commits to `(chainid, oracle, deadline, pusher, creator)` but tracks **no nonce and no used-signature set**. A creator who holds a valid unexpired signature can replay it an unlimited number of times — including after the pusher has called `revokePusher()` or re-delegated to a different creator — silently overwriting `namespaceRemapping[pusher]` back to themselves. This breaks the revocation invariant, allows a malicious creator to hijack a pusher's namespace writes away from a new creator, and causes the new creator's feeds to go permanently stale until the old deadline expires, making every pool that reads those feeds unusable.

---

### Finding Description

`allowPushers` builds a hash and recovers the pusher's signature:

```solidity
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
namespaceRemapping[pusher] = msg.sender;   // overwrites any current mapping
``` [1](#0-0) 

There is no nonce, no "used-signature" bitmap, and no check that `namespaceRemapping[pusher]` is currently `address(0)`. The only replay barrier is `_ensureDeadline(deadline)`.

`revokePusher` clears the mapping:

```solidity
function revokePusher() external {
    address creator = namespaceRemapping[msg.sender];
    if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
    namespaceRemapping[msg.sender] = address(0);
    emit PusherRevoked(msg.sender, creator);
}
``` [2](#0-1) 

But the original signature is still valid. The creator can immediately call `allowPushers` again with the identical `(deadline, [pusher], [sig])` arguments, re-writing `namespaceRemapping[pusher] = creator`. The code's own comment acknowledges the concern ("an undated signature could re-establish a delegation AFTER the pusher revoked it") but the deadline only prevents replay *after* it expires — not *before*. [3](#0-2) 

The fallback push path resolves the namespace at push time:

```
fallback push → namespace resolution (namespaceRemapping[msg.sender]) → slot overwrite
``` [4](#0-3) 

So every push the victim pusher makes after re-delegation lands in the attacker-creator's namespace, not the new creator's namespace.

---

### Impact Explanation

**Stale feeds → unusable pools.**

Attack sequence:

1. Pusher P signs consent for Creator C1 with a far-future deadline T.
2. C1 calls `allowPushers` — `namespaceRemapping[P] = C1`.
3. P calls `revokePusher()` — mapping cleared.
4. P signs new consent for Creator C2; C2 calls `allowPushers` — `namespaceRemapping[P] = C2`.
5. C1 replays the original signature (T still valid) — `namespaceRemapping[P] = C1` again.
6. All of P's subsequent fallback pushes land in C1's namespace; C2's feeds receive no new data.
7. C2's feeds age past `MAX_TIME_DRIFT`; every pool that calls `price(feedId, pool)` through an `AnchoredPriceProvider` backed by C2's feeds reverts on the staleness check.
8. Swaps, liquidity withdrawals, and any pool operation that requires a live oracle quote are permanently blocked until T expires.

This satisfies the impact gate: **broken core pool functionality causing unusable swap/withdraw flows** due to **stale oracle data reaching the pool**.

---

### Likelihood Explanation

- C1 only needs to have obtained a valid pusher signature at any prior point — a normal operational step.
- The attack is a single public transaction with no special privilege.
- The window lasts until the original deadline T, which operators routinely set days or weeks in the future.
- The pusher has no on-chain mechanism to invalidate the old signature before T expires.

Likelihood: **Medium** (requires a prior relationship between C1 and P, but the exploit itself is trivial once that relationship existed).

---

### Recommendation

Add a per-pusher nonce that is incremented on every successful `allowPushers` call and on every `revokePusher` / `removePushers` call, and include it in the signed digest:

```solidity
mapping(address => uint256) public pusherNonce;

// In allowPushers:
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(
        block.chainid, address(this), deadline,
        pusher, msg.sender,
        pusherNonce[pusher]   // ← add nonce
    ))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
pusherNonce[pusher]++;        // ← invalidate all prior signatures
namespaceRemapping[pusher] = msg.sender;

// In revokePusher / removePushers:
pusherNonce[pusher]++;        // ← invalidate any outstanding signatures on revoke
```

This ensures that once a pusher revokes (or is removed), every previously issued signature is immediately invalidated regardless of its deadline.

---

### Proof of Concept

```solidity
// 1. Setup
uint256 deadline = block.timestamp + 30 days;
bytes memory sig = signConsent(PUSHER_KEY, deadline, pusher, creator1);

// 2. Creator1 delegates pusher
vm.prank(creator1);
oracle.allowPushers(deadline, toArray(pusher), toArray(sig));
assertEq(oracle.namespaceRemapping(pusher), creator1);

// 3. Pusher revokes
vm.prank(pusher);
oracle.revokePusher();
assertEq(oracle.namespaceRemapping(pusher), address(0));

// 4. Pusher re-delegates to creator2
bytes memory sig2 = signConsent(PUSHER_KEY, deadline, pusher, creator2);
vm.prank(creator2);
oracle.allowPushers(deadline, toArray(pusher), toArray(sig2));
assertEq(oracle.namespaceRemapping(pusher), creator2);

// 5. Creator1 REPLAYS old signature — re-hijacks pusher
vm.prank(creator1);
oracle.allowPushers(deadline, toArray(pusher), toArray(sig));  // same sig as step 2
assertEq(oracle.namespaceRemapping(pusher), creator1);         // ← creator2 is now starved

// 6. Pusher's pushes land in creator1's namespace; creator2's feeds go stale
// → pools backed by creator2's feeds revert on MAX_TIME_DRIFT check
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L262-270)
```text
    /*
     *
     * Push paths
     *
     */

    /// @notice Single-slot update authorized by the creator's signature. The signed slot
    ///         word carries its own 56-bit timestamp, so replay is neutralized by the
    ///         monotonicity check below — no deadline is needed.
```
