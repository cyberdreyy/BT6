### Title
Revoked Pusher Can Replay Stale EIP-191 Authorization Signature to Re-Establish Delegation and Push Bad Prices Into Pools — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracleV1`'s `allowPushers` delegation path binds pusher consent to a deadline but carries **no nonce and no revocation counter**. Once a creator calls `revokePusher`, the on-chain mapping entry is cleared, but the pusher's original EIP-191 authorization signature (signed over `(creator, deadline)` or equivalent) remains cryptographically valid until its deadline expires. Anyone in possession of that signature — including the revoked pusher — can call `allowPushers` again with the identical bytes to silently re-establish the delegation. The re-authorized pusher then regains the ability to write arbitrary price data into the creator's namespace, which flows downstream through `getOracleData` → `PriceProvider` → pool `getBidAndAskPrice()` → swap execution.

---

### Finding Description

`CompressedOracleV1` implements a registrationless push model: a creator owns a namespace and may delegate write access to external pushers via `allowPushers`. Each pusher proves consent with an EIP-191 signature that includes a deadline. The oracle records the delegation in `namespaceRemapping[pusher] = creator`. [1](#0-0) 

When the creator later calls `revokePusher`, the mapping entry is deleted. However, the protocol's own documentation explicitly acknowledges the gap:

> *"the signed consent has no data timestamp, so an undated signature could re-establish a delegation after the pusher revoked it"* [2](#0-1) 

Because `allowPushers` validates only the EIP-191 signature and the deadline — with no nonce, no revocation epoch, and no on-chain record of previously consumed signatures — the revoked pusher (or any party who observed the original `allowPushers` calldata on-chain) can replay the identical call. The oracle re-inserts `namespaceRemapping[pusher] = creator`, fully restoring write access.

With write access restored, the pusher calls the oracle's `fallback()` with a crafted 32-byte slot word. The slot word encodes all four feed positions plus a shared timestamp: [3](#0-2) 

The only on-write guard is a monotonicity check (`timestampMs.isAfter(oldTimestampMs)`) and a future-drift cap. A pusher who knows the current stored timestamp simply supplies `currentTimestamp + 1 ms` to pass the monotonicity gate and write an arbitrary price into any of the four positions. [4](#0-3) 

The corrupted price is then decoded by `getOracleData` via `U64x32.decode(compressed.p)` and the codebook spread indexes, and returned to any `PriceProvider` that reads this feed: [5](#0-4) 

The `PriceProvider` / `PriceProviderL2` staleness check uses `refTime` (converted from the slot's millisecond timestamp). Because the pusher supplied a fresh timestamp, the staleness check passes, the price guard is the only remaining barrier, and if the guard is unset or wide, the bad bid/ask reaches the pool swap. [6](#0-5) 

---

### Impact Explanation

A re-authorized revoked pusher can write an extreme price (e.g., near-zero bid or near-infinite ask) into a feed. The pool's `getBidAndAskPrice()` returns this value, and `SwapMath` executes the swap at the manipulated quote. Traders receive more output than the true market price permits (swap conservation failure) or the pool receives less input than owed, directly draining LP principal. Repeated pushes within the same block (each incrementing the timestamp by 1 ms) can drain a pool across multiple swaps in a single transaction, matching the "multiple re-allocations continuously drain funds" pattern identified in the reference report.

---

### Likelihood Explanation

- The original `allowPushers` calldata is permanently visible on-chain; no off-chain secret is required.
- The revoked pusher is the most natural attacker — they already hold the signed consent bytes.
- The window is bounded only by the deadline embedded in the signature. Long-lived delegations (e.g., 1-year deadline) leave a large exploitation window after revocation.
- No flash-loan or complex setup is needed; a single transaction suffices.

---

### Recommendation

Add a **per-pusher revocation nonce** (or a global revocation epoch per creator) to the signed message. The oracle should store `revokedNonce[creator][pusher]` and increment it on every `revokePusher` call. The `allowPushers` signature must commit to the current nonce so that any previously issued signature becomes invalid immediately upon revocation, regardless of its deadline.

Alternatively, track consumed authorization signatures in a `usedAuthSigs` mapping (keyed by signature hash) and reject replays.

---

### Proof of Concept

```
1. Creator calls allowPushers([pusher], deadline=T+365days, sig=pusherSig).
   → namespaceRemapping[pusher] = creator.

2. Creator calls revokePusher(pusher).
   → namespaceRemapping[pusher] = address(0).

3. Attacker (or pusher) replays the original calldata:
   allowPushers([pusher], deadline=T+365days, sig=pusherSig).
   → Signature still valid (deadline not expired, no nonce check).
   → namespaceRemapping[pusher] = creator  ← delegation restored.

4. Pusher calls oracle.fallback() with a crafted slot word:
   - timestamp = storedTimestamp + 1 ms  (passes isAfter check)
   - price field = U64x32.encode(extremePrice)
   - spread indexes = valid codebook entries (not 0xFF sentinel)
   → Slot overwritten with bad price.

5. Pool calls PriceProvider.getBidAndAskPrice()
   → getOracleData(feedId) returns extremePrice with fresh timestamp.
   → Staleness check passes (timestamp is current).
   → Price guard passes (if unset or wide).
   → Pool executes swap at manipulated bid/ask → LP loss.
``` [7](#0-6) [8](#0-7)

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L29-29)
```text
    mapping(address => address) public namespaceRemapping;
```

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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L119-131)
```text
    function _loadSlotLayout(bytes32 slotIndex) internal view returns (SlotLayout memory _layout) {
        uint256 slotValue;
        assembly ("memory-safe") {
            slotValue := sload(slotIndex)
        }

        _layout.timestampMs = toTimeMs(slotValue >> 8 & X56);

        _layout.oracle0 = _decodeCompressedOracleData(uint48((slotValue >> 208) & X48));
        _layout.oracle1 = _decodeCompressedOracleData(uint48((slotValue >> 160) & X48));
        _layout.oracle2 = _decodeCompressedOracleData(uint48((slotValue >> 112) & X48));
        _layout.oracle3 = _decodeCompressedOracleData(uint48((slotValue >> 64) & X48));
    }
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

**File:** smart-contracts-poc/contracts/oracles/compressed/docs/en/slot-structure.md (L27-29)
```markdown
Delegation (`allowPushers`) requires each pusher's EIP-191 signature (and a deadline:
the signed consent has no data timestamp, so an undated signature could re-establish a
delegation after the pusher revoked it).
```

**File:** smart-contracts-poc/contracts/PriceProviderL2.sol (L208-217)
```text
    function _getBidAndAskPrice() internal returns (uint128, uint128) {
        // 1. Read via the unified price(feedId, pool) path, forwarding the pool (msg.sender).
        //    refTime is already in seconds.
        (uint256 mid, uint256 spread, , uint256 refTime) =
            IPricedOracle(address(offchainOracle)).price(offchainFeedId, msg.sender);

        // 2. Staleness check
        if (_isStale(refTime, block.timestamp, MAX_TIME_DELTA, FUTURE_TOLERANCE)) {
            return (0, type(uint128).max);
        }
```

**File:** smart-contracts-poc/contracts/interfaces/ICompressedOracleV1.sol (L39-41)
```text
    event PusherAuthorized(address indexed pusher, address indexed creator);
    event PusherRevoked(address indexed pusher, address indexed creator);
}
```
