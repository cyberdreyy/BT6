### Title
`revokePusher` self-revocation is rendered ineffective within the deadline window by signature replay in `allowPushers` — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

### Summary

`allowPushers` uses a deadline-bound EIP-191 signature to establish pusher delegations. The code comment explicitly states the deadline is required to prevent re-establishment of delegation **after** a pusher revokes it. However, the same signature can be replayed an unlimited number of times within the deadline window, allowing the creator to immediately re-establish a delegation the pusher just cancelled via `revokePusher()`. The safety mechanism the code documents as working does not work.

### Finding Description

The `allowPushers` function signs over `(block.chainid, address(this), deadline, pusher, msg.sender)`. There is no nonce and no revocation flag in the signed message. [1](#0-0) 

The `_ensureDeadline` check only verifies `block.timestamp <= deadline`. [2](#0-1) 

So the same `(deadline, sig)` pair is valid for every call to `allowPushers` until the deadline expires. When a pusher calls `revokePusher()`, it sets `namespaceRemapping[pusher] = address(0)`: [3](#0-2) 

But the creator can immediately call `allowPushers` again with the **identical** signature to restore `namespaceRemapping[pusher] = creator`. The code's own NatSpec comment directly contradicts this behaviour:

> "The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it." [4](#0-3) 

The comment is wrong: the deadline only prevents replay **after** it expires. During the entire deadline window the signature is unconditionally reusable, so `revokePusher` provides no durable protection.

The `fallback` push path resolves the namespace at call time: [5](#0-4) 

Once the delegation is re-established, every subsequent push from the compromised pusher key lands in the creator's namespace again, overwriting live feed slots that price providers and pools read.

### Impact Explanation

A compromised pusher key can push arbitrary prices into the creator's namespace. The pusher calls `revokePusher()` to stop the bleeding. The creator — unaware of the compromise, or acting maliciously — replays the original `allowPushers` signature (still within its deadline window) to re-establish the delegation. The attacker's pushes resume, writing bad prices into the creator's namespace. Price providers backed by `CompressedOracle` (`PriceProvider`, `PriceProviderL2`) read those slots via `getOracleData` → `_price` → `price(feedId, pool)` and deliver the corrupted bid/ask to pools during swaps. Pools receive a bad-price quote and execute swaps at the wrong rate, causing direct loss of user principal or pool insolvency.

### Likelihood Explanation

Medium. The creator must actively call `allowPushers` a second time. In practice, automated keeper infrastructure that monitors delegation state and re-establishes it on any clearance (treating a revocation as an accidental reset) makes this trivially exploitable. Even without automation, a malicious creator can force a pusher to remain delegated against their will for the full deadline window (which can be set to any future timestamp).

### Recommendation

Add a per-pusher revocation nonce or a `revokedAt` timestamp to the oracle state. Include it in the signed message so that a signature produced before a revocation cannot be replayed after it:

```solidity
// In the signed hash:
keccak256(abi.encode(
    block.chainid,
    address(this),
    deadline,
    pusher,
    msg.sender,
    pusherRevocationNonce[pusher]   // ← new field
))
// In revokePusher():
pusherRevocationNonce[msg.sender]++;
namespaceRemapping[msg.sender] = address(0);
```

Alternatively, require the pusher to co-sign any re-establishment after a self-revocation, or track a `revokedByPusher` flag that `allowPushers` must explicitly clear with a fresh pusher signature.

### Proof of Concept

```
t=0:   pusher signs consent: sig = sign(chainid, oracle, deadline=T+1day, pusher, creator)
t=1:   creator calls allowPushers(T+1day, [pusher], [sig])
         → namespaceRemapping[pusher] = creator  ✓

t=2:   pusher's key is compromised; attacker pushes bad prices into creator namespace

t=3:   pusher calls revokePusher()
         → namespaceRemapping[pusher] = 0        ✓ (attacker's pushes now go to pusher's own ns)

t=4:   creator calls allowPushers(T+1day, [pusher], [sig])   ← SAME signature, deadline not expired
         → namespaceRemapping[pusher] = creator  ✗ (revocation undone)

t=5:   attacker resumes pushing bad prices into creator namespace
         → pools read corrupted bid/ask → bad-price swap execution
```

The `_ensureDeadline` gate passes at t=4 because `block.timestamp < T+1day`. No new pusher consent is required. The `revokePusher` call at t=3 produced zero durable effect. [6](#0-5) [7](#0-6) [2](#0-1)

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
