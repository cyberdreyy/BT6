### Title
Missing `deadline` in `updateBySignature` allows pending signed oracle updates to be submitted with stale prices — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracleV1::updateBySignature` is a permissionless function: any caller can submit a creator-signed slot word to the oracle. The function's only temporal guards are (a) a future-timestamp cap (`revertIfAfterBlockTimeWithDrift`) and (b) a per-slot monotonicity check. There is no `deadline` parameter. A signed update that was broadcast but never mined can therefore be submitted arbitrarily late, writing a stale price into the oracle that downstream price providers then consume as fresh, causing bad-price execution in pool swaps.

---

### Finding Description

**The design intent vs. the deployed code**

The outer project documentation and the contract registry both describe a four-parameter `updateBySignature` that includes an explicit `deadline`:

- `smart-contracts-poc/docs/en/oracle-packet-structure.md` line 37: `updateBySignature(feedCreator, deadline, newSlotValue, signature)`, signed over `keccak256(abi.encode(chainid, oracleAddress, feedCreator, deadline, newSlotValue))`, with the required guard "deadline must be in the future (`DeadlineExceeded` otherwise)".
- `smart-contracts-poc/contract-registry/versions/registry.json` line 3601–3631: ABI entry `updateBySignature(address, uint256, uint256, bytes)` — four parameters.

The deployed `CompressedOracle.sol` has only three parameters and no deadline:

```solidity
// CompressedOracle.sol line 271
function updateBySignature(address feedCreator, uint256 newSlotValue, bytes calldata signature)
    external
    override
    returns (bool)
```

The signed digest is:

```solidity
// line 295-296
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), feedCreator, newSlotValue))
);
```

No deadline is included in the message and no `_ensureDeadline` call is made, even though `_ensureDeadline` exists in `OracleBase` (line 124) and is already called by `allowPushers` (line 193).

**Why the monotonicity check alone is insufficient**

The code comment at line 268–270 claims "replay is neutralized by the monotonicity check." The monotonicity check (`timestampMs.isAfter(oldTimestampMs)`) only rejects updates whose embedded timestamp is ≤ the currently stored timestamp. It does **not** prevent a pending signed update from being submitted when:

- Its embedded timestamp T is **newer** than the stored timestamp (monotonicity passes), but
- T is **older** than the current block time (the price is stale).

This is the exact window the external report describes: a transaction signed at time T with price P₁ sits in the mempool while the market moves to P₂. Because there is no deadline, the signed update remains valid indefinitely.

**Attack path**

1. Creator signs a slot word with timestamp T and price P₁ (current market price) and broadcasts it (e.g., via a public relay or low-gas submission).
2. The transaction is not mined. The market moves; price is now P₂ ≠ P₁.
3. No newer update has been pushed to this slot via `fallback()`, so the stored timestamp is still < T.
4. A MEV bot (or any actor) detects the pending signed update and submits it.
5. `updateBySignature` executes:
   - `revertIfAfterBlockTimeWithDrift`: T is in the past → passes.
   - Monotonicity: T > stored timestamp → passes.
   - Signature: valid → passes.
   - Storage write: oracle slot now holds price P₁ with timestamp T.
6. Price provider reads the oracle: `refTime = T / 1000` (seconds). If `block.timestamp − refTime < MAX_TIME_DELTA`, the staleness check passes.
7. Pool swap executes at stale price P₁ instead of current price P₂.

**`MAX_TIME_DELTA` is not a reliable backstop**

`PriceProvider.sol` line 87 allows `MAX_TIME_DELTA` up to 7 days. Even a conservatively configured value of 1 hour means a signed update from 59 minutes ago with a materially different price passes the staleness check and reaches the pool.

---

### Impact Explanation

A stale price written by a delayed `updateBySignature` call reaches `getBidAndAskPrice` → pool `swap`. The pool executes at the wrong bid/ask, causing traders to receive more or less than the oracle-anchored curve permits. This is a direct bad-price execution impact: user principal is lost to the price discrepancy, and the surplus is extractable by the MEV actor who timed the submission.

---

### Likelihood Explanation

`updateBySignature` is permissionless and the signed payload is typically broadcast publicly (off-chain relay, mempool). MEV bots routinely monitor the mempool for profitable pending transactions. The creator has no on-chain mechanism to cancel a broadcast signature; the only mitigation is to push a newer `fallback()` update before the stale one is mined, which requires the creator to be aware of the pending transaction. The scenario is realistic on any chain where gas spikes cause transaction delays.

---

### Recommendation

Add a `deadline` parameter to `updateBySignature`, include it in the signed digest, and call `_ensureDeadline(deadline)` before the monotonicity check — exactly as `allowPushers` already does:

```solidity
function updateBySignature(
    address feedCreator,
    uint256 deadline,          // ← add
    uint256 newSlotValue,
    bytes calldata signature
) external override returns (bool) {
    _ensureDeadline(deadline); // ← add (rejects if block.timestamp > deadline)

    // ... existing timestamp and monotonicity checks ...

    bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
        keccak256(abi.encode(block.chainid, address(this), feedCreator, deadline, newSlotValue))
        //                                                              ^^^^^^^^ bind deadline
    );
    require(feedCreator == ECDSA.recover(hash, signature));
    _writeStorage(key, bytes32(newSlotValue & ~uint256(0xff)));
    return true;
}
```

This matches the interface described in the outer documentation and the registry ABI, and closes the window for stale-price injection via delayed submission.

---

### Proof of Concept

```
State: CompressedOracleV1 deployed with MAX_TIME_DRIFT = 60 s.
       PriceProvider deployed with MAX_TIME_DELTA = 3600 s (1 hour).
       Feed slot for (creator, slotId=0) is empty (stored timestamp = 0).

