### Title
Stale Contract-Pusher Delegation Persists After `isPusher()` Revocation, Allowing Unauthorized Price Writes Into Creator Namespace — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowContractPushers()` verifies consent via a **one-time** live `isPusher(creator)` staticcall at delegation time and then permanently writes `namespaceRemapping[pusher] = msg.sender`. If the contract pusher later changes its `isPusher()` response to `false` (revoking consent at the application level), the `namespaceRemapping` entry is **not cleared**. The `fallback()` push path reads only `namespaceRemapping[msg.sender]` and never re-checks `isPusher()`, so the now-unauthorized contract pusher retains full write authority over the creator's namespace and can push arbitrary prices into it.

---

### Finding Description

`allowContractPushers()` is designed so that a contract pusher proves consent via a live `isPusher(creator)` call rather than an EIP-191 signature with a deadline. The NatSpec comment explicitly states: *"consent is proven by a LIVE `isPusher(creator)` staticcall instead of a signature, so there is nothing to replay and no deadline is needed."* [1](#0-0) 

However, the live check is performed **only once** at delegation time. After `namespaceRemapping[pusher] = msg.sender` is written, the `fallback()` push path resolves the namespace purely from that mapping: [2](#0-1) 

There is no re-check of `isPusher()` on every push. If the contract pusher subsequently changes its `isPusher()` to return `false` — signalling that it no longer consents to write on behalf of the creator — the stale `namespaceRemapping` entry persists. The contract pusher can continue calling `fallback()` and overwriting any slot in the creator's namespace with arbitrary price data, as long as the timestamp is monotonically newer.

The creator's only remedy is to explicitly call `removePushers()`: [3](#0-2) 

A creator who manages authorization entirely through `isPusher()` (the mechanism the contract was designed to trust) will not know that a separate on-chain `removePushers()` call is also required. This is the exact structural gap: the live check that was supposed to replace the deadline/signature replay protection only fires once, leaving a permanently open write channel.

---

### Impact Explanation

Once the stale delegation is in place, the contract pusher calls `fallback()` with a crafted 32-byte slot word containing an arbitrary price, spread indices, and a fresh timestamp. The `fallback()` path resolves `namespaceRemapping[pusherContract] == creator`, computes `key = creator_namespace | slotId`, passes the monotonicity check (newer timestamp), and writes the bad price into the creator's storage slot. [4](#0-3) 

The `price()` and `getOracleData()` read paths then decode and return this attacker-controlled value: [5](#0-4) 

Any `AnchoredPriceProvider` or `ProtectedPriceProvider` bound to this feed's `feedId` will consume the corrupted mid price and spread in `_readLeg()` / `_getBidAndAskPrice()`, producing a bad bid/ask that reaches live pool swaps. The attacker can set spread indices to valid codebook values (not the `0xff` sentinel) so the stall guard does not fire, and set a price within any configured `priceGuard` bounds to bypass that check too. [6](#0-5) 

**Impact: Medium** — direct bad-price execution reaching live pool swaps; traders receive quotes derived from an attacker-controlled oracle value.

---

### Likelihood Explanation

The `allowContractPushers()` NatSpec explicitly frames the live `isPusher()` call as the complete authorization mechanism ("no deadline is needed"). A creator who builds a contract pusher with a revocable `isPusher()` flag will naturally expect that flipping the flag to `false` revokes write authority. The need to also call `removePushers()` is not documented in the NatSpec or the slot-structure docs. This is a realistic operational mistake for any creator managing multiple pushers through a contract-level access-control system.

**Likelihood: Medium.**

---

### Recommendation

Re-check `isPusher(creator)` inside the `fallback()` push path for every contract pusher (i.e., when `namespaceRemapping[msg.sender] != address(0)` and `msg.sender` has code). If the live check returns `false`, either skip the write or revert and clear the stale mapping. Alternatively, document prominently — in both `allowContractPushers()` NatSpec and the slot-structure docs — that changing `isPusher()` alone does **not** revoke delegation and that `removePushers()` must be called explicitly.

---

### Proof of Concept

1. `creator` calls `allowContractPushers([pusherContract])`. At this moment `pusherContract.isPusher(creator)` returns `true`. The oracle writes `namespaceRemapping[pusherContract] = creator`.
2. `creator` updates `pusherContract` so that `isPusher(creator)` now returns `false`, believing write authority is revoked.
3. `creator` does **not** call `removePushers([pusherContract])`.
4. `pusherContract` calls `oracle.fallback()` with a crafted slot word: valid codebook spread indices (e.g., `s0=4, s1=2`), an attacker-chosen price (e.g., `p = 9_000_000` in U64x32 encoding), and a timestamp one millisecond newer than the current stored value.
5. `fallback()` resolves `namespaceRemapping[pusherContract] == creator`, passes the monotonicity check, and writes the bad price into `creator`'s slot.
6. `oracle.price(feedId, pool)` returns the attacker-controlled mid price and spread.
7. `AnchoredPriceProvider._readLeg()` consumes the value, passes staleness and spread guards (since the timestamp is fresh and spread is below `ORACLE_BPS`), and produces a corrupted bid/ask that the pool uses to execute swaps. [7](#0-6) [2](#0-1)

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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L214-233)
```text
    /// @notice Contract-pusher variant: consent is proven by a LIVE `isPusher(creator)`
    ///         staticcall instead of a signature, so there is nothing to replay and no
    ///         deadline is needed.
    function allowContractPushers(address[] calldata pushers) external {
        uint256 l = pushers.length;
        for (uint256 i; i < l; i++) {
            address pusher = pushers[i];

            if (pusher == msg.sender) {
                revert NoSelfRemapping();
            }

            (bool ok, bytes memory res) = pusher.staticcall(abi.encodeWithSignature("isPusher(address)", msg.sender));
            require(ok);
            bool allowed = abi.decode(res, (bool));
            require(allowed);

            namespaceRemapping[pusher] = msg.sender;
            emit PusherAuthorized(pusher, msg.sender);
        }
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L245-260)
```text
    function removePushers(address[] calldata pushers) external {
        uint256 l = pushers.length;
        for (uint256 i; i < l; i++) {
            address pusher = pushers[i];
            if (pusher == msg.sender) {
                revert NoSelfRemapping();
            }

            if (namespaceRemapping[pusher] == msg.sender) {
                namespaceRemapping[pusher] = address(0);
                emit PusherRevoked(pusher, msg.sender);
            } else {
                revert InvalidManager(msg.sender);
            }
        }
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
