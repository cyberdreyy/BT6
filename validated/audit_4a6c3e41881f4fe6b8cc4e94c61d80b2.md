### Title
Pusher consent signature is replayable unlimited times before deadline, making `revokePusher()` permanently ineffective until expiry — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` contains no nonce, no used-signature bitmap, and no single-use invalidation. A creator who holds a pusher's consent signature can call `allowPushers` with that same signature an unlimited number of times before the deadline, silently re-establishing the delegation every time the pusher calls `revokePusher()`. The pusher's only effective escape is to wait for the deadline to expire.

---

### Finding Description

`allowPushers` verifies a pusher's EIP-191 signature over the tuple `(block.chainid, address(this), deadline, pusher, msg.sender)` and writes `namespaceRemapping[pusher] = msg.sender`: [1](#0-0) 

`_ensureDeadline` only checks `block.timestamp <= deadline`: [2](#0-1) 

There is no nonce, no `usedSignatures` mapping, and no single-use invalidation anywhere in the contract. The same bytes can be passed to `allowPushers` on every block until the deadline timestamp passes.

`revokePusher` clears the mapping: [3](#0-2) 

But because the signature is still cryptographically valid, the creator can immediately replay it and restore `namespaceRemapping[pusher] = creator` in the very next transaction. The code comment on `allowPushers` acknowledges that the deadline is the only replay barrier: [4](#0-3) 

The comment treats the deadline as protection against re-establishment *after* revocation, but it only prevents re-establishment *after the deadline expires* — not before. The pusher's revocation is therefore ineffective for the entire window `[revoke_time, deadline]`.

The `fallback` push path resolves the namespace at call time: [5](#0-4) 

So every push the pusher makes after the creator replays the signature lands in the creator's namespace, not the pusher's own.

---

### Impact Explanation

**Bad-price execution / stale feed reaching a live pool swap.**

If the pusher is an automated off-chain agent (keeper, bot, or delegated contract) that pushes prices into its own namespace to serve its own registered pool, and the creator re-establishes delegation after each revocation, the pusher's price updates are silently redirected to the creator's namespace. The pusher's own namespace receives no updates and goes stale. Any pool reading `feedIdOf(pusher, slotIndex, positionIndex)` will receive a zero or outdated `timestampMs`, which every consumer already treats as stale — causing the pool's swap to revert or execute at a bad price.

The creator does not need to force the pusher to push; the pusher's existing automated pipeline does the work. The creator only needs to replay the signature once per revocation, which costs a single transaction.

---

### Likelihood Explanation

- The pusher must have previously signed a consent with a non-trivial deadline (e.g., 1 day, 1 week, or longer — common for operational convenience).
- The creator must retain the signature bytes (trivially available from the original `allowPushers` calldata on-chain).
- The creator replays the signature in one transaction immediately after each `revokePusher` call.
- No privileged access is required; `allowPushers` is fully permissionless for any `msg.sender` who holds a valid signature.

---

### Recommendation

Track consumed signatures with a `mapping(bytes32 => bool) private _usedDelegationSignatures` and mark each signature hash as used on first acceptance:

```solidity
bytes32 sigHash = keccak256(signatures[i]);
require(!_usedDelegationSignatures[sigHash], SignatureAlreadyUsed());
_usedDelegationSignatures[sigHash] = true;
```

Alternatively, include a monotonically increasing per-pusher nonce in the signed payload so that each consent can only be accepted once and a new signature is required to re-establish after revocation.

---

### Proof of Concept

```
1. Pusher signs consent:
   sig = sign(keccak256(abi.encode(chainid, oracle, deadline=T+365days, pusher, creator)))

2. Creator calls allowPushers(T+365days, [pusher], [sig])
   → namespaceRemapping[pusher] = creator  ✓

3. Pusher calls revokePusher()
   → namespaceRemapping[pusher] = address(0)  ✓ (pusher believes they are free)

4. Creator calls allowPushers(T+365days, [pusher], [sig])  // SAME sig, no nonce check
   → namespaceRemapping[pusher] = creator  ✓ (delegation silently restored)

5. Pusher's automated bot pushes slot word → lands in creator namespace, not pusher's own
   → feedIdOf(pusher, slot, pos).timestampMs stays 0 → pusher's pool reads stale price

6. Steps 3–5 repeat unlimited times until block.timestamp > T+365days
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L192-211)
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

**File:** smart-contracts-poc/contracts/oracles/compressed/OracleBase.sol (L124-126)
```text
    function _ensureDeadline(uint256 deadline) internal view virtual {
        require(block.timestamp <= deadline, DeadlineExceeded());
    }
```
