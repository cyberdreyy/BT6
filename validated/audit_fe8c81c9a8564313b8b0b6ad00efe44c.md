### Title
Pusher Delegation Signature Has No Nonce — Creator Can Replay Revoked Consent Within Deadline Window to Silently Re-Establish Delegation and Stale Pusher-Namespace Feeds - (File: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol)

---

### Summary

`CompressedOracleV1.allowPushers` verifies pusher consent via an EIP-191 signature but includes **no nonce and no one-time-use guard**. After a pusher calls `revokePusher()`, the creator can immediately replay the original signed message — unchanged — to re-establish the delegation, as long as the deadline has not yet expired. The pusher's subsequent pushes are silently redirected back into the creator's namespace, leaving the pusher's own feeds permanently stale for any pool that reads them.

---

### Finding Description

`allowPushers` constructs the signed digest as:

```solidity
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
``` [1](#0-0) 

The message binds chain ID, oracle address, deadline, pusher, and creator. It does **not** bind any per-use nonce, a revocation counter, or a "used-hash" flag. After the signature is consumed once, the contract stores no record that it was ever presented.

`revokePusher` zeroes the mapping entry:

```solidity
namespaceRemapping[msg.sender] = address(0);
``` [2](#0-1) 

But `allowPushers` has no guard against re-presenting the same bytes. The creator calls it again with the identical `(deadline, [pusher], [signature])` tuple, the `_ensureDeadline` check passes (deadline is still in the future), ECDSA recovery succeeds (the message is identical), and the mapping is written back:

```solidity
namespaceRemapping[pusher] = msg.sender;
``` [3](#0-2) 

The code comment acknowledges the deadline is the only replay guard:

> *"the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it. The deadline is likewise required"* [4](#0-3) 

The deadline limits the **window** of replay but does not prevent replay within that window. A creator who requested a signature with a far-future deadline (e.g., 30 days, which is operationally normal) can replay it an unlimited number of times before it expires.

The `fallback` push path resolves the namespace from `namespaceRemapping[msg.sender]`:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [5](#0-4) 

Once the delegation is silently re-established, every push from the pusher's key is written into the creator's storage slots, not the pusher's own slots. The pusher's own `feedIdOf(pusher, slotIndex, positionIndex)` slots receive no further updates.

---

### Impact Explanation

Any pool or `AnchoredPriceProvider` configured to read `feedIdOf(pusher, slotIndex, positionIndex)` will observe a frozen timestamp and frozen price from the moment the creator replays the delegation. The `maxTimeDrift` guard in `OracleBase` / downstream providers will eventually reject the stale data as expired, causing the price read to revert or return zero — both of which block swaps or force the pool to operate on a bad (zero or last-seen) bid/ask. This matches the allowed impact: **stale bid/ask quote reaches a pool swap** and **broken core pool functionality causing loss of funds or unusable swap flows**.

---

### Likelihood Explanation

- The creator controls the deadline they ask the pusher to sign. Deadlines of hours to days are operationally normal for off-chain key-management workflows.
- The pusher (especially an automated bot) has no on-chain mechanism to detect that the delegation was silently re-established after revocation; the only signal is the `PusherAuthorized` event, which bots may not monitor.
- The creator is not a privileged/trusted role — it is any address that owns a namespace. A semi-malicious creator can execute this with zero additional permissions.
- The attack is free (only gas cost) and repeatable every time the pusher calls `revokePusher()`.

---

### Recommendation

Add a per-pusher nonce to the signed digest and increment it on every successful `allowPushers` call (or on every `revokePusher` call). This makes every previously issued signature immediately invalid after revocation:

```solidity
mapping(address => uint256) public pusherNonce;

// In allowPushers:
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(
        block.chainid, address(this), deadline,
        pusher, msg.sender, pusherNonce[pusher]   // ← add nonce
    ))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
pusherNonce[pusher]++;   // invalidate on use
```

Alternatively, record the hash of each consumed signature in a `mapping(bytes32 => bool) usedSignatures` and revert on reuse — analogous to the EIP-712 pattern recommended in the external report.

---

### Proof of Concept

```
1. Creator C asks Pusher P to sign a delegation with deadline = block.timestamp + 7 days.
   P signs: keccak256(abi.encode(chainid, oracle, deadline, P, C))  → sig

2. C calls allowPushers(deadline, [P], [sig])
   → namespaceRemapping[P] = C
   → P's pushes go to C's namespace (feedIdOf(C, slot, pos))

3. P calls revokePusher()
   → namespaceRemapping[P] = address(0)
   → P's pushes now go to P's own namespace (feedIdOf(P, slot, pos))
   → Pools reading feedIdOf(P, slot, pos) start receiving fresh prices.

4. C calls allowPushers(deadline, [P], [sig])  ← SAME sig, deadline still valid
   → ECDSA.recover succeeds (message unchanged)
   → namespaceRemapping[P] = C  (re-established silently)

5. P continues pushing (bot unaware of re-delegation).
   → All pushes land in C's namespace.
   → feedIdOf(P, slot, pos) timestamp freezes at the value from step 3.

6. Any pool reading feedIdOf(P, slot, pos) now sees a stale price.
   → maxTimeDrift guard eventually rejects it → swap reverts or pool
      falls back to zero price → bad-price execution / unusable swap flow.

Steps 3–5 can be repeated by C indefinitely until the deadline expires.
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L204-207)
```text
            bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
                keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
            );
            require(pusher == ECDSA.recover(hash, signatures[i]));
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L209-210)
```text
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
