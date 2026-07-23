### Title
`allowPushers` Signature Replay Bypasses `revokePusher()`, Locking Pusher Out of Own Namespace — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracleV1.allowPushers` verifies a pusher's EIP-191 consent signature and writes `namespaceRemapping[pusher] = creator`. The function's own NatSpec comment acknowledges that the deadline exists specifically to prevent a creator from re-establishing a delegation after the pusher revokes it. However, no nonce or used-signature bitmap is tracked, so the identical signature is replayable by the creator an unlimited number of times before the deadline expires. A pusher who calls `revokePusher()` can have their revocation immediately undone by the creator, rendering the revocation mechanism ineffective and permanently locking the pusher out of their own namespace for the lifetime of the deadline.

---

### Finding Description

`allowPushers` constructs and verifies the following signed digest:

```solidity
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
namespaceRemapping[pusher] = msg.sender;
``` [1](#0-0) 

The NatSpec comment directly above the function states:

> "The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it." [2](#0-1) 

The comment's claim is false. The deadline is a time bound, not a one-time-use token. Because no nonce, used-signature set, or per-pusher revocation counter is stored, the creator can call `allowPushers` with the exact same `(deadline, pusher, signature)` tuple immediately after the pusher calls `revokePusher()`:

```solidity
function revokePusher() external {
    address creator = namespaceRemapping[msg.sender];
    if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
    namespaceRemapping[msg.sender] = address(0);   // cleared here
    emit PusherRevoked(msg.sender, creator);
}
``` [3](#0-2) 

The creator's replay call passes all checks (deadline not expired, signature valid, pusher ≠ msg.sender) and overwrites `namespaceRemapping[pusher]` back to the creator's address. The pusher's revocation is silently undone.

The fallback push path resolves the namespace from `namespaceRemapping`:

```solidity
function allowContractPushers(address[] calldata pushers) external {
    ...
    namespaceRemapping[pusher] = msg.sender;
``` [4](#0-3) 

So every subsequent fallback push by the pusher continues to land in the creator's namespace, not the pusher's own. The pusher has no way to write into their own namespace until the deadline timestamp passes.

---

### Impact Explanation

- **Stale prices in pools using the pusher's own namespace.** If the pusher wants to serve a pool that reads from `feedIdOf(pusher, slotIndex, positionIndex)`, they must push into their own namespace. While the delegation is re-established against their will, every push is redirected to the creator's namespace. The pusher's own namespace receives no updates, so any `PriceProvider` or `AnchoredPriceProvider` bound to the pusher's feed IDs returns a stale price. A pool swap that consumes that stale quote executes at a bad price.

- **Pusher's only escape is to stop pushing entirely.** If the pusher stops pushing to avoid feeding the creator's namespace, the creator's namespace also goes stale. Either outcome (stale pusher namespace or stale creator namespace) is a bad-price execution path for pools that depend on either feed.

This matches the Allowed Impact Gate criterion: **bad-price execution — stale bid/ask quote reaches a pool swap**.

---

### Likelihood Explanation

- `allowPushers` is a public, permissionless function callable by any address.
- A creator who has already received a signed consent holds the signature indefinitely.
- Deadlines are set by the creator at call time; nothing prevents a creator from requesting a 1-year deadline when first establishing the delegation.
- The replay requires only a single transaction from the creator immediately after the pusher's `revokePusher()` transaction is mined.
- No special privileges, no front-running window beyond a single block, no additional setup.

Likelihood: **High** — any creator who holds a valid, unexpired signature can execute this at will.

---

### Recommendation

Add a per-pusher revocation nonce to the signed digest and increment it on every `revokePusher()` call:

```solidity
mapping(address pusher => uint256) public revocationNonce;

// In allowPushers:
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(
        block.chainid, address(this), deadline,
        pusher, msg.sender,
        revocationNonce[pusher]   // ← added
    ))
);

// In revokePusher:
namespaceRemapping[msg.sender] = address(0);
revocationNonce[msg.sender]++;   // ← invalidates all prior signatures
```

Any signature issued before the nonce increment is cryptographically distinct from one issued after, so a replayed pre-revocation signature will fail ECDSA recovery.

---

### Proof of Concept

```
1. block.timestamp = T
   deadline = T + 365 days

2. Pusher signs:
   digest = keccak256(abi.encode(chainid, oracle, deadline, pusher, creatorA))
   sig = ECDSA.sign(pusherKey, digest)

3. creatorA calls allowPushers(deadline, [pusher], [sig])
   → namespaceRemapping[pusher] = creatorA  ✓

4. Pusher calls revokePusher()
   → namespaceRemapping[pusher] = address(0)  ✓ (revocation succeeds)

5. creatorA immediately calls allowPushers(deadline, [pusher], [sig])
   with the IDENTICAL sig from step 2
   → deadline check: T + 365 days > block.timestamp  ✓
   → ECDSA.recover(hash, sig) == pusher  ✓  (same hash, same sig)
   → namespaceRemapping[pusher] = creatorA  ← revocation undone

6. Pusher's fallback push now lands in creatorA's namespace again.
   Pusher's own namespace (feedIdOf(pusher, ...)) receives no update.
   Any pool reading feedIdOf(pusher, ...) gets a stale price.
   Pool swap executes at the stale bid/ask.
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L217-232)
```text
    function allowContractPushers(address[] calldata pushers) external {
        uint256 l = pushers.length;
        for (uint256 i; i < l; i++) {
            address pusher = pushers[i];

            if (pusher == msg.sender) {
                revert NoSelfRemapping();
            }

            (bool ok, bytes memory res) = pusher.staticcall(abi.encodeWithSignature("isPusher(address)", msg.sender));
            require(ok);
            bool allowed = abi.decode(res, (bool));
            require(allowed);

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
