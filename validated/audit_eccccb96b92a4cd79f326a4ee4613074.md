### Title
`allowPushers` Signature Replay Allows Creator to Re-Establish a Revoked Pusher Delegation, Routing Pushes Away from the Pusher's Own Namespace and Causing Stale Oracle Prices in Pools — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

The `allowPushers` function accepts a pusher's EIP-191 signature that commits the pusher to a specific creator and deadline, but the signed message contains **no nonce**. The same signature is therefore replayable an unlimited number of times within the deadline window. After a pusher calls `revokePusher()` to clear their delegation, the creator can immediately replay the original signature to re-establish `namespaceRemapping[pusher] = creator`. The pusher's revocation is silently nullified for the entire remaining lifetime of the deadline, and every subsequent push the pusher makes is routed into the creator's namespace instead of the pusher's own namespace, leaving any pool that reads `feedIdOf(pusher, …)` with a permanently stale price.

---

### Finding Description

`allowPushers` builds its signed digest as:

```solidity
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
``` [1](#0-0) 

The five fields bind the signature to a chain, a contract, a deadline, a pusher, and a creator. There is no per-delegation nonce. Once the pusher has signed and the creator has called `allowPushers` once, the signature remains valid for every subsequent call to `allowPushers` until `block.timestamp > deadline`.

`revokePusher` clears the mapping:

```solidity
namespaceRemapping[msg.sender] = address(0);
``` [2](#0-1) 

But `allowPushers` performs no check for whether the pusher has previously revoked. It simply overwrites the mapping again:

```solidity
namespaceRemapping[pusher] = msg.sender;
``` [3](#0-2) 

The fallback push path reads `namespaceRemapping[msg.sender]` at the top of every call and routes the entire push into whichever namespace is currently stored:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [4](#0-3) 

Because the mapping is re-established by the replay, every push the pusher makes after their revocation still lands in the creator's namespace, not in `feedIdOf(pusher, …)`. Any pool or price provider that reads the pusher's own feed id receives a timestamp that stopped advancing the moment the original delegation was first established.

The code's own comment acknowledges the replay risk but treats the deadline as a sufficient mitigation:

> *"The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."* [5](#0-4) 

The deadline only bounds the total replay window; it does not prevent the creator from replaying the signature an arbitrary number of times within that window, including immediately after each revocation.

---

### Impact Explanation

A pool whose price provider reads `feedIdOf(pusher, slotIndex, positionIndex)` will observe a `timestampMs` that stopped advancing when the delegation was first established. Depending on the provider's `maxTimeDrift` configuration:

- **Stale-price DoS**: The `AnchoredPriceProvider` (or any provider with a drift guard) will reject the stale quote and revert every swap, making the pool unusable for the duration of the attack — matching the "unusable swap/liquidity flow" impact gate.
- **Bad-price execution**: If `maxTimeDrift` is generous or unset, the provider returns the last price the pusher published before the delegation, which may be arbitrarily far from the current market price, satisfying the "stale bid/ask quote reaches a pool swap" impact gate.

The creator can sustain the attack indefinitely by replaying the signature after every revocation attempt, for the full lifetime of the deadline.

---

### Likelihood Explanation

The attack requires:

1. The pusher to have previously signed a consent with a future deadline (a normal operational step when onboarding a pusher).
2. The creator to retain the signed bytes and call `allowPushers` again after each `revokePusher` call.

This is a semi-trusted trigger: the creator is a valid protocol participant who obtained a legitimate signature. No privileged admin role is needed. The parallel to the external report is exact — the party host (semi-trusted) resets the reentrancy guard variable; here the creator (semi-trusted) resets the namespace mapping variable.

---

### Recommendation

Add a per-pusher nonce to the signed digest and increment it on every successful delegation:

```solidity
mapping(address => uint256) public pusherNonce;

// In allowPushers:
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(
        block.chainid, address(this), deadline,
        pusher, msg.sender, pusherNonce[pusher]++
    ))
);
```

Alternatively, record each consumed signature hash in a `mapping(bytes32 => bool) usedSignatures` and revert on reuse. Either approach ensures that a revoked pusher's old signature cannot be replayed.

---

### Proof of Concept

```solidity
// Setup: pusher signs consent for creator with a 1-day deadline
uint256 deadline = block.timestamp + 1 days;
bytes32 digest = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(oracle), deadline, pusher, creator))
);
bytes memory sig = sign(PUSHER_KEY, digest);

address[] memory pushers = new address[](1);
pushers[0] = pusher;
bytes[] memory sigs = new bytes[](1);
sigs[0] = sig;

// Step 1: creator establishes delegation
vm.prank(creator);
oracle.allowPushers(deadline, pushers, sigs);
assertEq(oracle.namespaceRemapping(pusher), creator);

// Step 2: pusher revokes
vm.prank(pusher);
oracle.revokePusher();
assertEq(oracle.namespaceRemapping(pusher), address(0));

// Step 3: creator REPLAYS the same signature — no nonce prevents it
vm.prank(creator);
oracle.allowPushers(deadline, pushers, sigs);
assertEq(oracle.namespaceRemapping(pusher), creator); // delegation silently re-established

// Step 4: pusher's subsequent pushes land in creator's namespace, not pusher's own
// feedIdOf(pusher, slot, pos) timestamp stops advancing → pool reads stale price
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
