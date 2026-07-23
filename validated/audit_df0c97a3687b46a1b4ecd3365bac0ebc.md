### Title
Pusher Delegation Replay Allows Creator to Silently Re-establish Revoked Namespace Mapping — (File: `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` uses a deadline-bounded EIP-191 signature with no nonce. After a pusher calls `revokePusher()` to clear their delegation, the creator can immediately replay the original signature (same `deadline`, same `pusher`, same `creator`) to re-set `namespaceRemapping[pusher] = creator`. The pusher's revocation is silently undone within the deadline window, causing the pusher's subsequent data pushes to land in the creator's namespace rather than their own, feeding unexpected or stale price data to pools consuming that feed.

---

### Finding Description

`allowPushers` signs over `(block.chainid, address(this), deadline, pusher, msg.sender)` with no nonce: [1](#0-0) 

There is no check that prevents re-establishing an already-active or previously-revoked delegation. The only guards are deadline expiry, no-self-remapping, and signature validity. After `revokePusher()` clears `namespaceRemapping[pusher]` to `address(0)`: [2](#0-1) 

…the creator can call `allowPushers` again with the **identical** signature and deadline to re-set `namespaceRemapping[pusher] = creator`. The protocol's own comment acknowledges the underlying risk ("an undated signature could re-establish a delegation AFTER the pusher revoked it") but the deadline only bounds the window — it does not prevent re-establishment within it: [3](#0-2) 

The `fallback` push path resolves the namespace via `namespaceRemapping[msg.sender]`, so a pusher who believes they have revoked will still have their pushes land in the creator's namespace: [4](#0-3) 

The slot layout packs four 48-bit oracle lanes plus a 56-bit timestamp into one 256-bit storage word; an entire slot is overwritten on each push: [5](#0-4) 

A pusher who revokes and then pushes data intended for their own namespace (e.g., a different asset, a stale price, or a sentinel spread) will instead overwrite the creator's slot, corrupting the `(mid, spread0, spread1, timestamp)` tuple that `price()` and `getOracleData()` return to price providers and pools.

---

### Impact Explanation

The corrupted slot value flows directly into the `_price` read path: [6](#0-5) 

Any `PriceProvider` or `AnchoredPriceProvider` consuming the creator's feed will receive the wrong `mid` price or an inflated/sentinel spread, causing pools to execute swaps at a bad bid/ask. This matches the allowed impact gate: **bad-price execution — stale, inverted, or unclamped bid/ask quote reaches a pool swap**.

---

### Likelihood Explanation

- Trigger requires a creator who is willing to replay a previously-issued signature — a semi-trusted but reachable actor.
- The pusher must have signed a consent with a non-trivial future deadline (common in practice to avoid frequent re-signing).
- The pusher must call `revokePusher()` before the deadline expires.
- No privileged role or special setup beyond the normal delegation flow is needed.
- The creator's replay call is a single permissionless transaction.

Likelihood: **Medium**.

---

### Recommendation

Add a per-pusher nonce to the signature domain and increment it on every successful `allowPushers` call. Reject any signature whose nonce does not match the current value:

```solidity
mapping(address => uint256) public pusherNonce;

// In allowPushers:
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(
        block.chainid, address(this),
        pusherNonce[pusher],   // <-- add nonce
        deadline, pusher, msg.sender
    ))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
pusherNonce[pusher]++;         // <-- invalidate old signatures
namespaceRemapping[pusher] = msg.sender;
```

Alternatively, record a per-pusher revocation timestamp and reject any `allowPushers` call that uses a signature issued (deadline) before the most recent revocation.

---

### Proof of Concept

```
1. Creator calls allowPushers(deadline=T+1day, [pusher], [sig])
   → namespaceRemapping[pusher] = creator

2. Pusher calls revokePusher()
   → namespaceRemapping[pusher] = address(0)
   Pusher believes they are now pushing into their own namespace.

3. Creator calls allowPushers(deadline=T+1day, [pusher], [sig])
   with the IDENTICAL signature and deadline (block.timestamp < T+1day)
   → namespaceRemapping[pusher] = creator  (silently restored)

4. Pusher calls oracle.fallback(slotWord) with data for their own asset
   → namespace resolved as creator's → creator's slot overwritten with
     pusher's stale/wrong (mid, spread0, spread1, timestamp)

5. PriceProvider reads creator's feed → bad bid/ask delivered to pool swap
```

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L29-29)
```text
    mapping(address => address) public namespaceRemapping;
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L171-178)
```text
    function _price(bytes32 feedId)
        internal
        view
        returns (uint256 mid, uint256 spread, uint16 spread1, uint256 refTime)
    {
        OracleData memory data = getOracleData(feedId);
        return (uint256(data.price), uint256(data.spread0), data.spread1, data.timestampMs.toSeconds());
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

**File:** smart-contracts-poc/contracts/oracles/compressed/docs/en/slot-structure.md (L54-66)
```markdown
## Updates (Fallback / Signature)

Updates overwrite the entire storage slot value.

- `fallback()` accepts one or more 32-byte slot words (no prefix; calldata length must
  be a non-zero multiple of 32).
- `updateBySignature(feedCreator, newSlotValue, signature)` accepts a slot word signed
  by the feed creator over `keccak256(abi.encode(chainid, oracleAddress, feedCreator,
  newSlotValue))`, and is callable by anyone.

There is **no deadline** on either push path: each word carries its own timestamp and
the per-slot monotonicity check neutralizes replay (a replayed word is "not newer" and
is skipped).
```
