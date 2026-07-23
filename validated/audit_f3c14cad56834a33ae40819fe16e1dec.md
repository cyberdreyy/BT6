### Title
Pusher Delegation Signature Replay Allows Creator to Re-Establish Revoked Delegation, Feeding Unauthorized Prices Into Pools — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` does not consume or invalidate a pusher's EIP-191 signature after use. Within the deadline window, a creator can replay the identical signature bytes to re-establish a delegation that the pusher already revoked via `revokePusher()`. The pusher's revocation is silently overwritten, and all subsequent pushes from that wallet continue to land in the creator's namespace rather than the pusher's own namespace, feeding the creator's pool with price data the pusher no longer consents to provide.

---

### Finding Description

`allowPushers` iterates over a caller-supplied `(pushers[], signatures[])` array, verifies each EIP-191 signature, and writes `namespaceRemapping[pusher] = msg.sender`: [1](#0-0) 

The signed message commits to `(chainid, oracle, deadline, pusher, creator)`. No consumed-signature registry, no nonce, and no per-use invalidation exists. After the creator calls `allowPushers` once, the pusher may call `revokePusher()`: [2](#0-1) 

This sets `namespaceRemapping[pusher] = address(0)`. However, the creator immediately calls `allowPushers` again with the **identical** `(deadline, pusher, signature)` tuple. Because `block.timestamp <= deadline` still holds and the signature is cryptographically valid, the check at line 207 passes again: [3](#0-2) 

`namespaceRemapping[pusher]` is restored to `msg.sender` (the creator). The pusher's revocation is silently nullified.

The `fallback` push path resolves the namespace from `namespaceRemapping[msg.sender]` at call time: [4](#0-3) 

So every push the pusher makes after their revocation — believing they are writing to their own namespace — is actually written to the creator's namespace and consumed by the creator's pool.

The code comment at line 188–191 acknowledges the deadline as the intended replay guard, but the guard only bounds the window; it does not prevent repeated use of the same signature within that window: [5](#0-4) 

---

### Impact Explanation

A pool consuming the creator's `feedId` receives price data from a pusher who has explicitly revoked consent. The creator can:

1. Selectively re-establish delegation to cherry-pick which price updates land in their namespace (e.g., re-delegate only when the pusher's price is favorable, revoke when it is not — effectively choosing the bid/ask the pool sees).
2. Permanently prevent a pusher from escaping their namespace until the deadline expires, regardless of how many times `revokePusher` is called.

Both paths produce **bad-price execution**: the pool's `getBidAsk` call reads a price that does not reflect the pusher's current intent, and swaps execute against a stale or selectively curated oracle value.

---

### Likelihood Explanation

- The creator must have obtained a valid pusher signature initially (one social-engineering or phishing step).
- After that, re-establishing delegation costs one cheap on-chain call and can be automated (e.g., watch for `PusherRevoked` events and immediately replay).
- No privileged role is required beyond being the `msg.sender` who originally called `allowPushers`.
- The window is bounded by `deadline`, but creators can request long-deadline signatures (nothing in the contract caps deadline length).

---

### Recommendation

Track consumed signatures with a `mapping(bytes32 => bool) private _usedDelegations` keyed on the signature hash (or the full message hash). In `allowPushers`, after a successful `ECDSA.recover`, assert `!_usedDelegations[hash]` and then set `_usedDelegations[hash] = true`. This ensures each pusher-signed consent can only establish one delegation, making `revokePusher` permanently effective.

Alternatively, include a per-pusher monotonic nonce in the signed payload and store the last accepted nonce, so the pusher can invalidate all prior signatures by incrementing their nonce.

---

### Proof of Concept

```
// Setup
address creator = address(0xC0FFEE);
address pusher  = address(0xBEEF);

uint256 deadline = block.timestamp + 30 days; // long window

// Pusher signs consent off-chain:
// hash = keccak256(abi.encode(chainid, oracle, deadline, pusher, creator))
bytes memory sig = <pusher_signed_sig>;

// Step 1: creator establishes delegation
vm.prank(creator);
oracle.allowPushers(deadline, [pusher], [sig]);
// namespaceRemapping[pusher] == creator ✓

// Step 2: pusher revokes
vm.prank(pusher);
oracle.revokePusher();
// namespaceRemapping[pusher] == address(0) ✓

// Step 3: creator replays IDENTICAL signature — no new pusher consent needed
vm.prank(creator);
oracle.allowPushers(deadline, [pusher], [sig]);   // same sig, same deadline
// namespaceRemapping[pusher] == creator again ✓  ← revocation bypassed

// Step 4: pusher pushes (believing they write to own namespace)
vm.prank(pusher);
(bool ok,) = address(oracle).call(slotWord);      // fallback push path
// push lands in creator's namespace, not pusher's
// creator's pool reads creator's feedId → gets pusher's price data without consent
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
