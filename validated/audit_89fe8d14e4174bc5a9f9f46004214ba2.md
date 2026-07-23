### Title
`allowPushers` Consent Signature Has No Per-Invocation Invalidation — Creator Can Replay It to Undo `revokePusher()` Within the Deadline Window - (File: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol)

---

### Summary

`allowPushers` uses a deadline as its only replay guard. No nonce or used-signature bitmap exists. After a pusher calls `revokePusher()`, the creator can immediately call `allowPushers` again with the identical bytes signature and re-establish the delegation — as long as the deadline has not yet expired. `revokePusher()` is therefore ineffective for the entire remaining lifetime of the signed consent, which can be arbitrarily long.

---

### Finding Description

`allowPushers` recovers the pusher's EIP-191 consent signature and writes `namespaceRemapping[pusher] = msg.sender`:

```solidity
// CompressedOracle.sol lines 192-212
function allowPushers(uint256 deadline, address[] calldata pushers, bytes[] memory signatures) external {
    _ensureDeadline(deadline);
    ...
    bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
        keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
    );
    require(pusher == ECDSA.recover(hash, signatures[i]));
    namespaceRemapping[pusher] = msg.sender;
``` [1](#0-0) 

The signature is bound to `(chainid, address(this), deadline, pusher, creator)`. There is no nonce, no used-signature set, and no per-pusher revocation counter. The only replay gate is `_ensureDeadline(deadline)`, which only rejects calls made **after** the deadline — not repeated calls **before** it. [2](#0-1) 

`revokePusher()` clears the mapping in a single storage write:

```solidity
// CompressedOracle.sol lines 238-243
function revokePusher() external {
    address creator = namespaceRemapping[msg.sender];
    if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
    namespaceRemapping[msg.sender] = address(0);
    emit PusherRevoked(msg.sender, creator);
}
``` [3](#0-2) 

Because the original consent bytes are public (emitted on-chain or visible in the mempool), the creator can call `allowPushers` again in the very next block with the same `deadline` and the same `signatures[i]`, restoring `namespaceRemapping[pusher] = creator`. This can be repeated indefinitely until the deadline timestamp passes.

The code comment acknowledges the deadline is the sole protection against post-revocation replay:

> "the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it." [4](#0-3) 

But the comment treats the deadline as a complete fix. It is not: the deadline prevents replay **after** it expires; it does nothing to prevent replay **before** it expires. A creator who issued a consent with `deadline = block.timestamp + 365 days` can replay the signature for up to a year after the pusher revokes.

---

### Impact Explanation

**Broken invariant — pusher cannot exit delegation within the deadline window.**

The `revokePusher()` function is documented and designed to let a pusher immediately terminate their delegation. That guarantee is broken. Concretely:

1. **Pusher's own-namespace writes are blocked.** After revocation, the pusher's `fallback()` pushes should land in their own namespace. The creator's replay re-routes them back into the creator's namespace. The pusher cannot write into their own feeds without the creator's cooperation.

2. **Stale-feed DoS on the pool.** If the pusher stops pushing entirely to avoid contributing to the creator's namespace, the creator's compressed-oracle feeds go stale. Any pool whose `PriceProvider` reads those feeds will have `getSafePrice` revert on the `maxTimeDrift` check, making swaps and liquidity operations unusable for the duration.

3. **Forced contribution to a namespace the pusher has repudiated.** If the pusher's key is later suspected compromised, the pusher calls `revokePusher()` as an emergency stop. The creator's replay re-enables the compromised key to write into the creator's production namespace, defeating the emergency stop.

This satisfies the **broken core pool functionality** and **admin-boundary break** impact criteria: the pusher's revocation right — the only unprivileged exit from a delegation — is bypassed by the creator without any privileged role.

---

### Likelihood Explanation

- The creator already holds the consent signature bytes (they submitted the original `allowPushers` call).
- Replaying requires a single cheap transaction with no new cryptographic material.
- Deadlines are typically set days to months in the future (the test suite uses `block.timestamp + 1 days`), giving a large replay window.
- No on-chain monitoring is needed; the creator can front-run or back-run the pusher's `revokePusher()` in the same block.

---

### Recommendation

Add a per-pusher revocation nonce or a used-signature bitmap so that each consent signature can only establish delegation once:

```solidity
// Option A: per-pusher nonce
mapping(address => uint256) public pusherNonce;

// In allowPushers, include the nonce in the signed message:
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender, pusherNonce[pusher]))

// In revokePusher, increment the nonce:
pusherNonce[msg.sender]++;
namespaceRemapping[msg.sender] = address(0);
```

This ensures that after `revokePusher()` increments the nonce, the old signature (which committed to the previous nonce value) is permanently invalid, regardless of whether the deadline has passed.

---

### Proof of Concept

```solidity
// 1. Pusher signs consent with a far-future deadline.
uint256 deadline = block.timestamp + 365 days;
bytes memory sig = pusherSign(deadline, pusherAddr, creatorAddr);

// 2. Creator establishes delegation.
vm.prank(creator);
oracle.allowPushers(deadline, toArray(pusherAddr), toArray(sig));
// namespaceRemapping[pusher] == creator ✓

// 3. Pusher revokes.
vm.prank(pusher);
oracle.revokePusher();
// namespaceRemapping[pusher] == address(0) ✓

// 4. Creator replays the IDENTICAL signature — no new cryptographic material needed.
vm.prank(creator);
oracle.allowPushers(deadline, toArray(pusherAddr), toArray(sig));
// namespaceRemapping[pusher] == creator again — revocation undone ✓

// 5. Pusher's fallback pushes now land in creator's namespace, not their own.
vm.prank(pusher);
(bool ok,) = address(oracle).call(wordAt(slotId, pos, raw, tsMs));
assertTrue(ok);
// oracle.getOracleData(feedIdOf(creator, slotId, pos)).price != 0  ← creator namespace written
// oracle.getOracleData(feedIdOf(pusher,  slotId, pos)).price == 0  ← pusher's own namespace empty
```

The replay succeeds because `_ensureDeadline` only checks `block.timestamp <= deadline`, and the signature bytes are identical to the original — no malleation or new signing required. [5](#0-4) [6](#0-5)

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
