### Title
Pusher Revocation Can Be Bypassed by Creator Replaying the Original Delegation Signature Before Deadline Expiry — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracle.allowPushers` does not prevent a creator from replaying a pusher's original consent signature to re-establish a delegation that the pusher just revoked via `revokePusher()`. As long as the original deadline has not expired, the creator can call `allowPushers` again with the same signature, immediately restoring the delegation and nullifying the pusher's revocation. The pusher cannot effectively stop being a delegated namespace writer until the deadline in their original consent expires — an exact analog to M-19's "old delay still applies after parameter change."

---

### Finding Description

`allowPushers` requires a pusher's EIP-191 signature over `(chainid, oracle, deadline, pusher, creator)`. The code's own comment explicitly acknowledges the replay risk:

> "The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."

However, the deadline only prevents using the signature **after** the deadline expires. Before expiry, the signature remains cryptographically valid and the function has no mechanism to detect that it was already used and subsequently revoked.

Attack sequence:

1. Pusher signs consent with `deadline = now + 30 days`.
2. Creator calls `allowPushers(deadline, [pusher], [sig])` → `namespaceRemapping[pusher] = creator`.
3. Pusher calls `revokePusher()` → `namespaceRemapping[pusher] = address(0)`.
4. Creator immediately calls `allowPushers(deadline, [pusher], [sig])` again with the **same** signature — `_ensureDeadline` passes (deadline still in the future), ECDSA recovery succeeds (signature is still valid), `namespaceRemapping[pusher] = creator` is written again.
5. Pusher's revocation is nullified. Their subsequent fallback pushes land in the creator's namespace, not their own.

The pusher is stuck in the delegated state until the deadline expires — exactly the M-19 pattern where a parameter change (revocation) is supposed to take effect immediately but existing state (the still-valid signature) forces the old behavior to persist.

Relevant code: [1](#0-0) 

The `revokePusher` path that is bypassed: [2](#0-1) 

The fallback push path that uses the restored remapping: [3](#0-2) 

---

### Impact Explanation

After the creator replays the signature, the pusher's fallback pushes land in the creator's namespace instead of the pusher's own namespace. The pusher, believing they revoked and are now writing to their own namespace, may push data (prices, spreads, timestamps) that they did not intend for the creator's namespace. This data is then decoded by `getOracleData` / `price` and consumed by price providers (`PriceProvider`, `ProtectedPriceProvider`, `AnchoredPriceProvider`) that feed live bid/ask quotes into pool swaps. Corrupted or unintended price data in the creator's namespace constitutes bad-price execution reaching production pools.

Additionally, the pusher's own namespace remains empty (their intended writes go to the creator's namespace), so any pool or integrator reading the pusher's own feeds sees stale/zero data.

---

### Likelihood Explanation

- Signatures are typically stored off-chain by the creator (required to call `allowPushers` in the first place).
- Deadlines are set to be long (days to weeks) to accommodate operational windows — the replay window is correspondingly large.
- The creator is a semi-trusted actor with a clear incentive to keep a pusher delegated (e.g., to maintain feed freshness after the pusher tries to exit).
- The attack requires no special privileges beyond being the original creator who called `allowPushers`.

---

### Recommendation

Track consumed signatures to prevent replay. The simplest approach is a per-pusher revocation nonce: increment a `uint256 revocationNonce[pusher]` on every `revokePusher` / `removePushers` call and include it in the signed message:

```solidity
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender, revocationNonce[pusher]))
```

After revocation the nonce increments, invalidating all prior signatures. Alternatively, maintain a `mapping(bytes32 => bool) usedSignatureHashes` and mark each accepted signature hash as consumed so it cannot be replayed.

---

### Proof of Concept

```solidity
// Setup: pusher signs consent with a 30-day deadline
uint256 deadline = block.timestamp + 30 days;
bytes32 digest = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(oracle), deadline, pusher, creator))
);
bytes memory sig = sign(PUSHER_KEY, digest);

// Step 1: creator establishes delegation
vm.prank(creator);
oracle.allowPushers(deadline, _arr(pusher), _arr(sig));
assertEq(oracle.namespaceRemapping(pusher), creator);

// Step 2: pusher revokes
vm.prank(pusher);
oracle.revokePusher();
assertEq(oracle.namespaceRemapping(pusher), address(0)); // revoked

// Step 3: creator replays the SAME signature — succeeds because deadline is still valid
vm.prank(creator);
oracle.allowPushers(deadline, _arr(pusher), _arr(sig)); // no revert
assertEq(oracle.namespaceRemapping(pusher), creator);   // delegation restored

// Step 4: pusher's next push (intended for own namespace) lands in creator's namespace
uint56 tsMs = uint56(block.timestamp * 1000);
uint48 badPrice = _packRaw(9_999_999, 5, 5); // pusher's own intended data
vm.prank(pusher);
(bool ok,) = address(oracle).call(_wordAt(0, 0, badPrice, tsMs));
assertTrue(ok);

// Data is in creator's namespace, not pusher's
assertGt(oracle.getOracleData(oracle.feedIdOf(creator, 0, 0)).price, 0);
assertEq(oracle.getOracleData(oracle.feedIdOf(pusher,  0, 0)).price, 0);
// Creator's namespace now holds unintended price data that feeds into live pool swaps
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L236-243)
```text
    /// @notice Allows a pusher to self-revoke their delegation. After revocation the
    ///         wallet pushes into its OWN namespace again (the registrationless default).
    function revokePusher() external {
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
        namespaceRemapping[msg.sender] = address(0);
        emit PusherRevoked(msg.sender, creator);
    }
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L311-344)
```text
    fallback() override external {
        uint256 end;
        uint256 namespace;

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
        }
```
