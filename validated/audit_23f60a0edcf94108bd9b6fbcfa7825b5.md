### Title
`allowPushers` Consent Signature Has No Nonce — Creator Can Replay a Revoked Delegation Within the Deadline Window, Silently Re-Routing Pusher Writes Into the Creator's Namespace - (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracleV1.allowPushers` signs pusher consent over `(chainid, address(this), deadline, pusher, creator)` with **no nonce or per-delegation counter**. After a pusher calls `revokePusher()` to clear `namespaceRemapping[pusher]`, the creator can replay the original consent signature — still cryptographically valid until the deadline — to silently re-establish the delegation. The pusher's revocation is ineffective for the entire remaining deadline window, and any automated push from the pusher continues landing in the creator's namespace rather than the pusher's own.

---

### Finding Description

`allowPushers` writes `namespaceRemapping[pusher] = msg.sender` after verifying the pusher's EIP-191 signature over:

```solidity
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
``` [1](#0-0) 

There is no nonce, no per-delegation counter, and no revocation flag in the signed payload. The only replay guard is the deadline, which the code comment at lines 186–191 explicitly acknowledges:

> *"The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."* [2](#0-1) 

However, the deadline only prevents replay **after it expires**. Within the deadline window the same signature is accepted an unlimited number of times. `revokePusher()` clears the mapping to `address(0)`:

```solidity
namespaceRemapping[msg.sender] = address(0);
``` [3](#0-2) 

But `allowPushers` performs no check on whether the pusher has already revoked; it unconditionally overwrites the mapping. The revocation state is lost — a direct structural analog to M-03, where the second `grab` overwrites `vaultOwners[vaultId]`.

The `fallback` push path resolves the namespace at call time:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [4](#0-3) 

So once the delegation is re-established, every subsequent fallback push from the pusher lands in the creator's namespace, not the pusher's own — exactly as before revocation.

---

### Impact Explanation

The `namespaceRemapping` entry is the sole authority gate for where a pusher's slot writes land. After the creator replays the old signature:

1. The pusher's `revokePusher()` call is silently undone; `namespaceRemapping[pusher]` is restored to `creator`.
2. Any automated pusher (bot, keeper, off-chain relay) that does not re-check `namespaceRemapping` before each push continues writing into the creator's namespace.
3. The creator's pool, which reads prices from feeds in that namespace via `feedIdOf(creator, slotIndex, positionIndex)`, continues to receive price updates attributed to the pusher — even though the pusher intended to stop providing them.
4. If the pusher revoked because they detected a problem with the creator's pool or feed configuration, the re-delegation forces their price data to keep flowing to that pool, potentially enabling bad-price execution at swap time.

The corrupted value is `namespaceRemapping[pusher]`: it should be `address(0)` (revoked) but is restored to `creator` without the pusher's current consent.

---

### Likelihood Explanation

- The creator must have retained the original consent signature (trivial: it is a public transaction parameter, visible on-chain and in mempool).
- The deadline must not yet have expired. Deadlines are chosen by the creator; a creator who anticipates needing long-lived delegation will set a far-future deadline, maximising the replay window.
- The pusher must be an automated system (the common production case for price-feed bots) that does not re-query `namespaceRemapping` before each push.

All three conditions are realistic in a production deployment.

---

### Recommendation

Add a per-pusher revocation nonce to the signed payload:

```solidity
mapping(address => uint256) public pusherNonce;

// In allowPushers:
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(
        block.chainid, address(this), deadline,
        pusher, msg.sender,
        pusherNonce[pusher]   // <-- add nonce
    ))
);
// After writing namespaceRemapping:
pusherNonce[pusher]++;

// In revokePusher / removePushers:
pusherNonce[msg.sender]++;   // invalidate any outstanding signatures
```

Incrementing the nonce on revocation ensures that any previously issued consent signature is immediately invalidated, regardless of its deadline.

---

### Proof of Concept

```solidity
// 1. Pusher signs consent for creator with a far-future deadline.
uint256 deadline = block.timestamp + 30 days;
bytes memory sig = pusher.sign(
    keccak256(abi.encode(chainid, oracle, deadline, pusher, creator))
);

// 2. Creator establishes delegation.
vm.prank(creator);
oracle.allowPushers(deadline, [pusher], [sig]);
// namespaceRemapping[pusher] == creator  ✓

// 3. Pusher revokes.
vm.prank(pusher);
oracle.revokePusher();
// namespaceRemapping[pusher] == address(0)  ✓

// 4. Creator replays the SAME signature — still valid, deadline not expired.
vm.prank(creator);
oracle.allowPushers(deadline, [pusher], [sig]);
// namespaceRemapping[pusher] == creator  ← revocation silently undone

// 5. Pusher's next automated push lands in creator's namespace, not pusher's own.
vm.prank(pusher);
(bool ok,) = address(oracle).call(slotWord);
assertTrue(ok);
// oracle.getOracleData(feedIdOf(creator, slotId, pos)).price != 0  ← creator receives data
// oracle.getOracleData(feedIdOf(pusher,  slotId, pos)).price == 0  ← pusher's own ns empty
``` [5](#0-4) [6](#0-5) [7](#0-6)

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
