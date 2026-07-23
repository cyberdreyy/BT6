### Title
Pusher delegation signature can be replayed within the deadline window, permanently defeating `revokePusher()` — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

### Summary

`allowPushers()` in `CompressedOracleV1` uses a deadline to prevent signature replay, but the deadline only blocks replay **after** it expires. Within the deadline window the identical signature can be submitted an unlimited number of times. A creator can therefore re-establish a pusher's delegation immediately after the pusher calls `revokePusher()`, making self-revocation ineffective for the entire deadline duration and allowing bad prices to continue flowing into the creator's namespace and downstream pools.

### Finding Description

`allowPushers` constructs the signed digest as:

```solidity
// CompressedOracle.sol lines 204-207
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
``` [1](#0-0) 

After signature verification the mapping is unconditionally overwritten:

```solidity
namespaceRemapping[pusher] = msg.sender;
``` [2](#0-1) 

`revokePusher()` clears the mapping:

```solidity
// CompressedOracle.sol lines 238-243
function revokePusher() external {
    address creator = namespaceRemapping[msg.sender];
    if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
    namespaceRemapping[msg.sender] = address(0);
    emit PusherRevoked(msg.sender, creator);
}
``` [3](#0-2) 

There is no nonce, no consumed-signature registry, and no check that the pusher has not already revoked. The code's own NatSpec comment acknowledges the replay risk but incorrectly treats the deadline as a complete fix:

> *"The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."* [4](#0-3) 

The deadline prevents replay only **after** it expires; within the window the same `(deadline, pusher, creator, sig)` tuple is accepted on every call.

### Impact Explanation

A pusher that is an automated price-pushing system (e.g., a keeper bot or a contract pusher that was later converted to an EOA path) cannot stop its prices from landing in the creator's namespace until the deadline passes. If the pusher's key is compromised or the pusher begins emitting incorrect prices and tries to self-revoke to halt the damage, the creator can call `allowPushers` again with the original signature in the same block, restoring the delegation. The pusher's slot words continue to overwrite the creator's storage slots, feeding stale or manipulated `(price, spread0, spread1, timestampMs)` tuples into every pool that reads those feeds via `getOracleData` / `price()`. This satisfies the **bad-price execution** impact gate: an unclamped or stale bid/ask quote reaches a pool swap.

### Likelihood Explanation

Requires: (1) a creator who deliberately replays the signature after the pusher revokes, and (2) a pusher that is still actively pushing (automated). Both conditions are realistic in production where creators integrate third-party keeper bots with long-lived delegation signatures. Deadline windows of hours to days are common in off-chain signing flows, matching the M-08 sponsor comment that "loans last for months, this will be at most a few hours" — here the window is whatever the pusher signed.

### Recommendation

Add a per-pusher nonce to the signed digest and increment (or invalidate) it on every successful `allowPushers` call and on every `revokePusher` / `removePushers` call:

```solidity
mapping(address => uint256) public pusherNonce;

// in allowPushers:
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(
        block.chainid, address(this), deadline,
        pusher, msg.sender, pusherNonce[pusher]++
    ))
);

// in revokePusher / removePushers: also increment pusherNonce[pusher]
```

This ensures that a signature consumed once — or invalidated by a revocation — can never be replayed.

### Proof of Concept

```
1. Pusher signs consent:
     digest = keccak256(chainid ‖ oracle ‖ deadline=T+1day ‖ pusher ‖ creator)
     sig = sign(pusherKey, digest)

2. Creator calls allowPushers(T+1day, [pusher], [sig])
     → namespaceRemapping[pusher] = creator  ✓

3. Pusher's automated bot is compromised; pusher calls revokePusher()
     → namespaceRemapping[pusher] = address(0)  ✓

4. Creator immediately calls allowPushers(T+1day, [pusher], [sig])  ← SAME sig
     → _ensureDeadline passes (T+1day > block.timestamp)
     → ECDSA.recover returns pusher  ← sig still valid, no nonce
     → namespaceRemapping[pusher] = creator  ← delegation restored

5. Compromised pusher bot pushes manipulated slot words into creator's namespace.
   Pools reading creator's feeds receive bad prices; swaps execute at wrong bid/ask.

6. Steps 3-4 can repeat indefinitely until block.timestamp > T+1day.
``` [5](#0-4)

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
