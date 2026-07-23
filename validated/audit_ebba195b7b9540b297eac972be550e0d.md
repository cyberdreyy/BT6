### Title
Pusher Delegation Signature Replay Nullifies `revokePusher()` — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

### Summary

`allowPushers` signs over `(block.chainid, address(this), deadline, pusher, creator)` with no nonce. A creator who retains the original signature bytes can call `allowPushers` again with the identical arguments after the pusher has called `revokePusher()`, silently re-establishing the delegation and redirecting the pusher's future price writes into the creator's namespace without the pusher's knowledge.

### Finding Description

`allowPushers` constructs its EIP-191 hash as:

```solidity
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
``` [1](#0-0) 

There is no per-pusher nonce, no consumed-flag, and no state change that invalidates the signature after first use. The code comment acknowledges that the deadline is the only guard against post-revocation replay:

> *"The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."* [2](#0-1) 

`revokePusher()` only zeroes `namespaceRemapping[msg.sender]`:

```solidity
namespaceRemapping[msg.sender] = address(0);
``` [3](#0-2) 

It does not burn or invalidate the original signature. Because the signed tuple `(chainid, oracle, deadline, pusher, creator)` is identical before and after revocation, the creator can call `allowPushers` a second time with the same `deadline` and `signatures` arguments and the `require(pusher == ECDSA.recover(...))` check passes again, writing `namespaceRemapping[pusher] = creator` once more.

The `fallback()` push path reads `namespaceRemapping[msg.sender]` at call time:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [4](#0-3) 

So after the replay, every subsequent push from the pusher's EOA lands in the creator's namespace, not the pusher's own namespace, without any on-chain signal to the pusher.

### Impact Explanation

A pusher who revokes to stop contributing to a creator's namespace (e.g., because the creator's pool is misconfigured, or the pusher is now pushing a different asset pair into their own namespace) has their revocation silently undone. Their price writes continue to land in the creator's namespace. If the creator's pool is consuming those feeds, it receives prices that the pusher did not intend for that context — a bad-price execution path reaching live swaps. The pusher's own namespace simultaneously receives no updates, so any pool relying on the pusher's own feeds also breaks.

### Likelihood Explanation

The creator retains the original `signatures` bytes from the first `allowPushers` call (they submitted the transaction, so they have the calldata). The replay requires only that the deadline has not yet expired. A deadline of `block.timestamp + N days` is a common pattern; the window can be hours to days. The creator is a regular (non-admin) user, not a privileged role, so this is an unprivileged trigger.

### Recommendation

Add a per-pusher revocation nonce to the oracle state and include it in the signed digest:

```solidity
mapping(address => uint256) public pusherNonce;

// in allowPushers:
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender, pusherNonce[pusher]))

// in revokePusher / removePushers:
pusherNonce[pusher]++;
```

Incrementing the nonce on every revocation makes all previously issued signatures for that pusher immediately invalid, regardless of their deadline.

### Proof of Concept

```solidity
// 1. Pusher signs consent for creator with deadline = now + 1 day
uint256 deadline = block.timestamp + 1 days;
bytes memory sig = pusher.sign(keccak256(abi.encode(chainid, oracle, deadline, pusher, creator)));

// 2. Creator establishes delegation
oracle.allowPushers(deadline, [pusher], [sig]);
// namespaceRemapping[pusher] == creator ✓

// 3. Pusher revokes
vm.prank(pusher);
oracle.revokePusher();
// namespaceRemapping[pusher] == address(0) ✓

// 4. Creator replays the IDENTICAL call — same deadline, same sig bytes
vm.prank(creator);
oracle.allowPushers(deadline, [pusher], [sig]);
// namespaceRemapping[pusher] == creator again — revocation nullified

// 5. Pusher pushes (thinking they're writing to their own namespace)
vm.prank(pusher);
oracle.call(slotWord);
// Price lands in CREATOR namespace, not pusher's own namespace
// Creator's pool reads the unintended price → bad-price execution
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
