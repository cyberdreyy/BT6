### Title
`allowPushers` consent signature has no nonce, enabling creator to replay a revoked pusher's consent and permanently redirect oracle slot writes into a pool-backing namespace — (File: `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` verifies an EIP-191 signature over `(chainid, oracle, deadline, pusher, creator)` but includes **no nonce**. After a pusher calls `revokePusher()`, the creator can immediately replay the original consent signature — still valid because the deadline has not expired — to re-set `namespaceRemapping[pusher] = creator`. The pusher's self-revocation is silently undone, and the pusher's slot writes continue to land in the creator's namespace (which backs a pool's price feed) against the pusher's will.

---

### Finding Description

`allowPushers` in `CompressedOracle.sol` constructs the signed digest as:

```solidity
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
namespaceRemapping[pusher] = msg.sender;
``` [1](#0-0) 

The signed tuple is `(chainid, oracle, deadline, pusher, creator)`. There is **no nonce, no consumed-flag, and no per-invocation counter**. The only replay barrier is the deadline.

`revokePusher()` clears the mapping:

```solidity
function revokePusher() external {
    address creator = namespaceRemapping[msg.sender];
    if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
    namespaceRemapping[msg.sender] = address(0);
    emit PusherRevoked(msg.sender, creator);
}
``` [2](#0-1) 

Because the signature is not consumed or invalidated on revocation, the creator can call `allowPushers` again with the **identical** signature (deadline still in the future) to re-write `namespaceRemapping[pusher] = creator`. The pusher's revocation is undone in the same block.

The codebase's own documentation acknowledges this gap:

> "the signed consent has no data timestamp, so an undated signature could re-establish a delegation after the pusher revoked it" [3](#0-2) 

The deadline is described as the mitigation, but the deadline only prevents replay **after** it expires. Within the deadline window — which the creator controls when calling `allowPushers` and can set to any future time — the creator can replay the signature indefinitely.

---

### Impact Explanation

`CompressedOracleV1` is the oracle backing pools through `AnchoredPriceProvider`. A pusher's `fallback()` slot writes land in the namespace that `namespaceRemapping[pusher]` resolves to: [4](#0-3) 

When the creator keeps re-establishing the delegation, the pusher's price updates continue to flow into the creator's namespace — and thus into the pool's price feed — against the pusher's will. The pusher's only recourse is to **stop pushing data entirely** until the deadline expires. This:

1. Breaks the core security invariant of `revokePusher()`: that a pusher can immediately end their delegation.
2. Allows a creator to hold a pusher's oracle namespace writes hostage for the full deadline window (which can be set to days or weeks).
3. If the pusher is trying to stop providing data (e.g., because they have detected a compromise or want to switch to a different creator's namespace), the creator can force the pusher's data to keep flowing to the pool, potentially keeping stale or unwanted prices active in the pool's feed.

The `AnchoredPriceProvider` reads the oracle via `offchainOracle.price(feedId, pool)`, which resolves to the creator's namespace slot: [5](#0-4) 

---

### Likelihood Explanation

**High.** Any creator who has received a valid consent signature from a pusher can replay it within the deadline window. No special privileges are required — just the original signature and a non-expired deadline. The creator sets the deadline when calling `allowPushers`, so the window can be arbitrarily long (up to any future timestamp the pusher agreed to sign).

---

### Recommendation

Add a per-pusher nonce to the signed message and increment it on each successful `allowPushers` call. In `revokePusher()`, also increment the nonce to invalidate any existing signatures:

```solidity
mapping(address => uint256) public pusherNonce;

// In allowPushers:
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender, pusherNonce[pusher]))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
pusherNonce[pusher]++;
namespaceRemapping[pusher] = msg.sender;

// In revokePusher():
namespaceRemapping[msg.sender] = address(0);
pusherNonce[msg.sender]++; // invalidate all existing consent signatures
emit PusherRevoked(msg.sender, creator);
```

---

### Proof of Concept

```
1. Pusher signs: sig = sign(keccak256(abi.encode(chainid, oracle, deadline=T+7days, pusher, creator)))
2. Creator calls allowPushers(T+7days, [pusher], [sig])
   → namespaceRemapping[pusher] = creator  ✓
3. Pusher calls revokePusher()
   → namespaceRemapping[pusher] = address(0)  ✓ (pusher believes they are free)
4. Creator calls allowPushers(T+7days, [pusher], [sig])  ← SAME signature, still valid
   → namespaceRemapping[pusher] = creator  ← delegation silently re-established
5. Pusher's next fallback() push lands in creator's namespace (feeds the pool)
6. Steps 3–4 repeat indefinitely until T+7days expires
```

The pusher's `revokePusher()` provides no actual protection within the deadline window. Every push the pusher makes — even intending to write to their own namespace — is redirected to the creator's pool-backing namespace.

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L24-23)
```text

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

**File:** smart-contracts-poc/contracts/oracles/compressed/docs/en/slot-structure.md (L27-29)
```markdown
Delegation (`allowPushers`) requires each pusher's EIP-191 signature (and a deadline:
the signed consent has no data timestamp, so an undated signature could re-establish a
delegation after the pusher revoked it).
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L277-295)
```text
    function _readLeg(bytes32 feedId)
        internal returns (uint256 mid, uint256 spreadBps, uint256 refTime, bool ok)
    {
        (mid, spreadBps, , refTime) = IPricedOracle(address(offchainOracle)).price(feedId, msg.sender);

        // Stale reference → not ok. Clamping to a stale anchor is the one false-safety case.
        if (_isStale(refTime, block.timestamp, MAX_REF_STALENESS)) return (mid, spreadBps, refTime, false);

        // Basic validity — mid positive, spreadBps not the stalled/off-hours marker (the Chainlink oracle
        // writes spreadBps = ORACLE_BPS when an RWA market is closed).
        if (mid == 0 || spreadBps >= ORACLE_BPS) return (mid, spreadBps, refTime, false);

        // Per-leg price guard.
        (uint128 guardMin, uint128 guardMax) = offchainOracle.priceGuard(feedId);
        guardMax = guardMax == 0 ? type(uint128).max : guardMax;
        if (mid < guardMin || mid > guardMax) return (mid, spreadBps, refTime, false);

        ok = true;
    }
```
