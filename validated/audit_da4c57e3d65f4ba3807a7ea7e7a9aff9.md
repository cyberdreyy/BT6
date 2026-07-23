### Title
Pusher delegation signature lacks a nonce, allowing creator to replay `allowPushers` and permanently nullify a pusher's `revokePusher()` call within the deadline window — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` accepts an EIP-191 signature whose domain is `(chainid, oracle, deadline, pusher, creator)`. Because there is no nonce, the same signature is valid for every call to `allowPushers` until the deadline timestamp passes. A creator who holds a pusher's signed consent can therefore replay it immediately after the pusher calls `revokePusher()`, re-establishing the delegation and making the revocation permanently ineffective for the entire deadline window.

---

### Finding Description

`allowPushers` verifies the pusher's consent with:

```solidity
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
namespaceRemapping[pusher] = msg.sender;
``` [1](#0-0) 

The signed message contains no nonce and no "used" flag. The same `(deadline, pusher, creator)` tuple produces the same hash on every call, so the signature is reusable an unlimited number of times before `deadline`.

`revokePusher` clears the mapping:

```solidity
namespaceRemapping[msg.sender] = address(0);
``` [2](#0-1) 

But nothing prevents the creator from immediately calling `allowPushers` again with the identical signature, writing `namespaceRemapping[pusher] = creator` back. The code's own comment acknowledges the replay risk but incorrectly claims the deadline fully mitigates it:

> "The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it." [3](#0-2) 

The deadline prevents replay only *after* it expires. During the entire deadline window — which callers routinely set days or weeks in the future — the creator can replay the signature an unlimited number of times, each time overwriting the pusher's revocation.

The fallback push path resolves the namespace at call time:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [4](#0-3) 

So every push the pusher makes after the creator's replay still lands in the creator's namespace, not the pusher's own namespace. The pusher's only escape is to stop pushing entirely, which is a denial-of-service on the pusher's own oracle service.

---

### Impact Explanation

A pusher that discovers a creator is malicious (e.g., the creator's pool is using the feed to execute bad-price swaps) or whose signing key is partially compromised cannot stop feeding the creator's namespace until the deadline expires. Every push the pusher makes continues to update `feedIdOf(creator, slotIndex, positionIndex)`, which the creator's pool reads via `getBidAndAskPrice`. This satisfies the **bad-price execution** and **admin-boundary break** impact gates: the creator, an unprivileged party relative to the pusher's revocation right, bypasses the revocation mechanism and keeps receiving live oracle quotes the pusher explicitly withdrew consent for.

---

### Likelihood Explanation

Medium. The creator must have saved the original signature bytes (trivial — they submitted the transaction themselves). The deadline is typically set far in the future (days/weeks) to avoid operational friction, so the replay window is large. The creator has a direct financial incentive to keep the pusher feeding their pool's price provider.

---

### Recommendation

Add a per-pusher nonce to the delegation signature, analogous to EIP-2612:

```solidity
mapping(address => uint256) public pusherNonces;

// In allowPushers:
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(
        block.chainid, address(this), deadline,
        pusherNonces[pusher],   // <-- add nonce
        pusher, msg.sender
    ))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
pusherNonces[pusher]++;         // <-- invalidate on use
namespaceRemapping[pusher] = msg.sender;
```

Increment `pusherNonces[pusher]` also inside `revokePusher()` so that any outstanding signed consent is immediately invalidated the moment the pusher revokes, regardless of the deadline.

---

### Proof of Concept

```
1. Creator A calls allowPushers(deadline=now+30days, [pusher], [sig])
   → namespaceRemapping[pusher] = A
   → pusher's fallback pushes land in feedIdOf(A, slot, pos)

2. Pusher discovers A is malicious; calls revokePusher()
   → namespaceRemapping[pusher] = 0
   → pusher's fallback pushes now land in pusher's OWN namespace

3. Creator A immediately calls allowPushers(deadline=now+30days, [pusher], [sig])
   using the IDENTICAL signature bytes from step 1
   → namespaceRemapping[pusher] = A  (revocation overwritten)
   → pusher's fallback pushes land in feedIdOf(A, slot, pos) again

4. Creator A repeats step 3 after every revokePusher() call the pusher makes,
   for the entire 30-day deadline window.

5. A's pool's PriceProvider reads feedIdOf(A, slot, pos) on every swap;
   the pusher cannot stop feeding it without ceasing all oracle activity.
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L241-242)
```text
        namespaceRemapping[msg.sender] = address(0);
        emit PusherRevoked(msg.sender, creator);
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L315-316)
```text
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;
```
