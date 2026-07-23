### Title
`allowPushers` Signature Replay Within Deadline Window Bypasses Pusher Revocation, Enabling Namespace Hijack and Bad-Price Injection — (`File: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracleV1.allowPushers()` contains no nonce or one-time-use mechanism. A creator who holds a pusher's consent signature can replay it an unlimited number of times within the deadline window, re-establishing delegation even after the pusher has called `revokePusher()`. This is the direct structural analog to the PrePO `setFinalLongPayout`-called-twice bug: a state-finalizing action (revocation) can be silently undone by replaying the same authorization, causing the pusher's data to land in the wrong namespace and feeding bad prices into pools.

---

### Finding Description

`allowPushers` verifies a pusher's EIP-191 signature over `(block.chainid, address(this), deadline, pusher, msg.sender)` and unconditionally writes `namespaceRemapping[pusher] = msg.sender`. [1](#0-0) 

The code comment explicitly acknowledges the replay risk and claims the deadline solves it: [2](#0-1) 

However, the deadline only prevents replay **after** it expires. Within the deadline window, the same `(deadline, pusher, creator)` tuple is accepted by `allowPushers` on every call — there is no nonce, no consumed-signature registry, and no check that `namespaceRemapping[pusher]` is currently `address(0)`. The revocation path clears the mapping: [3](#0-2) 

But the creator already holds the old signature and can immediately replay it to restore `namespaceRemapping[pusher] = creatorA`, making `revokePusher()` a no-op within the deadline window.

The `fallback` push path resolves the namespace at call time from `namespaceRemapping[msg.sender]`: [4](#0-3) 

So any push the pusher makes after believing they have revoked still lands in the creator's namespace, not their own.

---

### Impact Explanation

After a pusher revokes and begins pushing data intended for their own namespace (e.g., a different market, a different price scale, or a different asset pair), the creator replays the old signature. Every subsequent fallback push from the pusher is silently redirected into the creator's namespace. Pools whose `PriceProvider` reads `feedIdOf(creator, slotIndex, positionIndex)` now receive prices that were never intended for that feed. This is a **bad-price execution** path: the bid/ask quote reaching the pool swap is wrong because the underlying oracle slot contains data from a misattributed pusher.

Additionally, if the pusher revoked because their signing key was compromised and they wanted to stop bad data from flowing into the creator's feeds, the creator's replay of the old signature re-opens that channel, allowing the compromised key to continue writing into the creator's namespace.

---

### Likelihood Explanation

- The creator already possesses the signature — they submitted it in the original `allowPushers` call.
- Deadlines are typically set days or weeks in the future (the test suite uses `block.timestamp + 1 days`).
- The replay requires a single permissionless transaction from the creator with no additional cost.
- The pusher has no on-chain mechanism to invalidate the old signature before the deadline expires. [5](#0-4) 

---

### Recommendation

Add a per-pusher nonce to the signature domain and increment it on every successful `allowPushers` call, or maintain a `usedSignatures` mapping keyed on the signature hash. Either approach makes each consent signature single-use, so a revoked pusher's old signature cannot be replayed:

```solidity
// Option A: per-pusher nonce
mapping(address => uint256) public pusherNonce;

// In allowPushers:
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(
        block.chainid, address(this), deadline,
        pusher, msg.sender,
        pusherNonce[pusher]   // ← bind to current nonce
    ))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
pusherNonce[pusher]++;        // ← consume the nonce
namespaceRemapping[pusher] = msg.sender;
```

Alternatively, check that `namespaceRemapping[pusher] == address(0)` before accepting the signature, forcing the creator to obtain a fresh signature after any revocation.

---

### Proof of Concept

```
1. Pusher signs consent for creatorA with deadline = block.timestamp + 1 days.
   sig = sign(keccak256(chainid, oracle, deadline, pusher, creatorA))

2. creatorA calls allowPushers(deadline, [pusher], [sig])
   → namespaceRemapping[pusher] = creatorA  ✓

3. Pusher calls revokePusher()
   → namespaceRemapping[pusher] = address(0)  (pusher believes they are free)

4. creatorA calls allowPushers(deadline, [pusher], [sig])  ← SAME signature, deadline still valid
   → namespaceRemapping[pusher] = creatorA  ← revocation silently undone

5. Pusher pushes a slot word via fallback() intending to update their OWN namespace.
   → fallback resolves creator = namespaceRemapping[pusher] = creatorA
   → slot is written into creatorA's namespace, not the pusher's own

6. Pools reading feedIdOf(creatorA, slotIndex, positionIndex) now receive the
   pusher's misattributed data as the live bid/ask price.
``` [6](#0-5) [7](#0-6)

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

**File:** smart-contracts-poc/test/oracles/compressed/CompressedOracle.t.sol (L339-342)
```text
    function testAllowPushersDelegatesNamespace() public {
        uint256 deadline = block.timestamp + 1 days;
        _allowPusher(deadline);
        assertEq(oracle.namespaceRemapping(pusher), creator, "pusher should map to creator");
```
