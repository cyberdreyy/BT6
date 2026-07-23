### Title
`allowPushers` Consent Signature Has No Nonce, Enabling Replay Within the Deadline Window to Silently Restore a Revoked Pusher and Inject Bad Prices - (File: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol)

---

### Summary

`CompressedOracleV1.allowPushers` signs pusher consent over `(chainid, oracle, deadline, pusher, creator)` with **no nonce and no used-signature tracking**. Within the deadline window the identical signature is accepted an unlimited number of times. A pusher who self-revokes via `revokePusher()` can therefore have their delegation silently re-established by the creator replaying the original consent bytes, allowing a compromised pusher key to resume injecting prices into the creator's oracle namespace — prices that flow directly into pool swaps through `AnchoredPriceProvider`.

---

### Finding Description

`allowPushers` constructs the signed digest as:

```solidity
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
namespaceRemapping[pusher] = msg.sender;
``` [1](#0-0) 

The signed tuple contains no nonce, no per-use counter, and no on-chain record that the signature was ever consumed. The only guard is `_ensureDeadline(deadline)`, which only checks `block.timestamp <= deadline`. [2](#0-1) 

The code comment at lines 186–191 explicitly acknowledges the risk:

> *"The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."* [3](#0-2) 

The deadline is intended to bound the replay window, but it does **not** prevent replay within that window. `revokePusher()` only writes `namespaceRemapping[msg.sender] = address(0)`: [4](#0-3) 

After revocation the creator can immediately call `allowPushers` again with the original bytes and the mapping is restored — no new signature from the pusher is required. There is no nonce, no used-signature set, and no other on-chain state that would distinguish the second call from the first.

The `updateBySignature` path avoids this problem because the signed `newSlotValue` embeds a 56-bit timestamp and the monotonicity check rejects any replay of an older slot value: [5](#0-4) 

`allowPushers` has no equivalent monotonicity guard.

---

### Impact Explanation

The price path from oracle to pool is:

1. `CompressedOracleV1.fallback()` — pusher writes a slot word into the creator's namespace.
2. `AnchoredPriceProvider._readLeg()` calls `IPricedOracle(offchainOracle).price(feedId, pool)`, which reads `getOracleData(feedId)` → `U64x32.decode(compressed.p)` and `Codebook256.decode(s0/s1)`. [6](#0-5) 

3. `_computeBidAsk` converts the mid price to a Q64.64 bid/ask band and returns it to the pool. [7](#0-6) 

4. The pool uses the bid/ask to price every swap (`MetricOmmPool._getBidAndAskPriceX64`).

If the replayed delegation allows a compromised pusher key to write a manipulated price into the creator's namespace, that price propagates through the entire chain above. The attacker can push any price that passes the `priceGuard` bounds (which are set by the creator and may be wide or unset). A manipulated mid price shifts the entire bid/ask band, causing traders to receive worse execution than the true market price — a direct loss of user principal on every swap until the price is corrected.

---

### Likelihood Explanation

- A pusher signs consent with a long deadline (common for operational convenience; there is no cap on the deadline value).
- The pusher's signing key is later compromised or the pusher decides to exit.
- The pusher calls `revokePusher()` to stop the attacker.
- The creator — who may be unaware of the compromise — calls `allowPushers` again with the cached original signature bytes (e.g., stored in a deployment script or off-chain database) to "restore" the pusher, believing the revocation was accidental.
- The attacker resumes pushing manipulated prices.

The scenario requires the creator to replay the signature, which is a non-zero probability in operational settings where the original `allowPushers` calldata is retained. The deadline can be set to `type(uint256).max`, making the replay window effectively permanent.

---

### Recommendation

Add a per-pusher nonce to the signed digest and increment it on each successful `allowPushers` call:

```solidity
mapping(address => uint256) public pusherNonce;

// inside allowPushers loop:
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(
        block.chainid, address(this), deadline,
        pusher, msg.sender, pusherNonce[pusher]
    ))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
pusherNonce[pusher]++;          // invalidates all prior signatures
namespaceRemapping[pusher] = msg.sender;
```

Alternatively, invalidate all prior signatures for a pusher when `revokePusher()` or `removePushers()` is called by incrementing the nonce there. Either approach ensures that a signature consumed once cannot be replayed, and that a revocation is final.

---

### Proof of Concept

```solidity
function testAllowPushersSignatureReplay() public {
    // Pusher signs consent with a long deadline
    uint256 deadline = block.timestamp + 365 days;
    bytes memory sig = _signConsent(PUSHER_KEY, deadline, pusher, creator);

    address[] memory pushers = new address[](1);
    pushers[0] = pusher;
    bytes[] memory sigs = new bytes[](1);
    sigs[0] = sig;

    // Step 1: creator establishes delegation
    vm.prank(creator);
    oracle.allowPushers(deadline, pushers, sigs);
    assertEq(oracle.namespaceRemapping(pusher), creator, "delegated");

    // Step 2: pusher revokes (key compromised)
    vm.prank(pusher);
    oracle.revokePusher();
    assertEq(oracle.namespaceRemapping(pusher), address(0), "revoked");

    // Step 3: creator replays the SAME signature — no new consent from pusher needed
    vm.prank(creator);
    oracle.allowPushers(deadline, pushers, sigs); // REPLAY
    assertEq(oracle.namespaceRemapping(pusher), creator, "delegation silently restored");

    // Step 4: attacker (holding compromised key) pushes a manipulated price
    uint56 tsMs = uint56(block.timestamp * 1000);
    // encode a price of 999_999_999 (8-dec) — far from true market
    uint32 manipulatedP = uint32((uint32(0) << 27) | uint32(999_999_999 & ((1 << 27) - 1)));
    uint48 badRaw = (uint48(manipulatedP) << 16) | (uint48(0) << 8) | uint48(0);
    uint256 word = (uint256(tsMs) << 8) | uint256(0); // slotId = 0
    word |= uint256(badRaw) << 208;                   // position 0

    vm.prank(pusher); // attacker using compromised key
    (bool ok,) = address(oracle).call(abi.encodePacked(word));
    assertTrue(ok, "attacker pushes bad price into creator namespace");

    // Bad price is now live in the creator's namespace, ready to be consumed by AnchoredPriceProvider
    IOffchainOracle.OracleData memory data =
        oracle.getOracleData(oracle.feedIdOf(creator, 0, 0));
    assertGt(data.price, 0, "manipulated price stored");
}
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L271-303)
```text
    function updateBySignature(address feedCreator, uint256 newSlotValue, bytes calldata signature)
        external
        override
        returns (bool)
    {
        require(feedCreator != address(0), InvalidNamespace());

        uint256 namespace;
        assembly ("memory-safe") {
            namespace := shl(96, feedCreator) // [creator:20][zeros:12]
        }

        uint8 slotId = uint8(newSlotValue); // LSB
        TimeMs timestampMs = toTimeMs(newSlotValue >> 8 & X56);
        timestampMs.revertIfAfterBlockTimeWithDrift(MAX_TIME_DRIFT);
        bytes32 key = bytes32(namespace | uint256(slotId));
        uint256 old = uint256(_loadStorage(key));
        TimeMs oldTimestampMs = toTimeMs(old >> 8 & X56);

        bool newer = timestampMs.isAfter(oldTimestampMs);
        if (!newer) {
            return false;
        }

        bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
            keccak256(abi.encode(block.chainid, address(this), feedCreator, newSlotValue))
        );
        require(feedCreator == ECDSA.recover(hash, signature));

        _writeStorage(key, bytes32(newSlotValue & ~uint256(0xff)));

        return true;
    }
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L277-295)
```text
    function _readLeg(bytes32 feedId)
        internal returns (uint256 mid, uint256 spreadBps, uint256 refTime, bool ok)
    {
        (mid, spreadBps, , refTime) = IPricedOracle(address(offchainOracle)).price(feedId, msg.sender);

        // Stale reference → not ok. Clamping to a stale anchor is the one false-safety case.
        if (_isStale(refTime, block.timestamp, MAX_REF_STALENESS)) return (mid, spreadBps, refTime, false);

        // Basic validity — mid positive, spreadBps not the stalled/off-hours marker (the Chainlink oracle
        // writes spreadBps = ORACLE_BPS when an RWA market is closed).
        if (mid == 0 || spreadBps >= ORACLE_BPS) return (mid, spreadBps, refTime, false);

        // Per-leg price guard.
        (uint128 guardMin, uint128 guardMax) = offchainOracle.priceGuard(feedId);
        guardMax = guardMax == 0 ? type(uint128).max : guardMax;
        if (mid < guardMin || mid > guardMax) return (mid, spreadBps, refTime, false);

        ok = true;
    }
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L299-349)
```text
    function _computeBidAsk(uint256 mid, uint256 spreadBps)
        internal view returns (uint128, uint128)
    {
        // Circuit breaker: extreme (combined) uncertainty means the feed is clearly broken.
        if (spreadBps > MAX_SPREAD_BPS) {
            return (0, type(uint128).max);
        }

        // Reference band: mid ± (spreadBps + minMargin), bid rounded down, ask rounded up.
        uint256 half = spreadBps * ONE_BPS_E18 + minMargin; // < BPS_BASE_U by construction (spreadBps <= MAX_SPREAD_BPS here)
        uint256 refBid = _bandEdge(mid, BPS_BASE_U - half, Math.Rounding.Floor);
        uint256 refAsk = _bandEdge(mid, BPS_BASE_U + half, Math.Rounding.Ceil);
        if (refBid == 0 || refAsk > type(uint128).max || refBid >= refAsk) {
            return (0, type(uint128).max);
        }

        // Custom quote: source (both variants) or shaped reference quote (customizable variant).
        //    Immutable reference mode quotes the band directly — zero knob SLOADs.
        address _source = source;
        uint256 cBid;
        uint256 cAsk;
        if (_source != address(0)) {
            // 7a. Source mode: any failure (revert, OOG, garbage, zero, inverted) halts — fail
            //     closed. Knobs do NOT post-process the source output (the source shapes itself).
            bool ok;
            (ok, cBid, cAsk) = _readSource(_source);
            if (!ok) {
                return (0, type(uint128).max);
            }
        } else if (MUTABLE_PARAMS) {
            // 7b. Shaped reference quote: mid ± mid·spreadBps·confidence, then the marginStep step
            //     factors — PriceProvider semantics, clamped into the band below.
            bool ok;
            (ok, cBid, cAsk) = _shapedQuote(mid, spreadBps);
            if (!ok) {
                return (0, type(uint128).max);
            }
        } else {
            return (uint128(refBid), uint128(refAsk));
        }

        // 8. Clamp: out-of-band custom quotes are clipped silently to the band edge.
        //    bid ≤ refBid < refAsk ≤ ask, so bid < ask holds by construction.
        uint256 bidOut = Math.min(refBid, cBid);
        uint256 askOut = Math.max(refAsk, cAsk);
        if (bidOut == 0 || bidOut >= askOut) {
            return (0, type(uint128).max);
        }

        return (uint128(bidOut), uint128(askOut));
    }
```
