### Title
`allowPushers` Signature Has No Nonce — Creator Can Replay Old Consent Within Deadline Window to Re-Establish Revoked Delegation and Enable Bad-Price Injection - (File: `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

### Summary

`CompressedOracleV1.allowPushers` signs pusher consent over `(chainid, oracle, deadline, pusher, creator)` with no nonce. Within the deadline window the identical signature is valid on every call. After a pusher calls `revokePusher()` — the only on-chain mechanism to withdraw consent — the creator can immediately replay the original signature to re-establish `namespaceRemapping[pusher] = creator`, silently undoing the revocation. If the pusher revoked because their key was compromised, the creator's innocent replay re-opens the creator's namespace to the attacker, who can push arbitrary prices that downstream price providers and pools will consume.

### Finding Description

`allowPushers` constructs the signed digest as:

```solidity
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
``` [1](#0-0) 

There is no per-use nonce. The function's own NatDoc states the deadline is the sole guard against re-establishing delegation after revocation:

> "an undated signature could re-establish a delegation AFTER the pusher revoked it" [2](#0-1) 

But the deadline only blocks calls made **after** `block.timestamp > deadline`. Within the window `[now, deadline]` the signature is unconditionally reusable. `revokePusher` clears `namespaceRemapping[pusher]` to `address(0)`:

```solidity
namespaceRemapping[msg.sender] = address(0);
``` [3](#0-2) 

A subsequent call to `allowPushers` with the same `(deadline, pusher, sig)` tuple passes `_ensureDeadline` and the ECDSA check identically, and writes `namespaceRemapping[pusher] = creator` again — overwriting the revocation. [4](#0-3) 

The `fallback` push path resolves the namespace at call time:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [5](#0-4) 

So once the delegation is re-established, every subsequent push from the compromised pusher key lands in the **creator's** namespace, not the pusher's own isolated namespace. The creator's namespace is the one registered with pools and consumed by price providers.

### Impact Explanation

A compromised pusher key that has been revoked can be re-activated by the creator's innocent replay of the original consent signature. The attacker then pushes a crafted slot word with a fresh timestamp (passing the monotonicity gate) into the creator's namespace. The `CompressedOracleV1.price` path is open (no `inSwap` binding), so the bad price is immediately readable. Any `PriceProvider` or `AnchoredPriceProvider` pointing at that feed will serve the attacker-controlled bid/ask to the pool during the next swap, causing bad-price execution and potential pool insolvency.

### Likelihood Explanation

Pusher key rotation is a normal operational event. Creators are expected to hold consent signatures for their pushers (they submitted them on-chain originally). A creator who notices a pusher went offline and tries to "re-add" them using the cached signature — without knowing the key was stolen — triggers the vulnerability with a single transaction. No mempool racing is required; the creator acts in good faith.

### Recommendation

Add a per-pusher nonce to the signed digest and increment it on every successful `allowPushers` call:

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

This makes each consent signature single-use. After `revokePusher()` the nonce has already been consumed, so the old signature is permanently invalid regardless of the deadline.

### Proof of Concept

```
1. Creator C signs nothing; Pusher P signs consent for C with deadline = now + 30 days.
2. C calls allowPushers(deadline, [P], [sig]) → namespaceRemapping[P] = C.
3. P's key is stolen. P calls revokePusher() → namespaceRemapping[P] = 0.
4. C, unaware of the compromise, calls allowPushers(deadline, [P], [sig]) again
   with the SAME signature → namespaceRemapping[P] = C (revocation undone).
5. Attacker (holding P's key) calls oracle.call(craftedSlotWord) where craftedSlotWord
   encodes price = 0 (or MAX_UINT32) with timestamp = block.timestamp * 1000.
6. Monotonicity check passes (fresh timestamp > stored timestamp).
7. Slot is written into C's namespace.
8. Pool swap calls provider.getBidAndAskPrice() → oracle.price(feedId, pool) →
   returns attacker-controlled mid price → pool executes swap at wrong price.
``` [4](#0-3) [6](#0-5)

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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L192-212)
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