T=0:   Creator signs newSlotValue encoding price=100_000_000 (1.00 USD), timestamp=T*1000.
       Signed digest: keccak256(abi.encode(chainid, oracle, creator, newSlotValue)).
       Transaction broadcast with low gas; not mined.

T=1800 (30 min later):
       Market price moves to 110_000_000 (1.10 USD).
       Creator has not pushed a newer fallback() update.

T=1800: MEV bot submits oracle.updateBySignature(creator, newSlotValue, sig).
        - revertIfAfterBlockTimeWithDrift: 0 <= 1800+60 → passes.
        - isAfter(T=0ms, stored=0ms): 0 > 0 is false... 

Wait, let me reconsider. T=0 means timestamp in the slot word is 0*1000 = 0 ms. The stored timestamp is also 0. So isAfter(0, 0) = false, and the update returns false.

Let me redo: T=1000 (initial time), creator signs at T=1000 with timestamp=1000*1000=1_000_000 ms.

T=1000: Creator signs newSlotValue with timestamp=1_000_000 ms, price=100_000_000.
        Transaction broadcast, not mined.

T=2800 (30 min later, T=1000+1800=2800):
        Market price = 110_000_000.
        No newer fallback() push. Stored timestamp = 0 (never pushed).

T=2800: MEV bot submits oracle.updateBySignature(creator, newSlotValue, sig).
        - revertIfAfterBlockTimeWithDrift: 1_000_000/1000=1000 <= 2800+60 → passes.
        - isAfter(1_000_000 ms, 0 ms): true → proceeds.
        - Signature valid → passes.
        - Storage write: price=100_000_000, timestamp=1_000_000 ms.

T=2800: Pool swap triggers provider.getBidAndAskPrice().
        oracle.price(feedId, pool) returns refTime = 1_000_000/1000 = 1000 s.
        _isStale(1000, 2800, 3600): (2800-1000)=1800 < 3600 → NOT stale.
        Pool executes swap at bid/ask derived from price=100_000_000 (stale).
        Actual market price is 110_000_000 → user receives ~9% less than fair value.
```

The stale price passes all on-chain guards and reaches the pool swap because no deadline was enforced on the signed update. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L268-302)
```text
    /// @notice Single-slot update authorized by the creator's signature. The signed slot
    ///         word carries its own 56-bit timestamp, so replay is neutralized by the
    ///         monotonicity check below — no deadline is needed.
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
```

**File:** smart-contracts-poc/contracts/oracles/compressed/OracleBase.sol (L124-126)
```text
    function _ensureDeadline(uint256 deadline) internal view virtual {
        require(block.timestamp <= deadline, DeadlineExceeded());
    }
```

**File:** smart-contracts-poc/docs/en/oracle-packet-structure.md (L35-47)
```markdown
## Signature Payload (Off-chain → On-chain)

`updateBySignature(feedCreator, deadline, newSlotValue, signature)` expects `newSlotValue` to be a single slot word (same layout), signed by `feedCreator` over:

```text
keccak256(abi.encode(chainid, oracleAddress, feedCreator, deadline, newSlotValue))
```

## Required Guards

- `deadline` must be in the future (`DeadlineExceeded` otherwise).
- `timestamp` must not be in the future (`FutureTimestamp` otherwise).
- `timestamp` must be strictly increasing per slot (older updates are ignored).
```

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L87-88)
```text
        if (_maxTimeDelta == 0 || _maxTimeDelta > 7 days) revert MaxTimeDeltaOutOfBounds();
        MAX_TIME_DELTA = _maxTimeDelta;
```

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L121-133)
```text

    // ── Staleness ───────────────────────────────────────────────────────

    /// @dev Pure staleness check (L1). Any future refTime is stale.
    function _isStale(
        uint256 refTime,
        uint256 nowTs,
        uint256 maxDelta
    ) internal pure returns (bool) {
        if (refTime == 0) return true;
        if (refTime > nowTs) return true;
        return (nowTs - refTime) > maxDelta;
    }
```
