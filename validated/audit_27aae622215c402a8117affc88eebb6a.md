### Title
`revokePusher()` Self-Revocation Is Ineffective Within Deadline Window Due to Signature Replay in `allowPushers()` — (File: `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracleV1.allowPushers()` verifies a pusher's EIP-191 consent signature but tracks **no nonce and no used-signature set**. After a pusher calls `revokePusher()` to clear their delegation, the creator can immediately replay the original signature (same `deadline`, same `pusher`, same `msg.sender`) to re-establish `namespaceRemapping[pusher] = creator`. The pusher's self-revocation is silently overwritten within the deadline window, exactly mirroring how `FeePoolV0.distributeMochi()` flushed `treasuryShare` and allowed the cycle to repeat.

---

### Finding Description

`allowPushers()` constructs the signed digest as:

```solidity
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
``` [1](#0-0) 

The only freshness gate is `_ensureDeadline(deadline)`, which checks `block.timestamp <= deadline`. [2](#0-1) 

`revokePusher()` clears the mapping:

```solidity
namespaceRemapping[msg.sender] = address(0);
``` [3](#0-2) 

Because no nonce is consumed and no signature hash is recorded as used, the creator can call `allowPushers(deadline, [pusher], [originalSig])` again in the very next block with the **identical** signature. The digest is byte-for-byte identical (`chainid`, `address(this)`, `deadline`, `pusher`, `msg.sender` are all unchanged), so `ECDSA.recover` succeeds and `namespaceRemapping[pusher]` is written back to `creator`.

The code's own comment acknowledges the risk but mischaracterises the mitigation:

> "the deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it." [4](#0-3) 

The deadline limits the *window* but does **not** prevent re-establishment within that window. The pusher's revocation is therefore a no-op for the entire deadline duration.

---

### Impact Explanation

After the creator replays the signature, the pusher's `fallback()` pushes resolve the namespace as:

```solidity
address creator = namespaceRemapping[msg.sender]; // restored to creator
if (creator == address(0)) creator = msg.sender;
``` [5](#0-4) 

All subsequent pushes land in the **creator's** namespace, not the pusher's own. Any pool whose `PriceProvider` is bound to `feedIdOf(pusher, slotIndex, positionIndex)` will read `price = 0 / timestampMs = 0` — the never-pushed default — which every consumer rejects as stale. This produces a bad-price / stale-price condition on every swap that pool attempts, making the pool's swap path unusable for as long as the creator keeps replaying the delegation.

---

### Likelihood Explanation

- The trigger is **unprivileged**: the creator is a normal namespace owner with no special on-chain role; calling `allowPushers` requires only the original signature, which the creator already holds.
- The pusher signed consent with a deadline (commonly hours to days). Within that window the creator can replay at zero cost, in any block, as many times as needed.
- The pusher has no on-chain recourse: `revokePusher()` is immediately undone, and the pusher cannot force the deadline to expire early.

---

### Recommendation

Track consumed signatures with a `mapping(bytes32 => bool) usedDelegations` keyed on the full digest, and revert if the digest has already been used:

```solidity
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
require(!usedDelegations[hash], SignatureAlreadyUsed());
require(pusher == ECDSA.recover(hash, signatures[i]));
usedDelegations[hash] = true;
namespaceRemapping[pusher] = msg.sender;
```

This ensures each pusher consent signature can establish the delegation exactly once, so `revokePusher()` permanently terminates that delegation regardless of the remaining deadline.

---

### Proof of Concept

1. Pusher signs consent: `sig = sign(keccak256(abi.encode(chainid, oracle, deadline=T+1day, pusher, creator)))`.
2. Creator calls `allowPushers(T+1day, [pusher], [sig])` → `namespaceRemapping[pusher] = creator`. Pusher's fallback pushes now land in creator's namespace.
3. Pusher calls `revokePusher()` → `namespaceRemapping[pusher] = address(0)`. Pusher expects their next push to land in their own namespace (`feedIdOf(pusher, slot, pos)`).
4. Creator immediately calls `allowPushers(T+1day, [pusher], [sig])` again with the **same** `sig` — deadline still valid, digest identical → `namespaceRemapping[pusher] = creator` again.
5. Pusher's next `fallback()` push resolves `creator = namespaceRemapping[pusher]` → push lands in creator's namespace, not pusher's own.
6. `feedIdOf(pusher, slot, pos)` remains at `price=0 / timestampMs=0`. Any pool whose provider reads this feed sees a stale price and cannot execute swaps.
7. Steps 3–6 repeat for the entire deadline window; the pusher's revocation is permanently ineffective until `block.timestamp > deadline`.

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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L238-243)
```text
    function revokePusher() external {
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
        namespaceRemapping[msg.sender] = address(0);
        emit PusherRevoked(msg.sender, creator);
    }
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
