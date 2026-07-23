### Title
Stale Pusher Delegations Survive `stateGuard` Transfer, Letting Old Creator Feed Bad Prices — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracleV1` separates two orthogonal access-control planes: the **`stateGuard`** role (controls price-guard bounds and guard succession) and the **`namespaceRemapping`** pusher-delegation list (controls who may push raw price data into a creator's slots). When a creator transfers the `stateGuard` role to a new party, the pusher-delegation list is never cleared and the new guard has no mechanism to revoke it. The old creator and every pusher they previously authorized retain unconditional write access to the creator's price slots, which pools consume directly.

---

### Finding Description

`allowPushers` writes `namespaceRemapping[pusher] = msg.sender` (the creator). [1](#0-0) 

`removePushers` enforces `namespaceRemapping[pusher] == msg.sender`, so only the original creator can revoke a delegation. [2](#0-1) 

The `stateGuard` succession path in `OracleBase` (compressed) is a clean two-step handoff: [3](#0-2) 

After `acceptStateGuardRole` completes, the new guard (`Carol`) holds `stateGuard[feedId]` and can call `setPriceGuard`. However:

1. **`namespaceRemapping` is not cleared.** Every pusher Alice previously authorized still maps to Alice's namespace.
2. **The new guard cannot call `removePushers`.** That function reverts with `InvalidManager` unless `namespaceRemapping[pusher] == msg.sender`; Carol's address never appears there.
3. **The mapping is not enumerable per creator.** There is no on-chain way for Carol to discover which pushers Alice delegated; the flat `namespaceRemapping` mapping cannot be iterated by creator.
4. **Alice herself retains direct push access.** The `fallback` resolves the namespace as `namespaceRemapping[msg.sender]`, falling back to `msg.sender`; Alice's own address always resolves to her own namespace regardless of the `stateGuard` transfer. [4](#0-3) 

The only escape valve is `revokePusher`, which is self-service — the pusher must voluntarily revoke themselves. [5](#0-4) 

Furthermore, the compressed oracle's read path (`getOracleData` → `_price`) does not enforce the `priceGuard` bounds stored in `OracleBase`, so even if Carol sets a tight price guard, it provides no protection against bad pushes in this path. [6](#0-5) 

---

### Impact Explanation

A malicious or compromised old creator (or any pusher they previously authorized) can push arbitrary prices — inverted bid/ask, stale timestamps just above the stored monotonicity watermark, or extreme values — into the creator's namespace slots after the `stateGuard` has been transferred to a new party. Pools that have registered against those `feedId`s will consume the corrupted price on the next swap, causing:

- **Bad-price execution**: traders receive more output than the oracle curve permits, or the pool receives less input than owed.
- **Swap conservation failure**: the pool's token balance no longer covers LP claims or owed fees.

The new `stateGuard` has no on-chain remedy short of convincing every old pusher to self-revoke.

---

### Likelihood Explanation

The trigger requires: (a) a creator who previously delegated at least one pusher, and (b) a subsequent `stateGuard` transfer to a new party. Both operations are normal, documented protocol flows. The `stateGuard` transfer is explicitly designed for feed-management handoffs (e.g., selling feed rights). The new guard has no tooling to audit inherited delegations, making silent retention of old pusher access the default outcome of every such transfer. Likelihood is **medium**: the preconditions are realistic and the attack requires no special privilege beyond the old creator's retained namespace write access.

---

### Recommendation

1. **Bind pusher delegations to the `stateGuard`, not the creator address.** Store `namespaceRemapping[pusher] = stateGuard` and resolve the effective guard at push time, so that a guard transfer automatically invalidates all prior delegations.
2. **Alternatively, add a per-creator delegation nonce.** Increment a `uint256 delegationEpoch[creator]` on every `stateGuard` transfer and include the epoch in the `namespaceRemapping` value. Pushers whose stored epoch does not match the current epoch are silently treated as unregistered.
3. **Expose an enumerable pusher list per creator** so that a new `stateGuard` can audit and revoke inherited delegations on-chain.
4. **Enforce `priceGuard` bounds inside `getOracleData`** so that even if a stale pusher writes an extreme value, the read path rejects it before it reaches a pool.

---

### Proof of Concept

```
// Setup
address alice  = <creator>;
address bob    = <pusher alice previously authorized>;
address carol  = <new stateGuard>;

// 1. Alice delegates Bob
//    namespaceRemapping[bob] = alice
oracle.allowPushers(deadline, [bob], [bobSig]);

// 2. Alice transfers stateGuard to Carol (two-step)
oracle.setPendingStateGuardRole(feedId, carol);   // called by alice
oracle.acceptStateGuardRole(feedId);              // called by carol
// stateGuard[feedId] == carol

// 3. Carol cannot revoke Bob
oracle.removePushers([bob]);   // reverts: InvalidManager(carol)
                               // because namespaceRemapping[bob] == alice != carol

// 4. Bob pushes a manipulated price into alice's namespace
//    word encodes: extreme price, valid-but-stale timestamp > stored watermark
bytes memory payload = abi.encodePacked(manipulatedWord);
(bool ok,) = address(oracle).call(payload);
// ok == true; alice's slot now holds the bad price

// 5. Pool swap reads the corrupted price
//    feedId = feedIdOf(alice, slotIndex, positionIndex)
//    pool.swap(...) → oracle.price(feedId, pool) → bad mid/spread returned
//    trader receives excess output; pool balance falls below LP claims
```

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

**File:** smart-contracts-poc/contracts/oracles/compressed/OracleBase.sol (L60-79)
```text
    function setPendingStateGuardRole(bytes32 feedId, address newGuard) external checkRole(feedId) {
        pendingStateGuard[feedId] = newGuard;

        emit StateGuardPending(feedId, newGuard);
    }

    function purgePendingStateGuardRole(bytes32 feedId) external checkRole(feedId) {
        delete pendingStateGuard[feedId];

        emit PendingStateGuardDeleted(feedId);
    }

    function acceptStateGuardRole(bytes32 feedId) external {
        require(pendingStateGuard[feedId] == msg.sender, InvalidGuard(msg.sender));

        delete pendingStateGuard[feedId];
        stateGuard[feedId] = msg.sender;

        emit StateGuardUpdated(feedId, msg.sender);
    }
```
