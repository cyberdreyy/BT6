### Title
`revokePusher()` Is Ineffective Within the Deadline Window Due to Missing Nonce in `allowPushers` Signature — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracleV1.allowPushers` builds its EIP-191 signature hash without a nonce or any per-use invalidation marker. Once a pusher signs a delegation, the creator can replay that exact signature any number of times before the deadline expires. This makes `revokePusher()` — the pusher's only self-protection mechanism — completely ineffective within the deadline window: the creator can immediately re-establish the delegation after the pusher revokes it, allowing a compromised or malicious pusher key to continue injecting arbitrary prices into the creator's namespace and, from there, into registered pools.

---

### Finding Description

`allowPushers` constructs the signed hash as:

```solidity
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
``` [1](#0-0) 

There is no nonce, no per-signature consumed flag, and no mapping that records which `(pusher, creator, deadline)` tuples have already been used. The only replay guard is the deadline itself — but the deadline only prevents use *after* it expires, not repeated use *within* the window.

`revokePusher()` clears the mapping entry:

```solidity
namespaceRemapping[msg.sender] = address(0);
``` [2](#0-1) 

But the creator still holds the original, unexpired signature. They can immediately call `allowPushers` again with the same `(deadline, pusher, signature)` tuple, writing `namespaceRemapping[pusher] = creator` back into storage. The revocation is silently undone.

The code comment on `allowPushers` explicitly acknowledges the concern:

> *"the deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."* [3](#0-2) 

The comment treats the deadline as the solution, but the deadline only bounds the outer window — it does not prevent the creator from replaying the signature *within* that window after a revocation. The mitigation is incomplete.

**Attack sequence:**

1. Pusher signs a delegation with `deadline = block.timestamp + 365 days`.
2. Creator calls `allowPushers(deadline, [pusher], [sig])` → `namespaceRemapping[pusher] = creator`.
3. Pusher's key is compromised (or pusher decides to stop). Pusher calls `revokePusher()` → `namespaceRemapping[pusher] = address(0)`.
4. Creator (or attacker who controls the creator account) immediately calls `allowPushers(deadline, [pusher], [sig])` with the identical arguments → `namespaceRemapping[pusher] = creator` again.
5. The attacker now pushes arbitrary slot words through the `fallback()` path into the creator's namespace, overwriting all four price lanes in any slot with fabricated prices and spreads.
6. Pools registered against feeds in that namespace consume the bad prices on the next swap.

The `fallback()` push path resolves the namespace from `namespaceRemapping[msg.sender]` with no additional check:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [4](#0-3) 

Once the delegation is re-established, the attacker writes any `(price, s0, s1, timestamp)` combination that passes the monotonicity and `maxTimeDrift` guards — both of which are trivially satisfied by choosing a timestamp slightly ahead of the current stored value.

---

### Impact Explanation

A compromised pusher key that the legitimate pusher tried to revoke can be kept active indefinitely (until the deadline) by the creator replaying the original signature. The attacker pushes fabricated prices into the creator's namespace. Every pool registered against those feeds will execute swaps at the attacker-controlled bid/ask, causing:

- **Bad-price execution**: traders receive more output than the true oracle price permits, or the pool receives less input than owed.
- **Pool insolvency**: repeated bad-price swaps drain the pool's reserves below LP claims.

This is a direct loss of user principal and LP assets above Sherlock thresholds.

---

### Likelihood Explanation

The preconditions are:
1. A pusher signs a delegation with a long deadline (common operational practice for infrastructure keys).
2. The creator account is malicious or is itself compromised.
3. The creator saves the original signature (trivially done by monitoring the `PusherAuthorized` event or the original `allowPushers` transaction).

Condition 3 requires zero on-chain state — the signature is public in calldata. Condition 2 is the main gate, but the creator is the entity that originally called `allowPushers`, so they already possess the signature. The pusher's `revokePusher()` call is the trigger that reveals the window of attack. Likelihood is **Medium**.

---

### Recommendation

Add a per-pusher nonce to the signed payload and increment it on every successful `allowPushers` call. Record consumed `(pusher, nonce)` pairs so that a replayed signature is rejected even within the deadline:

```solidity
mapping(address => uint256) public pusherNonce;

// In allowPushers:
uint256 nonce = pusherNonce[pusher]++;
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender, nonce))
);
```

Alternatively, record the `(hash → bool)` of each consumed signature and revert on reuse. Either approach ensures that `revokePusher()` permanently invalidates the current delegation, because the next `allowPushers` call would require a freshly signed message with the incremented nonce.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

// Demonstrates that revokePusher() is ineffective within the deadline window.
// Run against CompressedOracleV1 in a Foundry test.

function test_revokePusherReplayable() public {
    address creator = address(this);
    (address pusher, uint256 pusherKey) = makeAddrAndKey("pusher");

    uint256 deadline = block.timestamp + 365 days;

    // Pusher signs consent
    bytes32 hash = keccak256(abi.encode(
        block.chainid, address(oracle), deadline, pusher, creator
    ));
    bytes32 ethHash = MessageHashUtils.toEthSignedMessageHash(hash);
    (uint8 v, bytes32 r, bytes32 s) = vm.sign(pusherKey, ethHash);
    bytes memory sig = abi.encodePacked(r, s, v);

    // Creator establishes delegation
    address[] memory pushers = new address[](1); pushers[0] = pusher;
    bytes[] memory sigs = new bytes[](1); sigs[0] = sig;
    oracle.allowPushers(deadline, pushers, sigs);
    assertEq(oracle.namespaceRemapping(pusher), creator);

    // Pusher revokes
    vm.prank(pusher);
    oracle.revokePusher();
    assertEq(oracle.namespaceRemapping(pusher), address(0)); // revoked

    // Creator replays the SAME signature — revocation is undone
    oracle.allowPushers(deadline, pushers, sigs);
    assertEq(oracle.namespaceRemapping(pusher), creator); // re-established!

    // Attacker (holding pusher key) now pushes fabricated prices
    // into creator's namespace — pools consuming these feeds get bad prices.
}
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L204-207)
```text
            bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
                keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
            );
            require(pusher == ECDSA.recover(hash, signatures[i]));
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
