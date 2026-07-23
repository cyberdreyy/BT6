### Title
Pusher revocation is bypassable within the deadline window via signature replay — (`File: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` signs consent over `(chainid, oracle, deadline, pusher, creator)` with no nonce. After a pusher calls `revokePusher()`, the creator can immediately replay the identical signed message — as long as the deadline has not expired — to re-establish the delegation. The pusher has no on-chain mechanism to permanently stop the redirect until the deadline expires. Any pool whose feedId is anchored to the pusher's own namespace will receive stale prices for the entire remaining deadline window.

---

### Finding Description

`allowPushers` constructs the signed digest as:

```solidity
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
``` [1](#0-0) 

There is no nonce, no per-pusher revocation counter, and no "revoked" flag. The function unconditionally overwrites `namespaceRemapping[pusher]` on every valid call:

```solidity
namespaceRemapping[pusher] = msg.sender;
``` [2](#0-1) 

`revokePusher()` clears the mapping to `address(0)`:

```solidity
namespaceRemapping[msg.sender] = address(0);
``` [3](#0-2) 

But because the same `(deadline, pusher, creator)` tuple produces the same digest, the creator can call `allowPushers` again with the identical `(deadline, signature)` pair in the very next block, restoring `namespaceRemapping[pusher] = creator`. The inline comment acknowledges the deadline is the only replay barrier:

> *"an undated signature could re-establish a delegation AFTER the pusher revoked it"* [4](#0-3) 

The deadline limits the window but does not close it. Within that window, `revokePusher` is effectively a no-op: the creator can undo it in the same block at zero cost.

The `_ensureDeadline` check only gates on `block.timestamp <= deadline`: [5](#0-4) 

No state is consumed or invalidated when the signature is first used.

---

### Impact Explanation

The `CompressedOracle` is registrationless: a feed's identity **is** its location — `feedIdOf(creator, slotIndex, positionIndex)` packs the creator address directly:

```solidity
return bytes32(
    uint256(uint160(creator)) << 96 | block.chainid << 16 | uint256(slotIndex) << 8 | positionIndex
);
``` [6](#0-5) 

When a pusher is delegated, every fallback push lands in the **creator's** namespace, not the pusher's own:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [7](#0-6) 

If a pool's `AnchoredPriceProvider` is bound to `feedIdOf(pusher, slot, pos)` — the pusher's own namespace — and the pusher is force-delegated to the creator's namespace, the pusher's price updates never reach that feedId. The pool reads a timestamp of zero (never-pushed sentinel), which every consumer rejects as stale. Swaps against that pool execute at a stale or zero price, satisfying the **bad-price execution** impact criterion.

---

### Likelihood Explanation

- The pusher must have signed a consent message with a non-trivial deadline (common for operational convenience — e.g., 7–30 days).
- The creator needs only to watch the mempool for `revokePusher` and front-run or immediately follow with `allowPushers` using the stored signature. This is a single cheap transaction.
- No privileged role is required; the creator is a valid semi-trusted participant in the delegation model.
- The pusher's only recourse is to stop pushing entirely, which itself causes the stale-price outcome for any pool relying on their feed.

---

### Recommendation

Add a per-pusher revocation nonce to the signed digest:

```solidity
mapping(address => uint256) public pusherNonce;

// In allowPushers:
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(
        block.chainid, address(this), deadline,
        pusher, msg.sender,
        pusherNonce[pusher]   // <-- consume on first use or on revoke
    ))
);
```

Increment `pusherNonce[pusher]` inside `revokePusher()` so that any previously signed consent is immediately invalidated, regardless of its deadline.

---

### Proof of Concept

```solidity
// 1. Pusher signs consent with deadline = now + 30 days
bytes memory sig = sign(PUSHER_KEY,
    keccak256(abi.encode(chainid, oracle, deadline, pusher, creator)));

// 2. Creator establishes delegation
oracle.allowPushers(deadline, [pusher], [sig]);
// namespaceRemapping[pusher] == creator ✓

// 3. Pusher self-revokes
vm.prank(pusher);
oracle.revokePusher();
// namespaceRemapping[pusher] == address(0) ✓

// 4. Creator replays the SAME signature — no nonce, deadline still valid
vm.prank(creator);
oracle.allowPushers(deadline, [pusher], [sig]);
// namespaceRemapping[pusher] == creator again ✓

// 5. Pusher's subsequent fallback pushes land in creator's namespace.
//    feedIdOf(pusher, slot, pos) receives no update → timestampMs == 0.
//    Any pool bound to feedIdOf(pusher, slot, pos) reads stale/zero price.
IOffchainOracle.OracleData memory d = oracle.getOracleData(
    oracle.feedIdOf(pusher, slot, pos));
assertEq(d.timestampMs, TimeMs.wrap(0)); // stale sentinel
```

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L49-53)
```text
    function feedIdOf(address creator, uint8 slotIndex, uint8 positionIndex) public view returns (bytes32) {
        return bytes32(
            uint256(uint160(creator)) << 96 | block.chainid << 16 | uint256(slotIndex) << 8 | positionIndex
        );
    }
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L204-207)
```text
            bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
                keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
            );
            require(pusher == ECDSA.recover(hash, signatures[i]));
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L209-209)
```text
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

**File:** smart-contracts-poc/contracts/oracles/compressed/OracleBase.sol (L124-126)
```text
    function _ensureDeadline(uint256 deadline) internal view virtual {
        require(block.timestamp <= deadline, DeadlineExceeded());
    }
```
