### Title
Creator Re-Establishes Pusher Delegation After Self-Revocation Using Unexpired Signature, Redirecting Price Data and Staling Dependent Pools - (File: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol)

---

### Summary

`CompressedOracle.allowPushers()` does not invalidate a pusher's previously signed consent when the pusher self-revokes via `revokePusher()`. A creator holding a valid, non-expired signature can immediately re-establish `namespaceRemapping[pusher] = creator` after the pusher clears it, making `revokePusher()` ineffective for the entire deadline window. This is the direct structural analog to `giveLoan()` compounding: just as a lender repeatedly re-invokes `giveLoan()` to compound debt the borrower cannot escape, a creator repeatedly re-invokes `allowPushers()` with the same signature to re-capture a pusher who has explicitly revoked, redirecting that pusher's price data away from the namespace pools depend on and causing those pools to receive stale quotes.

---

### Finding Description

`allowPushers` verifies the pusher's EIP-191 consent signature and unconditionally overwrites `namespaceRemapping[pusher]`:

```solidity
// CompressedOracle.sol lines 192–212
function allowPushers(uint256 deadline, address[] calldata pushers, bytes[] memory signatures) external {
    _ensureDeadline(deadline);
    ...
    bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
        keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
    );
    require(pusher == ECDSA.recover(hash, signatures[i]));
    namespaceRemapping[pusher] = msg.sender;   // ← unconditional overwrite
    ...
}
```

`revokePusher` clears the mapping:

```solidity
// CompressedOracle.sol lines 238–243
function revokePusher() external {
    address creator = namespaceRemapping[msg.sender];
    if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
    namespaceRemapping[msg.sender] = address(0);
    emit PusherRevoked(msg.sender, creator);
}
```

The code comment on `allowPushers` explicitly acknowledges the risk: *"an undated signature could re-establish a delegation AFTER the pusher revoked it"* — and states the deadline is the guard. But the deadline only prevents re-establishment **after** it expires; before expiry the same signature is accepted an unlimited number of times. There is no nonce, no used-signature registry, and no check that `namespaceRemapping[pusher]` is already zero or already points to a different creator.

**Attack path (two variants):**

*Variant 1 — Revocation loop:*
1. Pusher P signs consent for creator A, `deadline = now + 1 day`.
2. Creator A calls `allowPushers(deadline, [P], [sig_A])` → `namespaceRemapping[P] = A`.
3. P calls `revokePusher()` → `namespaceRemapping[P] = 0`.
4. Creator A immediately calls `allowPushers(deadline, [P], [sig_A])` again → `namespaceRemapping[P] = A` again.
5. Steps 3–4 repeat; P cannot escape until the deadline expires.

*Variant 2 — Re-delegation hijack (higher impact):*
1. P signs consent for creator A, `deadline_A = now + 1 day`.
2. Creator A establishes delegation: `namespaceRemapping[P] = A`.
3. P signs consent for creator B (a legitimate pool), `deadline_B = now + 2 days`.
4. Creator B establishes delegation: `namespaceRemapping[P] = B`.
5. Creator A calls `allowPushers(deadline_A, [P], [sig_A])` → `namespaceRemapping[P] = A` again.
6. P's fallback pushes now land in A's namespace instead of B's.
7. Pools registered against B's feed IDs (`feedIdOf(B, slotIndex, positionIndex)`) receive price 0 / timestamp 0 — the never-pushed sentinel — on every read.

The `fallback` push path resolves the namespace at call time:

```solidity
// CompressedOracle.sol lines 315–316
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
```

So every push P makes after step 5 writes into A's namespace, leaving B's slots permanently stale.

---

### Impact Explanation

Pools that registered against B's feed IDs call `price(feedId, pool)` → `getOracleData(feedId)` → `_loadSlotLayout`. The slot for `feedIdOf(B, slotIndex, positionIndex)` was never written, so `timestampMs = 0`. Every price provider that reads this feed (`PriceProvider`, `ProtectedPriceProvider`, `AnchoredPriceProvider`) runs a staleness check:

```solidity
// PriceProvider.sol / ProtectedPriceProvider.sol
if (_isStale(refTime, block.timestamp, MAX_TIME_DELTA)) {
    return (0, type(uint128).max);   // stall sentinel
}
```

`getBidAndAskPrice()` then reverts with `FeedStalled`. Every swap through those pools is blocked for the entire deadline window — up to 7 days if the creator chose the maximum allowed deadline. This is broken core pool functionality (unusable swap flows) with no direct path to recovery until the deadline expires and the pusher can re-delegate cleanly.

---

### Likelihood Explanation

- **Trigger is semi-trusted / valid participant**: the creator holds a legitimately obtained signature; no forgery is required.
- **Permissionless re-invocation**: `allowPushers` is public; the creator pays only gas.
- **Window can be long**: deadlines up to 7 days are plausible in production.
- **Detection is non-trivial**: the pusher must actively monitor `namespaceRemapping[pusher]` on-chain after every revocation attempt.
- **Motivation exists**: a creator whose pool depends on a specific pusher's data has economic incentive to prevent that pusher from re-delegating to a competitor.

---

### Recommendation

Add a per-pusher revocation nonce or a used-signature registry so that a signature cannot be replayed after the pusher has revoked:

```solidity
mapping(address => uint256) public pusherRevocationNonce;

// In revokePusher():
pusherRevocationNonce[msg.sender]++;

// In allowPushers(), include the nonce in the signed hash:
keccak256(abi.encode(
    block.chainid, address(this), deadline,
    pusher, msg.sender,
    pusherRevocationNonce[pusher]   // ← binds consent to current revocation epoch
))
```

Alternatively, record the revocation timestamp and reject any `allowPushers` call whose deadline predates the most recent revocation.

---

### Proof of Concept

```solidity
// Foundry test sketch
function testCreatorReestablishesDelegationAfterRevoke() public {
    uint256 deadline = block.timestamp + 1 days;

    // Pusher signs consent for creatorA
    bytes32 digest = MessageHashUtils.toEthSignedMessageHash(
        keccak256(abi.encode(block.chainid, address(oracle), deadline, pusher, creatorA))
    );
    (uint8 v, bytes32