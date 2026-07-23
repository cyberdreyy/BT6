### Title
Delegation signature in `allowPushers` carries no nonce and is never marked used, so a creator can replay a pusher's revoked consent to permanently re-establish the mapping before the deadline - (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

### Summary

`allowPushers` verifies an EIP-191 signature from each pusher wallet but never records that the signature was consumed. Because the signed payload contains only `(chainid, oracle, deadline, pusher, creator)` with no nonce or one-time token, the creator can call `allowPushers` with the exact same `(deadline, pushers[], signatures[])` arguments an unlimited number of times before the deadline expires. A pusher who calls `revokePusher()` to stop feeding a creator's namespace can have that revocation silently overwritten in the same block by the creator replaying the original signature.

### Finding Description

`allowPushers` builds the hash as:

```solidity
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
namespaceRemapping[pusher] = msg.sender;
``` [1](#0-0) 

There is no `mapping(bytes32 => bool) usedSignatures` or per-pusher nonce. The only expiry mechanism is `_ensureDeadline(deadline)`, which checks `block.timestamp <= deadline`. [2](#0-1) 

`revokePusher` sets `namespaceRemapping[msg.sender] = address(0)`:

```solidity
function revokePusher() external {
    address creator = namespaceRemapping[msg.sender];
    if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
    namespaceRemapping[msg.sender] = address(0);
    emit PusherRevoked(msg.sender, creator);
}
``` [3](#0-2) 

Because the signature is not invalidated on first use, the creator can immediately call `allowPushers` again with the identical calldata, writing `namespaceRemapping[pusher] = creator` back. This cycle can repeat for the entire lifetime of the deadline window.

The `fallback` push path resolves the namespace as:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [4](#0-3) 

So as long as the creator keeps replaying the signature, every push from the pusher wallet is redirected into the creator's namespace rather than the pusher's own namespace.

### Impact Explanation

A pusher who discovers a creator is using their price feed in a harmful way (e.g., a pool with manipulated parameters, or a pool the pusher no longer wishes to support) cannot stop feeding that creator's namespace before the deadline. The pusher's only escape is to cease all pushes entirely, which also starves their own namespace and any other legitimate consumers. Any pool reading `feedIdOf(creator, slotIndex, positionIndex)` continues to receive the pusher's price data against the pusher's explicit intent. If the pusher is the sole price source for that feed, the pool's oracle data remains live and tradeable when the pusher believed they had cut off the feed.

### Likelihood Explanation

Any creator who holds a valid, unexpired pusher signature (which they obtained legitimately during setup) can execute this replay at zero cost. The creator has a direct financial incentive to keep a price feed alive if their pool depends on it. Deadlines are typically set far in the future (days to months) to avoid operational disruption, so the replay window is large.

### Recommendation

Mark each signature as consumed on first use by hashing the recovered signature bytes (or the full payload hash) into a `mapping(bytes32 => bool) private _usedDelegations` and reverting if already set:

```solidity
mapping(bytes32 => bool) private _usedDelegations;

// inside the loop, after computing `hash`:
require(!_usedDelegations[hash], SignatureAlreadyUsed());
_usedDelegations[hash] = true;
```

Alternatively, add a per-pusher nonce to the signed payload (`keccak256(abi.encode(..., pusherNonce[pusher]++))`) so each consent is single-use by construction, directly mirroring the fix recommended in the external report.

### Proof of Concept

1. Pusher P signs: `keccak256(abi.encode(chainid, oracle, deadline=T+365days, P, C))` → `sig`.
2. Creator C calls `allowPushers(T+365days, [P], [sig])` → `namespaceRemapping[P] = C`. ✓
3. Pusher P calls `revokePusher()` → `namespaceRemapping[P] = address(0)`. ✓
4. Creator C calls `allowPushers(T+365days, [P], [sig])` again with identical arguments → `namespaceRemapping[P] = C` again. No revert.
5. Pusher P's next `fallback` push is routed to C's namespace. P's revocation had zero effect.
6. Steps 3–4 repeat indefinitely until `block.timestamp > T+365days`.

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L204-209)
```text
            bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
                keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
            );
            require(pusher == ECDSA.recover(hash, signatures[i]));

            namespaceRemapping[pusher] = msg.sender;
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
