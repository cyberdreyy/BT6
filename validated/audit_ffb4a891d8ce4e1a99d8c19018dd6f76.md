### Title
`revokePusher` State Ambiguity Allows Creator to Replay Delegation Signature After Pusher Revocation, Feeding Prices Into Pool — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`namespaceRemapping[pusher] == address(0)` encodes two semantically distinct states — **"never delegated"** and **"revoked"** — with the same sentinel value. Because `allowPushers` has no revocation-awareness, a creator can replay the pusher's original EIP-191 consent signature (before its deadline) to silently re-establish delegation after the pusher called `revokePusher()`. The pusher's prices then continue flowing into the creator's namespace and, through `AnchoredPriceProvider`, into live pool swaps.

---

### Finding Description

`CompressedOracleV1` stores delegation state in a single mapping:

```solidity
mapping(address => address) public namespaceRemapping;
``` [1](#0-0) 

The three logical states of a pusher are:

| Logical state | Stored value |
|---|---|
| Never delegated | `address(0)` |
| Delegated to creator | `creator` |
| Revoked | `address(0)` ← **collision** |

`revokePusher()` writes `address(0)` back:

```solidity
namespaceRemapping[msg.sender] = address(0);
``` [2](#0-1) 

`allowPushers` has no guard against re-delegating a previously revoked pusher. It only checks the deadline and the ECDSA signature:

```solidity
require(pusher == ECDSA.recover(hash, signatures[i]));
namespaceRemapping[pusher] = msg.sender;
``` [3](#0-2) 

Because the signature commits only to `(block.chainid, address(this), deadline, pusher, msg.sender)` — with no nonce or revocation counter — the **exact same bytes** that authorized the original delegation remain valid until the deadline expires. After `revokePusher()` sets the mapping back to `address(0)`, the creator calls `allowPushers` again with the identical `(deadline, pusher, signature)` tuple, and the mapping is overwritten to `creator` again.

The code's own comment acknowledges the replay risk but frames it only as an argument for requiring a deadline at all — it does not address intra-deadline replay after revocation:

> *"an undated signature could re-establish a delegation AFTER the pusher revoked it"* [4](#0-3) 

The fallback push path resolves the creator from the mapping at call time:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [5](#0-4) 

After the creator replays the delegation, every subsequent push from the pusher's address lands in the **creator's namespace** again, not the pusher's own namespace as the pusher intended.

---

### Impact Explanation

`AnchoredPriceProvider._readLeg` calls `IPricedOracle(address(offchainOracle)).price(feedId, msg.sender)` where `feedId` encodes the creator's address: [6](#0-5) 

If the pusher is an automated keeper that cannot instantly halt (a common deployment pattern), the creator's feed continues to receive live price updates after the pusher believed it had revoked. Those prices pass through `_computeBidAsk` and are returned as `(bid, ask)` to the pool's swap path. A creator who controls the namespace can therefore sustain a price feed the pusher intended to cut off, enabling bad-price execution against pool LPs or traders for the full remaining lifetime of the original signature.

---

### Likelihood Explanation

- The pusher must have signed a consent with a non-trivial future deadline (common for operational convenience).
- The creator must be willing to replay the signature — a single on-chain transaction.
- No privileged role beyond being the original delegating creator is required.
- The pusher has no on-chain mechanism to permanently invalidate the signature before the deadline.

---

### Recommendation

Replace the two-value `address(0)` sentinel with a three-state enum or add a per-pusher revocation nonce that is included in the signed digest:

```solidity
// Option A: revocation nonce in the signature
mapping(address => uint256) public pusherNonce;

// In allowPushers, include nonce in hash:
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender, pusherNonce[pusher]))

// In revokePusher, increment nonce to invalidate all prior signatures:
pusherNonce[msg.sender]++;
namespaceRemapping[msg.sender] = address(0);
```

This mirrors the external report's recommendation: use an enum (or equivalent multi-state representation) so that "Revoked" is a distinct, non-replayable state rather than being aliased to "NotDelegated."

---

### Proof of Concept

```
1. Pusher signs consent: sig = sign(chainid, oracle, deadline=T+365days, pusher, creator)
2. Creator calls allowPushers(T+365days, [pusher], [sig])
   → namespaceRemapping[pusher] = creator  ✓
3. Pusher calls revokePusher()
   → namespaceRemapping[pusher] = address(0)  (pusher believes delegation is dead)
4. Creator calls allowPushers(T+365days, [pusher], [sig])  ← SAME signature, still before deadline
   → require(pusher == ECDSA.recover(hash, sig))  PASSES (no nonce, no revocation flag)
   → namespaceRemapping[pusher] = creator  ← delegation silently restored
5. Pusher's automated keeper pushes price update via fallback()
   → creator = namespaceRemapping[pusher] = creator  (not address(0))
   → price lands in creator's namespace, not pusher's own namespace
6. AnchoredPriceProvider reads feedIdOf(creator, slot, pos) → stale/bad price
   → getBidAndAskPrice() returns (bid, ask) to pool swap → bad-price execution
```

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L29-29)
```text
    mapping(address => address) public namespaceRemapping;
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L186-191)
```text
    /// @notice Delegates pusher wallets into the caller's namespace. The pusher's EIP-191
    ///         signature is REQUIRED — without it anyone could remap a foreign pusher
    ///         wallet into their own namespace and silently swallow its pushes. The
    ///         deadline is likewise required: the signed consent carries no timestamp of
    ///         its own, so an undated signature could re-establish a delegation AFTER the
    ///         pusher revoked it.
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L207-209)
```text
            require(pusher == ECDSA.recover(hash, signatures[i]));

            namespaceRemapping[pusher] = msg.sender;
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L241-241)
```text
        namespaceRemapping[msg.sender] = address(0);
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L315-316)
```text
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L280-283)
```text
        (mid, spreadBps, , refTime) = IPricedOracle(address(offchainOracle)).price(feedId, msg.sender);

        // Stale reference → not ok. Clamping to a stale anchor is the one false-safety case.
        if (_isStale(refTime, block.timestamp, MAX_REF_STALENESS)) return (mid, spreadBps, refTime, false);
```
