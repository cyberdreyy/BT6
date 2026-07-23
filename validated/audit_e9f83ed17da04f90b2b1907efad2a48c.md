### Title
`allowPushers` Signature Replay Re-Establishes Delegation After Pusher Revocation — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

### Summary

The `allowPushers` function in `CompressedOracle.sol` does not invalidate previously-used consent signatures when a pusher calls `revokePusher()`. Any creator who holds the original on-chain signature can replay it — before its deadline expires — to silently re-establish delegation, making the pusher's revocation permanently ineffective for the lifetime of that signature.

### Finding Description

`allowPushers` maps a pusher wallet into a creator's namespace by verifying an EIP-191 signature over `(chainid, address(this), deadline, pusher, creator)`: [1](#0-0) 

The only replay protection is the `deadline` field. There is no nonce, no used-signature registry, and no check that `namespaceRemapping[pusher]` is currently `address(0)` before writing.

`revokePusher` clears the mapping: [2](#0-1) 

After revocation, `namespaceRemapping[pusher] == address(0)`. However, the original consent signature — which was submitted on-chain in the first `allowPushers` call and is therefore permanently visible in transaction history — remains cryptographically valid until `deadline`. Any caller can extract it from calldata and replay it:

```
Step 1: creator calls allowPushers(D, [P], [sig])  → namespaceRemapping[P] = creator
Step 2: pusher calls revokePusher()                 → namespaceRemapping[P] = address(0)
Step 3: creator calls allowPushers(D, [P], [sig])   → namespaceRemapping[P] = creator  ← replay
```

This is the direct analog of the `smFeePercentage` bug: just as `socializeLoss()` called a second time overwrites `previousSmFeePercentage` with the already-corrupted 100% value and breaks `disableWithdrawFee()`, calling `allowPushers` a second time (after revocation) overwrites the cleared `namespaceRemapping` entry and breaks `revokePusher()`. In both cases the "recovery" path is rendered permanently ineffective by a second invocation of the same state-writing function.

The code comment acknowledges the risk but treats the deadline as the sole mitigation: [3](#0-2) 

Deadlines are chosen by the creator, not the pusher, and can be set arbitrarily far in the future (e.g., one year). The pusher has no unilateral way to shorten the replay window.

The project's own audit-target specification flags this exact surface: [4](#0-3) 

### Impact Explanation

A pusher whose key is compromised, or who discovers that a creator is using their price data maliciously, calls `revokePusher()` to stop their pushes from landing in the creator's namespace. The creator immediately replays the original on-chain signature to re-establish delegation. The compromised or unwanted key continues to write into the creator's namespace; the creator's pool continues to consume those prices in every subsequent swap. Traders execute against manipulated bid/ask quotes and suffer direct principal loss.

Additionally, if the pusher is a shared infrastructure pusher serving multiple creators, their only remaining option is to stop pushing entirely — starving all other creators' feeds simultaneously. This is a griefing vector that can render multiple pools non-functional.

### Likelihood Explanation

- The original signature is permanently on-chain in the first `allowPushers` transaction's calldata; no off-chain coordination is required to replay it.
- The creator is the only party who can call `allowPushers` (it requires `msg.sender == creator`), so the attack is creator-initiated. Creators are semi-trusted but not fully trusted.
- Deadlines are creator-chosen and routinely set to months or years in the future, giving a long replay window.
- The pusher has no mechanism to force-expire the signature or shorten the deadline.

### Recommendation

Track consumed signatures with a per-pusher nonce or a `usedSignatures` mapping, and reject any signature that has already been accepted:

```solidity
mapping(bytes32 => bool) private _usedSignatures;

// inside allowPushers loop:
bytes32 sigHash = keccak256(signatures[i]);
require(!_usedSignatures[sigHash], "signature already used");
_usedSignatures[sigHash] = true;
```

Alternatively, include a pusher-controlled nonce in the signed message so the pusher can invalidate all outstanding signatures by incrementing their nonce:

```solidity
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender, pusherNonces[pusher]))
```

Either approach ensures that `revokePusher()` is a durable exit: once revoked, no previously-issued signature can re-establish the delegation.

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

// Assume: oracle = deployed CompressedOracle
// Actors: creator (address creatorAddr), pusher (address pusherAddr, key PUSHER_KEY)

// 1. Pusher signs consent for creator with a 1-year deadline
uint256 deadline = block.timestamp + 365 days;
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(oracle), deadline, pusherAddr, creatorAddr))
);
bytes memory sig = sign(PUSHER_KEY, hash); // pusher signs

// 2. Creator establishes delegation (sig goes on-chain)
vm.prank(creatorAddr);
oracle.allowPushers(deadline, _arr(pusherAddr), _arr(sig));
assertEq(oracle.namespaceRemapping(pusherAddr), creatorAddr); // delegated

// 3. Pusher revokes
vm.prank(pusherAddr);
oracle.revokePusher();
assertEq(oracle.namespaceRemapping(pusherAddr), address(0)); // revoked

// 4. Creator replays the SAME on-chain signature — no new consent from pusher
vm.prank(creatorAddr);
oracle.allowPushers(deadline, _arr(pusherAddr), _arr(sig)); // succeeds
assertEq(oracle.namespaceRemapping(pusherAddr), creatorAddr); // delegation restored

// 5. Pusher's future pushes still land in creator's namespace;
//    revocation had zero lasting effect.
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

**File:** generate_scanned_questions.py (L993-999)
```python
            file_function="smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::allowPushers",
            entrypoint="smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::allowPushers",
            call_path="public allowPushers -> EIP-191 signature recovery -> namespaceRemapping update -> later fallback pushes use delegated namespace",
            values="the delegated namespace owner, replay scope, and every future slot write attributed to the delegated pusher",
            control_hint="Delegation is intentionally permissionless, so signature domain separation and replay resistance are the only things preventing namespace hijack.",
            validation_focus="Replay and cross-context-test pusher signatures across creators, deadlines, chain ids, and contract addresses and assert no delegated namespace can be claimed twice.",
        ),
```
