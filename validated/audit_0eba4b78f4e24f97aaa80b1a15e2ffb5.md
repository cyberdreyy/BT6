### Title
`allowPushers` consent signature carries no nonce, allowing creator to replay a revoked delegation and permanently redirect pusher price writes into creator namespace - (File: `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

The EIP-191 consent signature verified in `allowPushers` commits to `(chainid, oracle, deadline, pusher, creator)` but contains **no per-delegation nonce**. After a pusher calls `revokePusher()` to clear `namespaceRemapping[pusher]`, the creator can immediately call `allowPushers` again with the identical signature to re-establish the mapping. This cycle repeats until the deadline expires, making the pusher's self-revocation permanently ineffective within the signed window and silently redirecting every subsequent fallback push into the creator's namespace.

---

### Finding Description

`allowPushers` verifies the pusher's consent over:

```solidity
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
namespaceRemapping[pusher] = msg.sender;
``` [1](#0-0) 

The signed payload contains `chainid`, `oracle address`, `deadline`, `pusher`, and `creator` — but **no nonce**. The same `(deadline, pusher, creator)` tuple produces the same hash every time. There is no on-chain record that the signature was already consumed.

The code comment itself acknowledges the concern:

> *"The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."* [2](#0-1) 

The comment treats the deadline as the fix. But the deadline only bounds the outer time window — it does **not** prevent the creator from calling `allowPushers` a second (or hundredth) time with the same bytes within that window.

`revokePusher` clears the mapping unconditionally:

```solidity
namespaceRemapping[msg.sender] = address(0);
``` [3](#0-2) 

But because `allowPushers` has no nonce check, the creator can immediately re-set it. The pusher has no on-chain mechanism to permanently invalidate the old signature short of waiting for the deadline to expire.

The `fallback` push path resolves the namespace at call time:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [4](#0-3) 

So every push the pusher makes after the creator replays the signature lands in the **creator's** namespace, not the pusher's own, feeding the creator's pool with the pusher's price data without the pusher's knowledge.

---

### Impact Explanation

The broken invariant is stated explicitly in the project's own audit-target notes:

> *"Delegation cleanup must fully remove the authority that later fallback or signed updates would otherwise reuse. High if stale update authority can continue writing production feed data."*

A malicious creator who holds a pusher's old consent signature can:

1. Re-establish `namespaceRemapping[pusher] = creator` at any time before the deadline.
2. Force every subsequent fallback push from that pusher to overwrite slots in the **creator's** namespace.
3. Any pool whose `PriceProvider` reads `feedIdOf(creator, slotIndex, positionIndex)` will consume those redirected price writes.

The pusher believes they have revoked consent; the pool operator and LPs believe the feed is controlled by the creator alone. Neither party can detect the silent redirection on-chain. The pusher's only mitigation is to stop pushing entirely — but that is an off-chain action that may not happen before the next price update is consumed by a live swap.

---

### Likelihood Explanation

- The creator must have retained the pusher's original consent bytes — trivially true for any creator who called `allowPushers` at least once.
- The deadline must not have expired. Deadlines are chosen by the creator at delegation time; nothing prevents a creator from requesting a signature with a deadline months in the future.
- No special privileges, no gas-intensive setup, and no mempool race are required. The creator simply calls `allowPushers` again with the same calldata.

---

### Recommendation

Add a per-pusher nonce to the signed message and track consumed nonces on-chain:

```solidity
mapping(address => uint256) public pusherNonces;

// In allowPushers:
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(
        block.chainid, address(this), deadline,
        pusher, msg.sender,
        pusherNonces[pusher]   // <-- add nonce
    ))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
pusherNonces[pusher]++;        // <-- consume nonce
namespaceRemapping[pusher] = msg.sender;
```

This ensures each consent signature is single-use. After `revokePusher()` increments (or the creator calls `removePushers`), the old signature is permanently invalid regardless of the deadline.

---

### Proof of Concept

```
// Setup
deadline = block.timestamp + 365 days   // creator chose a far-future deadline
sig = pusher.sign(keccak256(abi.encode(chainid, oracle, deadline, pusher, creator)))

// Step 1 — creator establishes delegation
oracle.allowPushers(deadline, [pusher], [sig])
// namespaceRemapping[pusher] == creator  ✓

// Step 2 — pusher revokes
oracle.revokePusher()   // called by pusher
// namespaceRemapping[pusher] == address(0)  ✓

// Step 3 — creator replays the SAME sig (no nonce check)
oracle.allowPushers(deadline, [pusher], [sig])
// namespaceRemapping[pusher] == creator  ← revocation bypassed

// Step 4 — pusher's next price push lands in creator namespace
oracle.call(pusher, slotWord)
// getOracleData(feedIdOf(creator, slotId, pos)).price == pusher's price
// getOracleData(feedIdOf(pusher,   slotId, pos)).price == 0
// Creator's pool reads feedIdOf(creator,...) → consumes pusher's price
```

The creator can repeat steps 2→3 indefinitely until `deadline` passes, making `revokePusher` a no-op for the entire signed window.

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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L204-209)
```text
            bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
                keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
            );
            require(pusher == ECDSA.recover(hash, signatures[i]));

            namespaceRemapping[pusher] = msg.sender;
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L241-242)
```text
        namespaceRemapping[msg.sender] = address(0);
        emit PusherRevoked(msg.sender, creator);
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L315-316)
```text
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;
```
