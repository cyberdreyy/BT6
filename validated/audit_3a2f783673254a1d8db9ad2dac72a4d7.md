### Title
Pusher Delegation Signature Replay Bypasses Revocation, Allowing Continued Bad-Price Injection into Pools — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

### Summary

`allowPushers` accepts a pusher's EIP-191 consent signature that contains no nonce. After a pusher calls `revokePusher()`, the creator can immediately replay the original signature to re-establish the delegation before the deadline expires. A compromised pusher key therefore cannot be neutralised by revocation alone — the attacker retains write authority over the creator's namespace and can continue injecting arbitrary prices that reach live pool swaps through `AnchoredPriceProvider`.

### Finding Description

**Link A — Signature has no nonce; it is replayable for the full deadline window.**

`allowPushers` hashes `(block.chainid, address(this), deadline, pusher, msg.sender)` and verifies the pusher's signature against that hash. [1](#0-0) 

There is no nonce, no used-signature registry, and no per-pusher revocation counter. The same `(deadline, pusher, creator)` tuple produces the same hash every time, so the same signature passes `ECDSA.recover` on every replay until `block.timestamp > deadline`.

**Link B — `revokePusher()` clears the mapping but does not invalidate the signature.** [2](#0-1) 

`revokePusher()` sets `namespaceRemapping[msg.sender] = address(0)`. It emits `PusherRevoked` but takes no action that would prevent the creator from calling `allowPushers` again with the identical signature. The protocol's own NatDoc acknowledges the concern ("an undated signature could re-establish a delegation AFTER the pusher revoked it") but the deadline only bounds the outer window — it does not prevent replay within that window.

**Link C — Deadline window extends the attack surface.**

`_ensureDeadline` allows deadlines up to the caller's choice (no upper cap enforced in `allowPushers`). A pusher who signed consent with `deadline = block.timestamp + 7 days` gives the creator a 7-day window to replay the signature after any revocation. [3](#0-2) 

**Link D — Fallback push path has no additional authorization check.**

Once `namespaceRemapping[pusher]` is restored to `creator`, every subsequent `fallback()` call from the pusher's address writes into the creator's namespace without further verification. [4](#0-3) 

**Link E — Creator-namespace prices flow directly into pool swaps via `AnchoredPriceProvider`.**

`AnchoredPriceProvider._readLeg` calls `IPricedOracle(address(offchainOracle)).price(feedId, msg.sender)`, which for a `CompressedOracleV1`-backed feed decodes the slot written by the pusher. A manipulated mid price produces a manipulated bid/ask band that the pool uses for swap settlement. [5](#0-4) 

**Exact attack chain:**

1. Pusher signs consent for creator with `deadline = T + N days`; creator calls `allowPushers` → delegation established.
2. Pusher's key is compromised; attacker begins pushing manipulated prices into creator's namespace.
3. Real pusher calls `revokePusher()` → `namespaceRemapping[pusher] = address(0)`.
4. Creator (possibly unaware of the compromise) calls `allowPushers` again with the original, still-valid signature → `namespaceRemapping[pusher] = creator` restored.
5. Attacker continues pushing arbitrary prices through the pusher's key.
6. `AnchoredPriceProvider` reads the manipulated mid, computes a shifted bid/ask band, and the pool executes swaps at the attacker-controlled price.

### Impact Explanation

The attacker can write any price value (subject only to the timestamp monotonicity gate and the `MAX_TIME_DRIFT` future-timestamp guard) into the creator's feed namespace. `AnchoredPriceProvider` will consume this as the reference mid and compute bid/ask accordingly. Traders executing swaps against the pool receive execution at an attacker-controlled price, resulting in direct loss of principal to the pool or to counterparty traders. If no `priceGuard` is set on the feed, the price is unbounded.

### Likelihood Explanation

Requires two conditions: (1) the pusher's private key is compromised, and (2) the creator replays the original signature after revocation (either unknowingly, or because they are themselves the attacker). Both conditions are reachable without any privileged on-chain role. The deadline window (potentially days) gives ample time for the replay. Likelihood: **Medium**.

### Recommendation

Add a per-pusher nonce to the consent signature and increment it on every successful `allowPushers` call. Alternatively, maintain a `usedSignatures` mapping (keyed by the consent hash) and reject any hash that has already been consumed. Either approach ensures that a single signed consent can only establish delegation once, making `revokePusher()` final.

```solidity
// Example: nonce-based fix
mapping(address => uint256) public pusherNonce;

// In allowPushers:
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(
        block.chainid, address(this), deadline,
        pusher, msg.sender,
        pusherNonce[pusher]   // <-- add nonce
    ))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
pusherNonce[pusher]++;        // <-- invalidate on use
namespaceRemapping[pusher] = msg.sender;
```

### Proof of Concept

```solidity
// 1. Pusher signs consent (deadline = now + 1 day)
uint256 deadline = block.timestamp + 1 days;
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(oracle), deadline, pusher, creator))
);
(uint8 v, bytes32 r, bytes32 s) = vm.sign(PUSHER_KEY, hash);
bytes memory sig = abi.encodePacked(r, s, v);

// 2. Creator establishes delegation
vm.prank(creator);
oracle.allowPushers(deadline, _arr(pusher), _sigs(sig));
assertEq(oracle.namespaceRemapping(pusher), creator);

// 3. Pusher revokes
vm.prank(pusher);
oracle.revokePusher();
assertEq(oracle.namespaceRemapping(pusher), address(0));

// 4. Creator replays the SAME signature — succeeds, delegation restored
vm.prank(creator);
oracle.allowPushers(deadline, _arr(pusher), _sigs(sig)); // no revert
assertEq(oracle.namespaceRemapping(pusher), creator);    // delegation live again

// 5. Attacker (holding pusher key) pushes manipulated price
uint56 tsMs = uint56((block.timestamp + 1) * 1000);
uint48 badPrice = _packRaw(9_000_000, 1, 1); // extreme price
vm.prank(pusher); // attacker controls this key
(bool ok,) = address(oracle).call(_wordAt(0, 0, badPrice, tsMs));
assertTrue(ok);

// 6. Price now reads as attacker-controlled value in creator's namespace
IOffchainOracle.OracleData memory data =
    oracle.getOracleData(oracle.feedIdOf(creator, 0, 0));
assertEq(data.price, U64x32.decode(uint32(badPrice >> 16)));
// AnchoredPriceProvider will use this as the reference mid for pool swaps
```

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L204-210)
```text
            bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
                keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
            );
            require(pusher == ECDSA.recover(hash, signatures[i]));

            namespaceRemapping[pusher] = msg.sender;
            emit PusherAuthorized(pusher, msg.sender);
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

**File:** smart-contracts-poc/contracts/oracles/compressed/OracleBase.sol (L124-126)
```text
    function _ensureDeadline(uint256 deadline) internal view virtual {
        require(block.timestamp <= deadline, DeadlineExceeded());
    }
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L258-295)
```text
    function _getBidAndAskPrice() internal returns (uint128, uint128) {
        (uint256 mid, uint256 spreadBps, , bool ok) = _readLeg(baseFeedId);
        if (!ok) return (0, type(uint128).max);

        bytes32 _quote = quoteFeedId;
        if (_quote != bytes32(0)) {
            (uint256 mid2, uint256 spreadBps2, , bool ok2) = _readLeg(_quote);
            if (!ok2 || mid2 == 0) return (0, type(uint128).max);
            // Synthetic ratio (8-decimal): mid1 / mid2. Relative uncertainties of a ratio add.
            mid = Math.mulDiv(mid, ORACLE_DECIMALS, mid2);
            spreadBps += spreadBps2;
        }

        return _computeBidAsk(mid, spreadBps);
    }

    /// @dev Reads one feed and runs its per-leg guards. ok=false (→ caller halts, fail closed) on:
    ///      stale reference, mid == 0, spreadBps == the off-hours/stall marker (spreadBps >= ORACLE_BPS), or a
    ///      priceGuard violation. Each leg is read through the attributed path independently.
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
