### Title
Attacker Can Selectively Submit Withheld Signed Oracle Updates to Exploit Price Gaps — (File: `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracleV1.updateBySignature()` is a permissionless function that accepts any valid creator-signed slot update. Because signed updates are published off-chain for public submission, an attacker can collect two valid signed updates `(T1, P1)` and `(T2, P2)` within the staleness window, withhold them, and submit them in a chosen order inside a single atomic MEV bundle — executing trades between submissions to extract the price gap from LP reserves.

---

### Finding Description

`updateBySignature` enforces only two guards before writing to storage:

1. **Future-drift check** (line 285): `timestampMs.revertIfAfterBlockTimeWithDrift(MAX_TIME_DRIFT)` — rejects timestamps too far in the future.
2. **Monotonicity check** (lines 290–293): `timestampMs.isAfter(oldTimestampMs)` — rejects timestamps not strictly greater than the stored one. [1](#0-0) 

Neither guard prevents an attacker from **selectively ordering** which signed updates to submit. The oracle operator continuously signs and publishes slot words off-chain (this is the explicit design of the `updateBySignature` path — "allows anyone to submit an update signed by the creator"). An attacker who monitors the off-chain feed can collect two updates `(T1, P1)` and `(T2, P2)` where `T1 < T2`, withhold both while the on-chain oracle is still at `T0 < T1`, and then submit them in sequence within a single atomic transaction.

Critically, `CompressedOracleV1.price()` is a **`view` function** that completely ignores the `pool` argument: [2](#0-1) 

Unlike the providers `OracleBase.price()`, there is no `inSwap()` binding, no `registeredPool` check, and no blacklist check. Any pool whose `PriceProvider` points to a `CompressedOracleV1` feed is exposed. The `PriceProvider` staleness check (`MAX_TIME_DELTA`, typically 24 hours) is the only downstream guard, and it only rejects prices older than that window — it does not prevent the selective-ordering attack as long as both `T1` and `T2` fall within the window. [3](#0-2) 

The `fallback()` direct-push path has the same monotonicity-only guard and is callable by any authorized pusher, but `updateBySignature` is the wider surface because it is callable by **anyone** holding a valid creator signature. [4](#0-3) 

---

### Impact Explanation

The attacker executes trades at a known-favorable price `P1` and closes at a known-favorable price `P2`, extracting the gap from the pool's LP reserves. This is a **direct loss of LP principal**: the pool pays out more than the oracle/bin curve permits, or receives less input than owed. The `CompressedOracleV1.price()` being a `view` function also means no `PriceRead` event is emitted, making the attack harder to detect on-chain.

---

### Likelihood Explanation

- `updateBySignature` is explicitly designed for public submission of creator-signed updates; signed slot words are expected to be publicly available off-chain.
- MEV infrastructure (Flashbots bundles, block builders) is widely available on all target chains.
- No special permissions are required — only access to two valid signed updates within `MAX_TIME_DELTA`.
- The 24-hour staleness window provides a large collection window for finding a favorable price pair.
- The `CompressedOracleV1` is open and permissionless by design, removing the abuse-protection layer that the providers `OracleBase` provides. [5](#0-4) 

---

### Recommendation

1. **Add a nonce or sequence number** to the signed message so each signed update can only be submitted once and in the intended order. The current signed payload `keccak256(abi.encode(block.chainid, address(this), feedCreator, newSlotValue))` binds only to the slot value and chain — not to a submission sequence.
2. **Alternatively**, restrict `updateBySignature` to a whitelist of authorized submitters (analogous to the executor/sequencer mitigation in the external report).
3. **Require pools using `CompressedOracleV1`** to use an `AnchoredPriceProvider` that clamps quotes to a trusted reference band, limiting the exploitable gap even if selective ordering occurs.

---

### Proof of Concept

```
Setup:
  - Oracle creator signs and publishes two slot words off-chain:
      slotWord1: timestamp=T1, price=P1 (e.g. $100), slotId=0
      slotWord2: timestamp=T2>T1, price=P2 (e.g. $110), slotId=0
  - On-chain oracle is at timestamp T0 < T1 (neither update submitted yet)
  - Pool uses CompressedOracleV1 via a non-anchored PriceProvider

Attack (single atomic MEV bundle):
  1. AttackContract.execute():
     a. Call updateBySignature(creator, slotWord1, sig1)
        → oracle slot now stores P1=$100, timestamp=T1
     b. Call pool.swap(tokenA→tokenB) at oracle price P1=$100
        → attacker buys tokenB cheaply
     c. Call updateBySignature(creator, slotWord2, sig2)
        → oracle slot now stores P2=$110, timestamp=T2 (monotonicity satisfied)
     d. Call pool.swap(tokenB→tokenA) at

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L14-23)
```text
/// @notice Registrationless compressed oracle: a feed's LOCATION is its identity.
///         There is no feed registry — the feedId packs (creator, chainid, slotIndex,
///         positionIndex) and every read decodes its coordinates straight from the id:
///
///           feedId = creator << 96 | block.chainid << 16 | slotIndex << 8 | positionIndex
///
///         A creator owns 256 slots × 4 positions in their own namespace and simply
///         pushes into them (directly, or through pushers delegated via `allowPushers`).
///         A never-pushed position reads as price 0 / timestamp 0, which every consumer
///         already rejects as stale — no seeding or creation step is needed.
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L163-169)
```text
    function price(bytes32 feedId, address /* pool */)
        external
        view
        returns (uint256 mid, uint256 spread, uint16 spread1, uint256 refTime)
    {
        return _price(feedId);
    }
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L268-303)
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
