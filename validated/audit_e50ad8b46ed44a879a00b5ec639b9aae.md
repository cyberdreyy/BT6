### Title
Pusher Consent Signature Replay in `allowPushers` Renders `revokePusher` Ineffective Within the Deadline Window — (`File: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

### Summary

`allowPushers` verifies a pusher's EIP-191 consent signature and sets `namespaceRemapping[pusher] = creator`. The only replay guard is a deadline check (`block.timestamp <= deadline`). There is no nonce and no used-signature mapping. After a pusher calls `revokePusher()` to clear their delegation, the creator can immediately call `allowPushers` again with the **identical signature bytes** — the deadline check still passes, the signature still verifies, and the delegation is silently re-established. The code comment explicitly states the deadline is the mechanism that prevents this, but the deadline only blocks signatures after they expire, not within the window.

### Finding Description

`allowPushers` constructs the signed digest as:

```solidity
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
namespaceRemapping[pusher] = msg.sender;
``` [1](#0-0) 

There is no `mapping(bytes32 => bool) usedSignatures` or per-pusher nonce. The only guard is `_ensureDeadline`:

```solidity
function _ensureDeadline(uint256 deadline) internal view virtual {
    require(block.timestamp <= deadline, DeadlineExceeded());
}
``` [2](#0-1) 

`revokePusher` clears the mapping:

```solidity
namespaceRemapping[msg.sender] = address(0);
``` [3](#0-2) 

Because the signature is not consumed or invalidated on first use, the creator holds a replayable credential for the entire lifetime of the deadline. The code comment acknowledges the deadline is the intended protection:

> "The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it." [4](#0-3) 

But the deadline only prevents use after expiry. Within the window the same bytes are accepted an unlimited number of times.

### Impact Explanation

The `fallback` push path resolves the namespace at call time:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [5](#0-4) 

After the creator replays the consent signature, every subsequent push from the pusher's automated bot lands in the creator's namespace again. The `price` function reads directly from that namespace:

```solidity
OracleData memory data = getOracleData(feedId);
return (uint256(data.price), uint256(data.spread0), data.spread1, data.timestampMs.toSeconds());
``` [6](#0-5) 

A pool whose price provider reads from the creator's `feedId` will consume whatever price the pusher's bot writes — even though the pusher believed they had revoked. If the creator is malicious, they can time the re-delegation to coincide with a swap, causing the pool to execute against a price the pusher did not intend to authorize for that namespace. This is a bad-price execution path and an admin-boundary break: the pusher's revocation mechanism is bypassed by an action the creator is permitted to take.

### Likelihood Explanation

- The consent signature is a standard off-chain bytes blob that the creator retains after the first `allowPushers` call.
- Re-calling `allowPushers` with the same arguments costs only gas.
- The window is as long as the deadline the pusher originally agreed to (commonly hours to days).
- No privileged role is required; the creator is the ordinary `msg.sender` of `allowPushers`.
- The pusher has no on-chain way to invalidate the signature before the deadline expires.

### Recommendation

Mark each consent signature as consumed on first use. The simplest fix is to hash the digest and record it:

```solidity
mapping(bytes32 => bool) private _usedConsentSignatures;

// inside allowPushers, after ECDSA.recover succeeds:
require(!_usedConsentSignatures[hash], "consent already used");
_usedConsentSignatures[hash] = true;
```

Alternatively, include a per-pusher nonce in the signed message and increment it on each successful delegation, so the pusher can invalidate any outstanding consent by incrementing their nonce on-chain.

### Proof of Concept

```solidity
// 1. Pusher signs consent with deadline = now + 1 day
bytes memory sig = _signConsent(PUSHER_KEY, deadline, pusher, creator);

// 2. Creator establishes delegation
vm.prank(creator);
oracle.allowPushers(deadline, _arr(pusher), _arr(sig));
assertEq(oracle.namespaceRemapping(pusher), creator); // delegated

// 3. Pusher revokes
vm.prank(pusher);
oracle.revokePusher();
assertEq(oracle.namespaceRemapping(pusher), address(0)); // cleared

// 4. Creator replays the SAME signature — deadline still valid
vm.prank(creator);
oracle.allowPushers(deadline, _arr(pusher), _arr(sig)); // succeeds, no revert
assertEq(oracle.namespaceRemapping(pusher), creator);   // delegation re-established

// 5. Pusher's bot pushes a price — lands in creator's namespace, not pusher's own
vm.prank(pusher);
(bool ok,) = address(oracle).call(_wordAt(slotId, pos, raw, tsMs));
assertTrue(ok);
// creator's pool reads this price via feedIdOf(creator, slotId, pos)
```

The `_signConsent` helper in the test suite confirms the exact digest format:

```solidity
keccak256(abi.encode(block.chainid, address(oracle), deadline, _pusher, _creator))
``` [7](#0-6) 

No test covers the replay-after-revoke scenario, confirming the gap is untested and the re-delegation succeeds silently.

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L176-178)
```text
        OracleData memory data = getOracleData(feedId);
        return (uint256(data.price), uint256(data.spread0), data.spread1, data.timestampMs.toSeconds());
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L204-210)
```text
            bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
                keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
            );
            require(pusher == ECDSA.recover(hash, signatures[i]));

            namespaceRemapping[pusher] = msg.sender;
            emit PusherAuthorized(pusher, msg.sender);
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

**File:** smart-contracts-poc/contracts/oracles/compressed/OracleBase.sol (L124-126)
```text
    function _ensureDeadline(uint256 deadline) internal view virtual {
        require(block.timestamp <= deadline, DeadlineExceeded());
    }
```

**File:** smart-contracts-poc/test/oracles/compressed/CompressedOracle.t.sol (L507-517)
```text
    function _signConsent(uint256 pk, uint256 deadline, address _pusher, address _creator)
        internal
        view
        returns (bytes memory)
    {
        bytes32 digest = MessageHashUtils.toEthSignedMessageHash(
            keccak256(abi.encode(block.chainid, address(oracle), deadline, _pusher, _creator))
        );
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(pk, digest);
        return abi.encodePacked(r, s, v);
    }
```
