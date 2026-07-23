Looking at the `CompressedOracleV1` delegation path, specifically `allowPushers`, `revokePusher`, and the EIP-191 signature domain.

### Title
`allowPushers` Consent Signature Is Replayable Within the Deadline Window, Making `revokePusher()` Ineffective — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracleV1.allowPushers` verifies a pusher's EIP-191 consent signature but contains no mechanism to invalidate that signature after it has been used once. A creator who holds a valid (not-yet-expired) consent signature can call `allowPushers` repeatedly with the same signature to re-establish delegation every time the pusher calls `revokePusher()`. The pusher's self-revocation is therefore completely ineffective until the deadline expires, and the creator can silently redirect all of the pusher's future slot writes into the creator's namespace without the pusher's ongoing consent.

---

### Finding Description

`allowPushers` signs over `(block.chainid, address(this), deadline, pusher, msg.sender)`: [1](#0-0) 

The signature is stateless — there is no nonce, no "used" bitmap, and no per-pusher revocation counter. The only expiry is the deadline. `revokePusher()` clears `namespaceRemapping[msg.sender]` to `address(0)`: [2](#0-1) 

But because the original consent signature is still cryptographically valid (the deadline has not passed), the creator can immediately call `allowPushers` again with the identical `(deadline, [pusher], [sig])` arguments and restore `namespaceRemapping[pusher] = creator`. The code's own comment acknowledges the deadline is the only guard against post-revocation re-establishment: [3](#0-2) 

The comment addresses re-establishment *after* the deadline but is silent on re-establishment *before* it. Within the deadline window the creator can replay the signature an unlimited number of times, making `revokePusher()` a no-op.

The `fallback()` push path resolves the effective namespace at call time: [4](#0-3) 

So every push the pusher makes after their (silently undone) revocation lands in the creator's namespace, not the pusher's own.

---

### Impact Explanation

**Bad-price execution / stale price reaching pools:**

After the pusher calls `revokePusher()` and believes they are pushing into their own namespace, their slot writes are still routed to the creator's namespace. Any pool or price provider reading from `feedIdOf(pusher, slotIndex, positionIndex)` — the pusher's own namespace — receives the last pre-revocation value, which ages indefinitely. Downstream consumers (e.g., `AnchoredPriceProvider`) that enforce `maxTimeDrift` will reject the stale quote, breaking swap execution for pools that depend on the pusher's own feeds. Pools that do not enforce staleness will execute at an arbitrarily old price.

**Consent-boundary break:**

`revokePusher()` is documented as allowing a pusher to permanently exit a delegation. The creator — a semi-trusted, unprivileged party — can silently undo that exit an unlimited number of times within the deadline window, violating the security boundary the revocation function is supposed to enforce.

---

### Likelihood Explanation

- Any creator who obtained a pusher's consent signature with a non-trivial deadline (days to months, which is operationally normal) can exploit this.
- The pusher has no on-chain way to detect that their revocation was undone; they must monitor `PusherAuthorized` events.
- No privileged role is required; the creator is a standard `msg.sender`.
- The pusher cannot shorten the deadline after signing.

Likelihood: **Medium** (requires a malicious creator and a pusher who continues pushing after revocation, but both conditions are realistic in a live oracle ecosystem).

---

### Recommendation

Add a per-pusher revocation nonce to the signature domain:

```solidity
mapping(address => uint256) public pusherNonce;

// In allowPushers:
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(
        block.chainid, address(this), deadline,
        pusher, msg.sender, pusherNonce[pusher]   // ← add nonce
    ))
);

// In revokePusher:
namespaceRemapping[msg.sender] = address(0);
pusherNonce[msg.sender]++;   // ← invalidate all prior signatures
```

Incrementing the nonce on revocation makes every previously issued consent signature immediately invalid, regardless of its deadline.

---

### Proof of Concept

```
1. Pusher P signs: keccak256(abi.encode(chainid, oracle, deadline=T+365d, P, C))
   → sig_P

2. Creator C calls allowPushers(T+365d, [P], [sig_P])
   → namespaceRemapping[P] = C  ✓

3. P calls revokePusher()
   → namespaceRemapping[P] = address(0)  (P believes delegation is gone)

4. C calls allowPushers(T+365d, [P], [sig_P])  ← same signature, still before deadline
   → namespaceRemapping[P] = C  (revocation silently undone)

5. P pushes a slot word via fallback():
   creator = namespaceRemapping[P]  →  C  (not P)
   → write lands in C's namespace, P's own namespace stays stale

6. Any pool reading feedIdOf(P, slotIndex, positionIndex) receives the
   pre-revocation price, which is now arbitrarily stale.
   Pools enforcing maxTimeDrift revert; pools without staleness checks
   execute at the stale price.
```

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L186-192)
```text
    /// @notice Delegates pusher wallets into the caller's namespace. The pusher's EIP-191
    ///         signature is REQUIRED — without it anyone could remap a foreign pusher
    ///         wallet into their own namespace and silently swallow its pushes. The
    ///         deadline is likewise required: the signed consent carries no timestamp of
    ///         its own, so an undated signature could re-establish a delegation AFTER the
    ///         pusher revoked it.
    function allowPushers(uint256 deadline, address[] calldata pushers, bytes[] memory signatures) external {
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L204-210)
```text
            bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
                keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
            );
            require(pusher == ECDSA.recover(hash, signatures[i]));

            namespaceRemapping[pusher] = msg.sender;
            emit PusherAuthorized(pusher, msg.sender);
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L315-321)
```text
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;

        assembly ("memory-safe") {
            end := calldatasize()
            namespace := shl(96, creator) // [creator:20][zeros:12]
        }
```
