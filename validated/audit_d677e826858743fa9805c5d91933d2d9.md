### Title
`revokePusher()` Self-Revocation Is Ineffective Within Deadline Window Due to `allowPushers` Signature Replay — (File: `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

A pusher who calls `revokePusher()` to self-revoke their namespace delegation can have that delegation immediately re-established by the creator using the **original, already-consumed `allowPushers` signature**, as long as the deadline has not expired. No nonce, revocation flag, or per-delegation counter prevents this replay. The result is that `revokePusher()` provides no effective protection against continued bad-price pushes within the deadline window.

---

### Finding Description

`allowPushers` requires the pusher to sign a consent message committing them to a specific creator's namespace:

```solidity
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
namespaceRemapping[pusher] = msg.sender;
``` [1](#0-0) 

The only on-chain validity gate is `block.timestamp <= deadline`. There is no nonce, no per-pusher revocation counter, and no flag recording that the pusher has already revoked.

`revokePusher()` resets the mapping to zero:

```solidity
function revokePusher() external {
    address creator = namespaceRemapping[msg.sender];
    if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
    namespaceRemapping[msg.sender] = address(0);
    emit PusherRevoked(msg.sender, creator);
}
``` [2](#0-1) 

Because the signature is bound only to `(chainid, oracle, deadline, pusher, creator)` — not to any monotonic counter — the creator can immediately call `allowPushers` again with the **identical** signature and deadline, writing `namespaceRemapping[pusher] = creator` a second time. The revocation is silently undone.

The code's own comment acknowledges the risk but frames the deadline as the complete mitigation:

> *"the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."* [3](#0-2) 

The deadline prevents indefinite replay across time, but does **not** prevent replay within the deadline window — the exact window during which a compromised pusher is most dangerous.

The `fallback()` push path resolves the effective namespace from `namespaceRemapping`:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [4](#0-3) 

So after the creator replays the delegation, every subsequent push from the compromised pusher key lands in the creator's namespace, overwriting legitimate price data whenever the pushed timestamp is newer than the stored one.

---

### Impact Explanation

The corrupted slot value is read by `getOracleData`, which decodes price, spread, and timestamp and returns them to `AnchoredPriceProvider._readLeg` or `PriceProvider._getBidAndAskPrice`. Both providers apply staleness and price-guard checks, but a freshly pushed, in-range, non-stale bad price passes all guards and reaches `_computeBidAsk` / `_getBidAndAskPrice`, producing a corrupted bid/ask that the pool uses for swap settlement. [5](#0-4) [6](#0-5) 

The `AnchoredPriceProvider` band clamp limits how far outside the reference band the final quote can be, but a bad price that is within the band (e.g., a price shifted by the full `MAX_SPREAD_BPS` margin) still causes direct loss to pool LPs or traders.

---

### Likelihood Explanation

- The creator is a semi-trusted party who originally set up the delegation; they may have automated infrastructure that re-establishes delegations on any change to `namespaceRemapping`.
- Deadlines are chosen by the creator and can be set arbitrarily far in the future (the contract imposes no cap).
- A compromised pusher key is a realistic operational event; the inability to revoke is the critical gap.
- The replay requires no new cryptographic material — the creator already holds the original signature.

---

### Recommendation

Add a per-pusher revocation nonce to the signed digest and increment it on every `revokePusher` / `removePushers` call:

```solidity
mapping(address => uint256) public pusherNonce;

// In allowPushers:
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender, pusherNonce[pusher]))

// In revokePusher / removePushers:
pusherNonce[pusher]++;
namespaceRemapping[pusher] = address(0);
```

This makes every previously issued signature invalid after revocation, regardless of whether the deadline has elapsed.

---

### Proof of Concept

```
1. Pusher signs: sig = sign(keccak256(chainid, oracle, deadline=T+1day, pusher, creator))
2. Creator calls allowPushers(T+1day, [pusher], [sig])
   → namespaceRemapping[pusher] = creator  ✓

3. Pusher's key is compromised; pusher calls revokePusher()
   → namespaceRemapping[pusher] = address(0)  ✓ (revocation recorded)

4. Creator's automated system detects the mapping change and calls
   allowPushers(T+1day, [pusher], [sig])  ← SAME signature, SAME deadline
   → namespaceRemapping[pusher] = creator  ✗ (revocation silently undone)

5. Attacker (holding compromised pusher key) calls fallback() with a crafted
   slot word: price = manipulated_value, timestamp = block.timestamp (fresh)
   → Monotonicity check passes (fresh > stored)
   → Slot written to creator's namespace

6. AnchoredPriceProvider reads the corrupted price via price(feedId, pool)
   → Bad bid/ask reaches MetricOmmPool.swap()
   → Trader receives more token0/token1 than the true oracle price permits,
     draining LP assets.
``` [7](#0-6)

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L101-117)
```text
    function getOracleData(bytes32 feedId) public view override returns (OracleData memory data) {
        (address creator, uint8 slotIndex, uint8 positionIndex) = _unpackFeedId(feedId);

        SlotLayout memory _layout = _loadSlotLayout(_oracleSlot(creator, slotIndex));
        CompressedOracleData memory compressed = _selectCompressedData(_layout, positionIndex);

        if (compressed.s1 == 0xff && compressed.s0 == 0xff) {
            data.spread1 = BPS_BASE;
            data.spread0 = BPS_BASE;
            return data;
        }

        data.price = U64x32.decode(compressed.p);
        data.spread0 = _decodeCodebookIndex(compressed.s0);
        data.spread1 = _decodeCodebookIndex(compressed.s1);
        data.timestampMs = _layout.timestampMs;
    }
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L186-192)
```text
    /// @notice Delegates pusher wallets into the caller's namespace. The pusher's EIP-191
    ///         signature is REQUIRED — without it anyone could remap a foreign pusher
    ///         wallet into their own namespace and silently swallow its pushes. The
    ///         deadline is likewise required: the signed consent carries no timestamp of
    ///         its own, so an undated signature could re-establish a delegation AFTER the
    ///         pusher revoked it.
    function allowPushers(uint256 deadline, address[] calldata pushers, bytes[] memory signatures) external {
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L326-344)
```text
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
