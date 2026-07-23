### Title
Delegation Signature Replay Nullifies Pusher Revocation Within Deadline Window — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` signs over `(chainid, address(this), deadline, pusher, creator)` with no nonce. A creator who holds a pusher's previously-issued consent signature can replay it any number of times before the deadline expires. Because `revokePusher` only clears `namespaceRemapping[pusher]` without invalidating outstanding signatures, a creator can immediately re-establish delegation after the pusher revokes, making the pusher's revocation completely ineffective until the deadline passes.

---

### Finding Description

`allowPushers` constructs the signed digest as:

```solidity
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
namespaceRemapping[pusher] = msg.sender;
``` [1](#0-0) 

The signed payload contains no nonce, no per-use counter, and no on-chain consumed-signature registry. The only replay bound is the `deadline` field, which is checked by `_ensureDeadline`:

```solidity
function _ensureDeadline(uint256 deadline) internal view virtual {
    require(block.timestamp <= deadline, DeadlineExceeded());
}
``` [2](#0-1) 

`revokePusher` clears the mapping but does nothing to the signature:

```solidity
function revokePusher() external {
    address creator = namespaceRemapping[msg.sender];
    if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
    namespaceRemapping[msg.sender] = address(0);
    emit PusherRevoked(msg.sender, creator);
}
``` [3](#0-2) 

The code comment on `allowPushers` explicitly acknowledges the deadline as the sole guard against post-revocation replay:

> *"The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."* [4](#0-3) 

The comment treats the deadline as sufficient, but the deadline only caps the total replay window — it does **not** prevent the creator from replaying the same signature immediately after the pusher revokes, re-establishing delegation without any new consent.

---

### Impact Explanation

The `namespaceRemapping` determines where every fallback push lands:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [5](#0-4) 

If a pusher revokes because their key is compromised or their price data is known-bad, and the creator immediately replays the old signature, the pusher's subsequent writes continue landing in the creator's namespace. Pools that consume the creator's feeds via `price(feedId, pool)` will receive the bad/stale prices. The pusher's emergency revocation — the only self-protection mechanism available to them — is rendered completely ineffective until the deadline expires.

---

### Likelihood Explanation

The creator retains the pusher's signature from the original `allowPushers` call (it is calldata, permanently visible on-chain). Replaying it requires a single transaction with no special privileges. Any creator who wishes to prevent a pusher from revoking can do so trivially within the deadline window. The deadline is a configurable off-chain parameter and can be set to hours or days, making the replay window practically significant.

---

### Recommendation

Add a per-pusher consumed-signature registry (nonce or used-hash set) so that each consent signature can only establish delegation once:

```solidity
mapping(bytes32 => bool) private _usedDelegationHashes;

// inside allowPushers, after recovering the signer:
require(!_usedDelegationHashes[hash], "signature already used");
_usedDelegationHashes[hash] = true;
namespaceRemapping[pusher] = msg.sender;
```

Alternatively, include a per-pusher nonce in the signed payload and increment it on every successful delegation or revocation, so any outstanding signature for the old nonce is automatically invalidated.

---

### Proof of Concept

```
1. Pusher signs: consent = sign(chainid, oracle, deadline=T+1day, pusher, creator)
2. Creator calls allowPushers(T+1day, [pusher], [consent])
   → namespaceRemapping[pusher] = creator  ✓
3. Pusher discovers their key is compromised; calls revokePusher()
   → namespaceRemapping[pusher] = address(0)  ✓ (pusher believes they are safe)
4. Creator immediately calls allowPushers(T+1day, [pusher], [consent])
   with the IDENTICAL signature from step 1 — deadline still valid, hash unchanged
   → namespaceRemapping[pusher] = creator  ← delegation silently re-established
5. Attacker (holding pusher's compromised key) pushes bad prices via fallback()
   → writes land in creator's namespace, not pusher's own namespace
6. Pools reading creator's feedId receive the attacker-controlled bad price
   → bad-price execution on live swaps
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L204-209)
```text
            bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
                keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
            );
            require(pusher == ECDSA.recover(hash, signatures[i]));

            namespaceRemapping[pusher] = msg.sender;
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
