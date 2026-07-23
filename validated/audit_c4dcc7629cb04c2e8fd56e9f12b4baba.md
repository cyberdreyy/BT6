### Title
`revokePusher` is ineffective within the deadline window due to signature replay in `allowPushers` — (File: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol)

---

### Summary

`CompressedOracleV1.allowPushers` accepts a pusher's EIP-191 signature to establish namespace delegation but has no nonce or used-signature tracking. After a pusher calls `revokePusher()` to clear their delegation, the creator can immediately replay the original signature (while the deadline is still valid) to silently re-establish it. The code comment explicitly claims the deadline prevents this replay, but the deadline only blocks signatures *after* expiry — it does nothing to prevent re-use *before* expiry.

---

### Finding Description

`allowPushers` signs over `(block.chainid, address(this), deadline, pusher, msg.sender)` and writes `namespaceRemapping[pusher] = msg.sender`: [1](#0-0) 

`revokePusher` clears the mapping: [2](#0-1) 

There is no nonce, no per-signature invalidation flag, and no check that the mapping is currently zero before writing. The code comment at line 189–191 explicitly claims the deadline prevents re-establishment after revocation: [3](#0-2) 

That claim is false. `_ensureDeadline` only checks `block.timestamp <= deadline`: [4](#0-3) 

A creator who holds the pusher's original signature can call `allowPushers` again with the identical `(deadline, pusher, sig)` tuple at any point before the deadline expires, atomically undoing the revocation. The pusher receives no on-chain signal that the delegation was re-established; they may continue pushing believing their data lands in their own namespace, while it silently flows into the creator's namespace.

The `fallback` push path resolves the namespace at call time: [5](#0-4) 

So every push the pusher makes after the creator's replay lands in the creator's namespace, not the pusher's own.

---

### Impact Explanation

A malicious creator can permanently suppress a pusher's revocation for the entire deadline window (up to the deadline the pusher originally signed). The pusher's price data continues to be attributed to the creator's namespace and consumed by any pool backed by that feed. If the creator is operating a pool with a manipulated or stale price feed, the pusher's continued (unknowing) pushes keep the feed alive and valid-looking, enabling bad-price execution against traders. The invariant documented in the project's own audit target list — "Delegation cleanup must fully remove the authority that later fallback or signed updates would otherwise reuse" — is broken.

---

### Likelihood Explanation

Medium. The creator must have retained the original consent signature (trivially true: they submitted it on-chain and can read it from calldata). The deadline must not yet have expired (creators are incentivised to use long deadlines for operational convenience). The pusher must continue pushing after revoking (likely if they believe the revocation succeeded). No special privileges or external conditions are required beyond the creator calling a public function.

---

### Recommendation

Track consumed delegation signatures with a `mapping(bytes32 => bool) usedDelegations` keyed on the signature hash (or on `keccak256(abi.encode(deadline, pusher, creator))`). In `allowPushers`, revert if the hash has already been used, and mark it used on first acceptance. This mirrors the standard EIP-2612 nonce pattern and makes every consent single-use, so a revocation cannot be undone by replaying the original signature.

---

### Proof of Concept

```
1. Pusher signs: digest = keccak256(abi.encode(chainid, oracle, deadline=T+1day, pusher, creator))
2. Creator calls allowPushers(T+1day, [pusher], [sig])
   → namespaceRemapping[pusher] = creator  ✓

3. Pusher calls revokePusher()
   → namespaceRemapping[pusher] = address(0)  (pusher believes delegation is gone)

4. Creator calls allowPushers(T+1day, [pusher], [sig])  ← SAME signature, still valid
   → namespaceRemapping[pusher] = creator  (delegation silently restored)

5. Pusher continues pushing (e.g. slot 0, position 0, fresh timestamp)
   → fallback resolves creator = namespaceRemapping[pusher] = creator
   → data lands in creator's namespace, not pusher's own

6. Pool backed by creator's feed reads the pusher's price data as if it were
   the creator's authorised quote → bad-price execution against pool traders.
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L315-321)
```text
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;

        assembly ("memory-safe") {
            end := calldatasize()
            namespace := shl(96, creator) // [creator:20][zeros:12]
        }
```

**File:** smart-contracts-poc/contracts/oracles/compressed/OracleBase.sol (L124-126)
```text
    function _ensureDeadline(uint256 deadline) internal view virtual {
        require(block.timestamp <= deadline, DeadlineExceeded());
    }
```
