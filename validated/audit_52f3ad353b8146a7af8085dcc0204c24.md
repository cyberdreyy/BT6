### Title
`allowPushers` delegation signature has no nonce, allowing creator to replay pusher consent and permanently override `revokePusher()` within the deadline window — (File: `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` verifies an EIP-191 signature that commits to `(chainid, oracle, deadline, pusher, creator)` but includes **no nonce**. Because the signed payload is stateless, the creator can replay the exact same signature bytes any number of times before the deadline. A pusher who calls `revokePusher()` to self-revoke — e.g., after discovering their key is compromised — can have that revocation immediately overwritten by the creator replaying the original consent signature. The pusher's key therefore remains authorised to write prices into the creator's namespace for the full lifetime of the deadline, and any attacker holding the pusher's private key can continue pushing arbitrary prices into oracle feeds consumed by pools.

---

### Finding Description

`allowPushers` constructs the signed digest as:

```solidity
// CompressedOracle.sol lines 204-207
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
namespaceRemapping[pusher] = msg.sender;
``` [1](#0-0) 

The tuple `(chainid, oracle, deadline, pusher, creator)` is fully deterministic for a given delegation event. No nonce, counter, or one-time-use flag is included. The contract never records that a particular `(pusher, creator, deadline, signature)` tuple has already been consumed.

`revokePusher` zeroes the mapping:

```solidity
// CompressedOracle.sol lines 238-243
function revokePusher() external {
    address creator = namespaceRemapping[msg.sender];
    if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
    namespaceRemapping[msg.sender] = address(0);
    emit PusherRevoked(msg.sender, creator);
}
``` [2](#0-1) 

Because `allowPushers` performs no replay check, the creator can immediately call it again with the identical signature bytes, restoring `namespaceRemapping[pusher] = creator`. The pusher's revocation is silently undone in the same block.

The code comment at line 189 acknowledges the replay concern but claims the deadline is sufficient mitigation:

> *"the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it"* [3](#0-2) 

The deadline only bounds the outer window; it does **not** prevent replay within that window. A pusher who signed with a 24-hour deadline cannot revoke for 24 hours regardless of how many times they call `revokePusher()`.

The `fallback` push path resolves the effective namespace from `namespaceRemapping`:

```solidity
// CompressedOracle.sol lines 315-316
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [4](#0-3) 

So while the delegation is active, every `fallback` call from the pusher's address writes into the creator's storage slots, not the pusher's own namespace.

---

### Impact Explanation

Oracle feeds written by `CompressedOracleV1` are consumed by `AnchoredPriceProvider` (and transitively by `MetricOmmPool.getBidAndAsk`). If an attacker holds a compromised pusher key and the pusher's `revokePusher()` call is continuously overridden by the creator replaying the original signature, the attacker retains the ability to write arbitrary `(price, spread0, spread1, timestampMs)` tuples into the creator's feed slots for the full deadline window. A monotonicity bypass is not needed — the attacker simply supplies a timestamp one millisecond ahead of the current stored value. The resulting stale, inverted, or unbounded bid/ask quote reaches pool swaps, enabling:

- Traders to receive more output than the oracle curve permits (swap conservation failure).
- Pool insolvency if LP reserves are drained at manipulated prices.

This matches the **bad-price execution** and **pool insolvency** impact categories in the allowed gate.

---

### Likelihood Explanation

The trigger requires two conditions:

1. A pusher's EOA key is compromised (realistic for hot-wallet pushers).
2. The creator replays the original signature — either innocently (thinking the revocation was accidental) or maliciously.

The creator is a semi-trusted party (not the factory owner or oracle admin), so replaying their own previously-issued delegation is an unprivileged on-chain action requiring no special role. The deadline window for production pushers is likely hours to days, giving ample time for exploitation after the pusher's revocation attempt.

---

### Recommendation

Track consumed signatures per pusher address using a nonce, mirroring the fix described in the external report:

```solidity
mapping(address => uint256) public pusherNonce;

// In allowPushers, include the nonce in the hash:
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(
        block.chainid, address(this), deadline,
        pusher, msg.sender,
        pusherNonce[pusher]   // <-- add nonce
    ))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
pusherNonce[pusher]++;        // <-- invalidate after use
namespaceRemapping[pusher] = msg.sender;
```

Alternatively, record the full signature hash in a `mapping(bytes32 => bool) usedSignatures` set and revert on reuse. Either approach ensures that once a pusher revokes and the creator attempts to replay, the signature is already spent and the call reverts.

---

### Proof of Concept

```solidity
// 1. Pusher signs consent for Creator with deadline = block.timestamp + 1 days
bytes32 hash = keccak256(abi.encode(
    block.chainid, address(oracle), deadline, pusher, creator
));
bytes memory sig = sign(PUSHER_KEY, hash); // EIP-191 signed

// 2. Creator establishes delegation
vm.prank(creator);
oracle.allowPushers(deadline, toArray(pusher), toArray(sig));
assertEq(oracle.namespaceRemapping(pusher), creator); // delegation active

// 3. Pusher discovers key compromise, self-revokes
vm.prank(pusher);
oracle.revokePusher();
assertEq(oracle.namespaceRemapping(pusher), address(0)); // revoked

// 4. Creator replays the IDENTICAL signature bytes
vm.prank(creator);
oracle.allowPushers(deadline, toArray(pusher), toArray(sig)); // no revert
assertEq(oracle.namespaceRemapping(pusher), creator); // delegation restored

// 5. Attacker (holding compromised pusher key) pushes bad price
uint256 badSlotWord = _buildSlotWord(slotId, badPrice, spread0, spread1, block.timestamp * 1000);
vm.prank(pusher); // attacker controls this key
(bool ok,) = address(oracle).call(abi.encodePacked(badSlotWord));
assertTrue(ok); // bad price written into creator's feed → consumed by pool
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
