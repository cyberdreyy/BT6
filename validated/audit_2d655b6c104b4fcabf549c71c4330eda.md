### Title
`allowContractPushers` Checks `isPusher` Only Once at Registration — Revoked Contract Pusher Can Still Overwrite Creator Namespace Prices - (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracleV1.allowContractPushers` performs a one-time `isPusher(creator)` staticcall to prove consent, then permanently writes `namespaceRemapping[pusher] = creator`. The `fallback()` push path never re-checks `isPusher`. If the contract pusher later changes its `isPusher` return value to `false` (internally revoking consent), the oracle's `namespaceRemapping` entry is not cleared, and the pusher can continue writing arbitrary prices into the creator's namespace indefinitely.

---

### Finding Description

The NatDoc comment on `allowContractPushers` explicitly states:

> *"consent is proven by a LIVE `isPusher(creator)` staticcall instead of a signature, so there is nothing to replay and **no deadline is needed**."*

This is the stated justification for omitting the deadline that the EOA path (`allowPushers`) requires. The EOA path's NatDoc explains why a deadline is mandatory:

> *"an undated signature could re-establish a delegation AFTER the pusher revoked it."*

The contract-pusher path was designed to avoid that problem via an ongoing live check. But the check is **only performed once**, at registration time: [1](#0-0) 

After `namespaceRemapping[pusher] = msg.sender` is written, the `fallback()` push path resolves the namespace purely from that mapping — `isPusher` is never consulted again: [2](#0-1) 

If the pusher contract later changes its `isPusher` implementation to return `false` (e.g., after an upgrade, ownership transfer, or internal revocation), the oracle has no mechanism to detect this. The stale `namespaceRemapping` entry remains, and the pusher continues to write into the creator's namespace on every `fallback()` call.

The creator's only recourse is to explicitly call `removePushers`, but the design comment implies that changing `isPusher` is the revocation mechanism — a creator who relies on that assumption is left with an active, unrevoked delegation they believe is cancelled.

---

### Impact Explanation

Prices pushed into the creator's namespace are consumed by `AnchoredPriceProvider` / `PriceProvider` and ultimately reach `MetricOmmPool.swap()` via `getBidAndAskPrice`. A contract pusher that has internally revoked consent but retains an active `namespaceRemapping` entry can push:

- Arbitrarily high or low `U64x32`-encoded prices
- Sentinel spread indexes (`s0 == 0xff, s1 == 0xff`) that cause `getOracleData` to return `spread = BPS_BASE` (100%), making every swap maximally wide
- Stale timestamps that pass the monotonicity gate if they are newer than the last stored value [3](#0-2) 

Any of these corrupt the bid/ask quote consumed by the pool, satisfying the **bad-price execution** impact gate: a trader receives a quote at a price the oracle/bin curve does not permit, or the pool fails to receive owed input.

---

### Likelihood Explanation

The trigger is a contract pusher whose `isPusher` return value changes after registration — a realistic scenario for:

- Upgradeable pusher contracts that rotate their authorized creator set
- Pusher contracts that implement access-control logic and later revoke a creator
- Compromised pusher contracts whose owner changes the internal allowlist

The creator has no on-chain signal that the pusher's consent has changed; the oracle emits no event and performs no re-check. The creator must independently discover the situation and call `removePushers` before any bad push lands.

---

### Recommendation

Re-check `isPusher(creator)` on every `fallback()` push for contract pushers, or remove the claim that the live check substitutes for a deadline. Concretely:

1. In `fallback()`, after resolving `creator = namespaceRemapping[msg.sender]`, if `msg.sender` has code, perform a `staticcall` to `isPusher(creator)` and revert/skip if it returns `false`.
2. Alternatively, align the contract-pusher path with the EOA path by requiring an explicit deadline and re-registration after any consent change, removing the misleading "live check" justification.

---

### Proof of Concept

```
1. Deploy a MockPusherSelective(creator) that returns isPusher(creator) == true.
2. creator calls allowContractPushers([pusherContract]).
   → namespaceRemapping[pusherContract] = creator  ✓
3. pusherContract.setAllowedCreator(address(0))  // isPusher now returns false for creator
4. vm.prank(address(pusherContract));
   oracle.call(wordAt(slotId=0, pos=0, price=MAX_UINT32, s0=0, s1=0, ts=now))
   → fallback() reads namespaceRemapping[pusherContract] == creator (stale)
   → writes MAX_UINT32 price into creator's slot 0, position 0
5. oracle.getOracleData(oracle.feedIdOf(creator, 0, 0)).price == U64x32.decode(MAX_UINT32)
   → corrupted price is now live in the creator's namespace
   → AnchoredPriceProvider / pool swap consumes this bad quote
``` [4](#0-3) [5](#0-4)

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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L214-234)
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
