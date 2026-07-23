### Title
`allowPushers` Consent Signature Lacks a Nonce, Allowing Creator to Nullify a Pusher's `revokePusher()` and Permanently Redirect Pushes into Creator's Namespace — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracleV1.allowPushers` verifies a pusher's EIP-191 consent signature but includes no nonce or revocation-state commitment in the signed digest. The code comment explicitly states the deadline is the mechanism that prevents re-establishing a delegation after the pusher revokes it, but a deadline only blocks an *expired* signature — a still-valid signature can be replayed by the creator an unlimited number of times within the deadline window, including immediately after `revokePusher()` clears the mapping. The pusher's only on-chain revocation primitive is therefore ineffective against a creator who retains the original signature.

---

### Finding Description

`allowPushers` constructs the signed digest as:

```solidity
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
``` [1](#0-0) 

The tuple `(chainid, oracle, deadline, pusher, creator)` is fully static for the lifetime of the deadline. There is no nonce, no revocation counter, and no commitment to the current value of `namespaceRemapping[pusher]`. After the pusher calls `revokePusher()`:

```solidity
function revokePusher() external {
    address creator = namespaceRemapping[msg.sender];
    if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
    namespaceRemapping[msg.sender] = address(0);
    emit PusherRevoked(msg.sender, creator);
}
``` [2](#0-1) 

…the creator can immediately call `allowPushers` again with the identical signature bytes and the same `deadline`, passing the `ECDSA.recover` check and writing `namespaceRemapping[pusher] = msg.sender` again:

```solidity
namespaceRemapping[pusher] = msg.sender;
emit PusherAuthorized(pusher, msg.sender);
``` [3](#0-2) 

The code comment directly above `allowPushers` acknowledges this exact risk and claims the deadline mitigates it:

> *"The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."* [4](#0-3) 

The mitigation is incomplete. The deadline only prevents using a signature whose expiry has passed; it does not prevent replaying a still-valid signature after revocation. The invariant the comment asserts — that revocation is effective — is broken for the entire window `[revoke_time, deadline)`.

The `fallback` push path resolves the namespace at call time:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [5](#0-4) 

So every push the pusher makes after the creator re-delegates them lands in the creator's namespace, not the pusher's own namespace. The pusher's own feeds remain at their pre-revocation values (stale or zero), while the creator's feeds continue to be updated.

---

### Impact Explanation

The `CompressedOracleV1` feeds are consumed by `AnchoredPriceProvider` via the `price(feedId, pool)` path, which ultimately drives `getBidAndAskPrice()` during pool swaps. If a pusher's key is compromised and the pusher revokes to stop bad-price writes, a creator who retains the original consent signature can immediately re-delegate the compromised pusher. The attacker controlling the pusher key then continues writing manipulated slot values into the creator's namespace. Those values propagate through `getOracleData` → `_price` → `AnchoredPriceProvider._readLeg` → `_computeBidAsk` into live pool swaps, producing bad-price execution and direct loss of user principal.

Even without key compromise, a creator can use this to lock a pusher into their namespace against the pusher's will for the full deadline window (potentially months), preventing the pusher from redirecting their data to their own namespace or another creator.

---

### Likelihood Explanation

The trigger is a creator calling `allowPushers` with a previously-used, still-valid signature after the pusher has revoked. The creator already holds the signature (they used it for the original delegation), the call is permissionless, and no additional setup is required. The only precondition is that the original deadline has not yet expired. Deadlines are chosen by the creator at delegation time; a creator who anticipates needing long-lived delegation will naturally choose a far-future deadline, maximising the replay window.

---

### Recommendation

Include a per-pusher revocation counter (nonce) in the signed digest and increment it on every successful `revokePusher` or `removePushers` call:

```solidity
mapping(address => uint256) public pusherNonce;

// In revokePusher / removePushers:
pusherNonce[pusher]++;

// In allowPushers digest:
keccak256(abi.encode(
    block.chainid, address(this), deadline,
    pusher, msg.sender, pusherNonce[pusher]
))
```

After revocation the nonce increments, invalidating all previously issued signatures for that pusher regardless of their deadline.

---

### Proof of Concept

```solidity
// 1. Pusher signs consent for creator with deadline = block.timestamp + 365 days
bytes memory sig = pusher.sign(
    keccak256(abi.encode(chainid, oracle, deadline, pusher, creator))
);

// 2. Creator delegates pusher
oracle.allowPushers(deadline, [pusher], [sig]);
// namespaceRemapping[pusher] == creator ✓

// 3. Pusher revokes
vm.prank(pusher);
oracle.revokePusher();
// namespaceRemapping[pusher] == address(0) ✓

// 4. Creator immediately replays the SAME signature
vm.prank(creator);
oracle.allowPushers(deadline, [pusher], [sig]);
// namespaceRemapping[pusher] == creator again — revocation nullified

// 5. Attacker (holding pusher key) pushes manipulated price into creator's namespace
vm.prank(pusher);
(bool ok,) = address(oracle).call(maliciousSlotWord);
// Price lands in creator's namespace, consumed by AnchoredPriceProvider on next swap
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
