### Title
Pusher consent signature in `allowPushers` has no replay protection within the deadline window, allowing a creator to silently re-establish a revoked delegation - (File: `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

### Summary

`allowPushers` verifies a pusher's EIP-191 consent signature whose domain is `(chainid, oracle, deadline, pusher, creator)`. There is no nonce, no per-pusher revocation counter, and no "used-signatures" set. The same signature is therefore valid for every call to `allowPushers` until the deadline expires. A creator who holds a pusher's consent signature can replay it immediately after the pusher calls `revokePusher()`, re-establishing the delegation without any fresh consent from the pusher. The code comment at lines 186–191 explicitly acknowledges the replay concern and claims the deadline addresses it, but the deadline only prevents replay *after* it expires — it does nothing to prevent replay *within* the deadline window.

### Finding Description

`allowPushers` constructs the signature hash as:

```solidity
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
``` [1](#0-0) 

There is no nonce or consumed-signature tracking anywhere in the contract: [2](#0-1) 

`revokePusher()` simply zeroes the mapping entry:

```solidity
namespaceRemapping[msg.sender] = address(0);
``` [3](#0-2) 

Because the signature domain does not encode any state that changes on revocation, the creator can call `allowPushers` again with the identical `(deadline, pusher, sig)` tuple and the `require(pusher == ECDSA.recover(...))` check passes again, writing `namespaceRemapping[pusher] = msg.sender` a second time.

The comment at lines 186–191 explicitly identifies the replay-after-revocation risk and states the deadline is the mitigation:

> *"The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."* [4](#0-3) 

The deadline mitigates replay *after* expiry, but not replay *before* expiry. The pusher's revocation is therefore ineffective for the entire remaining lifetime of the original deadline.

### Impact Explanation

The `fallback` push path resolves the namespace at call time:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [5](#0-4) 

Every slot word the pusher sends after revocation is silently written into the creator's namespace instead of the pusher's own namespace. The concrete price-feed impact:

1. Pusher P is delegated to creator C1 (oracle used by a live pool). P revokes and re-delegates to creator C2 (a different oracle). C1 immediately replays the old signature, overwriting `namespaceRemapping[P] = C1`. P's subsequent pushes land in C1's namespace, not C2's. C2's oracle receives no further updates → stale `refTime` → `AnchoredPriceProvider` or pool rejects the quote as stale → pool is effectively frozen for that feed.

2. Alternatively, P revokes because they discovered C1 is using their data maliciously. C1 replays the signature to force P's data to continue flowing into their namespace, feeding prices into pools against the pusher's explicit intent.

The `feedIdOf` encoding ties a feed's identity to the creator address, so data written into the wrong namespace is consumed by pools registered against that creator's feed IDs. [6](#0-5) 

### Likelihood Explanation

- The trigger is a valid, unprivileged call to the public `allowPushers` function — no special role is required.
- The creator already holds the pusher's signature (they used it to establish the original delegation).
- The replay requires a single transaction and zero additional off-chain work.
- Deadlines are typically set days or weeks in the future (the test suite uses `block.timestamp + 1 days`), giving the creator a large window. [7](#0-6) 

### Recommendation

Add a per-pusher nonce to the signature domain and increment it on every successful `allowPushers` call (and optionally on `revokePusher`). This makes every consent signature single-use:

```solidity
mapping(address => uint256) public pusherNonce;

// In allowPushers:
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(
        block.chainid, address(this), deadline,
        pusher, msg.sender, pusherNonce[pusher]   // <-- add nonce
    ))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
pusherNonce[pusher]++;   // invalidate the consumed signature
namespaceRemapping[pusher] = msg.sender;
```

Alternatively, maintain a `mapping(bytes32 => bool) usedSignatures` and mark each consumed digest as spent.

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import "forge-std/Test.sol";
import {CompressedOracleV1} from "src/oracles/compressed/CompressedOracle.sol";
import {MessageHashUtils} from "@openzeppelin/contracts/utils/cryptography/MessageHashUtils.sol";

contract ReplayDelegationTest is Test {
    CompressedOracleV1 oracle;
    uint256 constant PUSHER_KEY = 0xBEEF;
    address pusher;
    address creator;

    function setUp() public {
        oracle = new CompressedOracleV1(address(this), 0);
        pusher  = vm.addr(PUSHER_KEY);
        creator = address(0xC0FFEE);
        vm.warp(1_700_000_000);
    }

    function testReplayAfterRevoke() public {
        uint256 deadline = block.timestamp + 1 days;

        // 1. Pusher signs consent once.
        bytes32 digest = MessageHashUtils.toEthSignedMessageHash(
            keccak256(abi.encode(block.chainid, address(oracle), deadline, pusher, creator))
        );
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(PUSHER_KEY, digest);
        bytes memory sig = abi.encodePacked(r, s, v);

        address[] memory pushers = new address[](1);
        pushers[0] = pusher;
        bytes[] memory sigs = new bytes[](1);
        sigs[0] = sig;

        // 2. Creator establishes delegation.
        vm.prank(creator);
        oracle.allowPushers(deadline, pushers, sigs);
        assertEq(oracle.namespaceRemapping(pusher), creator);

        // 3. Pusher revokes.
        vm.prank(pusher);
        oracle.revokePusher();
        assertEq(oracle.namespaceRemapping(pusher), address(0), "revoked");

        // 4. Creator replays the SAME signature — succeeds, revocation undone.
        vm.prank(creator);
        oracle.allowPushers(deadline, pushers, sigs);  // no revert!
        assertEq(oracle.namespaceRemapping(pusher), creator, "delegation re-established without pusher consent");
    }
}
```

The test passes on the current code, demonstrating that `revokePusher` provides no durable protection against a creator who holds the original consent signature. [7](#0-6) [3](#0-2)

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L29-29)
```text
    mapping(address => address) public namespaceRemapping;
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L49-53)
```text
    function feedIdOf(address creator, uint8 slotIndex, uint8 positionIndex) public view returns (bytes32) {
        return bytes32(
            uint256(uint160(creator)) << 96 | block.chainid << 16 | uint256(slotIndex) << 8 | positionIndex
        );
    }
```

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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L315-316)
```text
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;
```
