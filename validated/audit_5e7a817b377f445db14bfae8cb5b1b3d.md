### Title
Pusher revocation can be silently undone by creator replaying a non-expired consent signature — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` in `CompressedOracleV1` verifies a pusher's EIP-191 consent signature but never marks it as used. After a pusher calls `revokePusher()`, the creator can replay the original signature — as long as its deadline has not expired — to silently overwrite the revocation and re-route the pusher's future price updates back into the creator's namespace. The code's own comment acknowledges the risk but the deadline does not close it within its validity window.

---

### Finding Description

`allowPushers` binds the signature to `(chainid, oracle, deadline, pusher, creator)` and writes `namespaceRemapping[pusher] = msg.sender`: [1](#0-0) 

There is no nonce and no used-signature registry. The same bytes can be submitted an unlimited number of times before `deadline`.

When a pusher calls `revokePusher()`, the mapping is cleared to `address(0)`: [2](#0-1) 

Because the signature is still valid (deadline not yet reached), the creator can immediately call `allowPushers` again with the identical arguments, overwriting `address(0)` back to `creator`. The revocation is silently undone.

The code comment in `allowPushers` explicitly acknowledges the risk: [3](#0-2) 

> *"the deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."*

The comment implies the deadline closes the window. It does not: the deadline only prevents replay after expiry. Any signature with a far-future deadline (e.g. 1 year) can be replayed throughout that entire window.

After re-establishment, the `fallback` push path resolves the namespace from `namespaceRemapping[msg.sender]` and writes into the creator's slot: [4](#0-3) 

Any price pushed by the (now re-delegated) pusher lands in the creator's namespace and is consumed by `getOracleData` → `price` → downstream `PriceProvider` → `MetricOmmPool.swap`.

---

### Impact Explanation

If a pusher's private key is compromised, `revokePusher()` is the pusher's primary self-protection mechanism. If the creator (or an automated keeper that re-establishes delegations on revocation) replays the old signature, the attacker holding the compromised key regains write access to the creator's namespace. Bad prices pushed through the `fallback` flow directly into pool swaps, causing bad-price execution for traders — a direct loss of user principal matching the "bad-price execution" impact gate.

---

### Likelihood Explanation

Medium. Requires: (1) a pusher to revoke, (2) the creator to hold a valid non-expired signature, and (3) the creator to replay it. Automated keeper systems that monitor `PusherRevoked` events and re-establish delegations are a realistic deployment pattern. Signatures with multi-month deadlines are the norm for operational convenience, leaving a long replay window.

---

### Recommendation

Track each signature hash as consumed after the first successful `allowPushers` call:

```solidity
mapping(bytes32 => bool) public usedDelegationSignatures;

// inside allowPushers, after ECDSA.recover succeeds:
require(!usedDelegationSignatures[hash], "signature already used");
usedDelegationSignatures[hash] = true;
```

Alternatively, include a per-pusher nonce in the signed message so the pusher can invalidate old signatures by incrementing their nonce, independent of the deadline.

---

### Proof of Concept

```
1. Pusher P signs consent for creator A:
     sig = sign(chainid, oracle, deadline=now+365days, pusher=P, creator=A)

2. Creator A calls allowPushers(deadline, [P], [sig])
     → namespaceRemapping[P] = A  ✓

3. P's key is compromised; P calls revokePusher()
     → namespaceRemapping[P] = address(0)  ✓

4. Creator A (or keeper) replays the SAME sig:
     allowPushers(deadline, [P], [sig])   // deadline still valid
     → namespaceRemapping[P] = A  ← revocation silently overwritten

5. Attacker (holding P's key) calls oracle.fallback() with crafted slot word
     → namespace resolves to A (line 315-316)
     → bad price written into creator A's slot

6. Pool reads bad price via getOracleData → price → PriceProvider
     → swap executes at attacker-controlled bid/ask
     → trader receives less than oracle/bin curve permits
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L192-211)
```text
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L315-343)
```text
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;

        assembly ("memory-safe") {
            end := calldatasize()
            namespace := shl(96, creator) // [creator:20][zeros:12]
        }

        // 4 * 6 + 7 + 1 = 32 bytes per slot
        if (end == 0 || end % 32 != 0) revert BadCalldataLength();

        for (uint256 ptr = 0; ptr < end; ptr += 32) {
            uint256 word;
            assembly ("memory-safe") {
                word := calldataload(ptr)
            }
            // casting to 'uint8' is safe we want LSB
            // forge-lint: disable-next-line(unsafe-typecast)
            uint8 slotId = uint8(word);
            TimeMs timestampMs = toTimeMs(word >> 8 & X56);
            timestampMs.revertIfAfterBlockTimeWithDrift(MAX_TIME_DRIFT);
            bytes32 key = bytes32(namespace | uint256(slotId));
            uint256 old = uint256(_loadStorage(key));
            TimeMs oldTimestampMs = toTimeMs(old >> 8 & X56);

            bool newer = timestampMs.isAfter(oldTimestampMs);
            if (!newer) continue;

            _writeStorage(key, bytes32(bytes32(word & ~uint256(0xff))));
```
