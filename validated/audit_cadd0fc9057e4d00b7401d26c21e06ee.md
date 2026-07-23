### Title
`revokePusher()` protection nullified by signature replay within the deadline window — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` contains no used-signature tracking or nonce. After a pusher calls `revokePusher()` to self-revoke, the creator can immediately replay the original EIP-191 consent signature (same `deadline`, same `pusher`, same `msg.sender`) to re-establish the delegation. The code's own NatSpec comment claims the deadline prevents this, but the deadline only blocks replay *after* it expires — not during the window `[revocation_time, deadline)`.

---

### Finding Description

`allowPushers` verifies the pusher's EIP-191 signature and writes `namespaceRemapping[pusher] = msg.sender`: [1](#0-0) 

The NatSpec comment explicitly states the deadline is the guard against post-revocation replay:

> *"an undated signature could re-establish a delegation AFTER the pusher revoked it"*

But the only check is `_ensureDeadline`, which passes as long as `block.timestamp <= deadline`: [2](#0-1) 

`revokePusher()` clears the mapping: [3](#0-2) 

There is no nonce, no per-pusher revocation flag, and no used-signature bitmap. The signed message `keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))` is identical on every replay call: [4](#0-3) 

So the creator can call `allowPushers(deadline, [pusher], [originalSig])` again in the same block as the pusher's `revokePusher()`, restoring `namespaceRemapping[pusher] = creator`. The fallback push path then routes the pusher's writes back into the creator's namespace: [5](#0-4) 

This can be repeated indefinitely until the deadline expires. The protection window the pusher believes they have is zero.

---

### Impact Explanation

A pusher who revokes to stop their price data from landing in a creator's namespace cannot actually do so during the `[revocation_time, deadline)` window. A malicious creator who holds a still-valid consent signature can keep the pusher's namespace hijacked, ensuring every subsequent fallback push continues to update the creator's feed slots. Any pool consuming that feed via `price(feedId, pool)` will receive prices the pusher did not intend to attribute to the creator, enabling the creator to sustain a live feed they should no longer control. The broken invariant is: *after `revokePusher()` succeeds, the pusher's pushes must land in their own namespace* — this invariant is violated for the full remaining deadline window.

---

### Likelihood Explanation

Medium. The creator must have obtained a valid consent signature with a future deadline (the normal operational flow). The replay requires only a single public transaction with no additional privileges. Any creator who delegated a pusher with a long-lived deadline (e.g., 1 year, as is common in off-chain key-management setups) can exploit this window.

---

### Recommendation

Track consumed consent signatures. Add a `mapping(bytes32 => bool) private _usedConsentHash` and mark the hash as used inside `allowPushers`. On `revokePusher()`, additionally mark the current delegation's hash as consumed so the creator cannot replay it:

```solidity
// in allowPushers, after signature recovery:
bytes32 consentHash = keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender));
require(!_usedConsentHash[consentHash], "consent already consumed or revoked");
_usedConsentHash[consentHash] = true;
namespaceRemapping[pusher] = msg.sender;
```

Alternatively, store the `deadline` that was used to establish the current delegation per pusher and reject any `allowPushers` call that reuses the same `(pusher, creator, deadline)` triple after a revocation.

---

### Proof of Concept

```solidity
// 1. Creator delegates pusher with a 1-year deadline
uint256 deadline = block.timestamp + 365 days;
bytes memory sig = pusherSign(deadline, pusher, creator); // pusher's EIP-191 consent
vm.prank(creator);
oracle.allowPushers(deadline, [pusher], [sig]);
// namespaceRemapping[pusher] == creator ✓

// 2. Pusher discovers creator is malicious and self-revokes
vm.prank(pusher);
oracle.revokePusher();
// namespaceRemapping[pusher] == address(0) ✓

// 3. Creator immediately replays the SAME signature — no new consent needed
vm.prank(creator);
oracle.allowPushers(deadline, [pusher], [sig]); // succeeds: deadline still valid, no nonce
// namespaceRemapping[pusher] == creator again ✓

// 4. Pusher's next fallback push lands in creator's namespace, not their own
vm.prank(pusher);
(bool ok,) = address(oracle).call(encodedSlotWord);
// oracle.getOracleData(feedIdOf(creator, slot, pos)).price == pusher's price ✓
// oracle.getOracleData(feedIdOf(pusher,  slot, pos)).price == 0              ✓
```

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L186-212)
```text
    /// @notice Delegates pusher wallets into the caller's namespace. The pusher's EIP-191
    ///         signature is REQUIRED — without it anyone could remap a foreign pusher
    ///         wallet into their own namespace and silently swallow its pushes. The
    ///         deadline is likewise required: the signed consent carries no timestamp of
    ///         its own, so an undated signature could re-establish a delegation AFTER the
    ///         pusher revoked it.
    function allowPushers(uint256 deadline, address[] calldata pushers, bytes[] memory signatures) external {
        _ensureDeadline(deadline);

        uint256 l = pushers.length;
        require(l == signatures.length);
        for (uint256 i; i < l; i++) {
            address pusher = pushers[i];

            if (pusher == msg.sender) {
                revert NoSelfRemapping();
            }

            bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
                keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
            );
            require(pusher == ECDSA.recover(hash, signatures[i]));

            namespaceRemapping[pusher] = msg.sender;
            emit PusherAuthorized(pusher, msg.sender);
        }
    }
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
