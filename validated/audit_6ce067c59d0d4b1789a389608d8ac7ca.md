### Title
Pusher Delegation Signature Replay Allows Creator to Permanently Override Pusher's Self-Revocation — (File: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol)

---

### Summary

The `allowPushers` function in `CompressedOracleV1` signs pusher consent over `(chainid, oracle, deadline, pusher, creator)` with **no nonce and no used-signature tracking**. After a pusher calls `revokePusher()`, the creator can immediately replay the same valid (non-expired) signature to re-establish the delegation. The pusher's subsequent price pushes — which the pusher believes land in their own namespace — are silently redirected into the creator's namespace, enabling bad-price injection into any pool that reads from that creator's feeds.

---

### Finding Description

`allowPushers` verifies the pusher's EIP-191 consent signature over:

```solidity
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
``` [1](#0-0) 

There is no nonce, no per-pusher revocation counter, and no record of consumed signatures. The only replay barrier is the deadline, which only blocks signatures whose deadline has already elapsed.

`revokePusher()` clears the mapping:

```solidity
namespaceRemapping[msg.sender] = address(0);
``` [2](#0-1) 

But because the original signature is still cryptographically valid until `deadline`, the creator can call `allowPushers` again with the identical `(deadline, pusher, signature)` tuple and the mapping is restored:

```solidity
namespaceRemapping[pusher] = msg.sender;   // re-written unconditionally
``` [3](#0-2) 

The code comment itself acknowledges the concern but misidentifies the deadline as the fix:

> *"The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."* [4](#0-3) 

The deadline prevents replay of an **expired** signature; it does not prevent replay of a **live** signature after revocation. The creator can loop: `allowPushers → pusher revokes → allowPushers → pusher revokes → …` indefinitely until the deadline expires, making `revokePusher()` a no-op for the entire consent window.

The fallback push path resolves the effective namespace from `namespaceRemapping[msg.sender]`: [5](#0-4) 

So every push the pusher makes after believing they have revoked is silently written into the creator's namespace instead of their own.

---

### Impact Explanation

A pool that reads from the creator's `feedId` (via `CompressedOracleV1.price()` or a `PriceProvider` wrapping it) will consume whatever the hijacked pusher writes. If the pusher — now operating independently — is pushing prices for a **different asset pair or at a different scale** than the creator's pool expects, the pool executes swaps at a corrupted mid-price and spread, causing:

- **Bad-price execution**: traders receive more output than the correct oracle/bin curve permits, or LPs receive less input than owed.
- **Pool insolvency**: repeated bad-price swaps drain LP reserves below the level needed to cover outstanding claims.

The `feedIdOf` encoding ties the price slot directly to the creator's address, so there is no additional guard between the hijacked push and the pool's price read: [6](#0-5) 

---

### Likelihood Explanation

- The creator is a **semi-trusted** party (not a protocol admin), so malicious replay is a realistic threat model.
- Automated pushers (bots, keeper networks) routinely continue pushing after revoking a specific delegation, because they push to their own namespace for other consumers.
- The consent window can be arbitrarily long (the pusher chooses the deadline at signing time, but the creator presents it); a 30-day or 1-year deadline is common in production key-management flows.
- The replay requires only a single on-chain call with already-known calldata — no new signature, no new key material.

---

### Recommendation

Track consumed signatures or maintain a per-pusher revocation nonce. The simplest fix is a `mapping(bytes32 => bool) private _usedConsents` keyed on the signature hash, set to `true` on first use and checked before writing `namespaceRemapping`. Alternatively, include a monotonically increasing per-pusher nonce in the signed payload so that each revocation invalidates all prior signatures for that pusher/creator pair.

---

### Proof of Concept

```
// Setup
deadline = block.timestamp + 365 days
sig = pusher.sign(keccak256(chainid, oracle, deadline, pusher, creator))

// Step 1 – establish delegation
vm.prank(creator);
oracle.allowPushers(deadline, [pusher], [sig]);
// namespaceRemapping[pusher] == creator  ✓

// Step 2 – pusher self-revokes
vm.prank(pusher);
oracle.revokePusher();
// namespaceRemapping[pusher] == address(0)  ✓

// Step 3 – creator replays the SAME signature
vm.prank(creator);
oracle.allowPushers(deadline, [pusher], [sig]);   // identical calldata, no revert
// namespaceRemapping[pusher] == creator  ← revocation bypassed

// Step 4 – pusher pushes BTC/USD prices thinking they go to own namespace
vm.prank(pusher);
oracle.call(btcUsdSlotWord);
// Data lands in creator's ETH/USD pool feed → bad-price execution
```

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L49-53)
```text
    function feedIdOf(address creator, uint8 slotIndex, uint8 positionIndex) public view returns (bytes32) {
        return bytes32(
            uint256(uint160(creator)) << 96 | block.chainid << 16 | uint256(slotIndex) << 8 | positionIndex
        );
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L241-242)
```text
        namespaceRemapping[msg.sender] = address(0);
        emit PusherRevoked(msg.sender, creator);
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L331-344)
```text
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
