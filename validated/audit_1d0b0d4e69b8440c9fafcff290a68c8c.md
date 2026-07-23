### Title
Delegated Pusher Can Front-Run `removePushers()` to Inject Persistent Bad Prices into Creator Namespace — (`File: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

A delegated pusher who observes a creator's `removePushers()` transaction in the mempool can front-run it by calling the `fallback()` push path with manipulated price data. Because namespace resolution and the write-time monotonicity check occur at push time with no write-time price validation, the bad price is committed to the creator's namespace before revocation takes effect. After `removePushers` executes, the pusher is detached, but the corrupted slot value persists in storage and is served to every pool consuming that feed until a legitimate push with a newer timestamp overwrites it.

---

### Finding Description

`CompressedOracleV1` allows a creator to delegate write access to their namespace to third-party EOA pushers via `allowPushers()`. The creator can revoke a pusher at any time by calling `removePushers()`.

The `fallback()` push path resolves the target namespace at call time:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [1](#0-0) 

It then writes the slot if the embedded timestamp is newer than the stored one, with no price-range validation at write time:

```solidity
bool newer = timestampMs.isAfter(oldTimestampMs);
if (!newer) continue;
_writeStorage(key, bytes32(bytes32(word & ~uint256(0xff))));
``` [2](#0-1) 

`removePushers()` only clears the mapping entry:

```solidity
if (namespaceRemapping[pusher] == msg.sender) {
    namespaceRemapping[pusher] = address(0);
    emit PusherRevoked(pusher, msg.sender);
}
``` [3](#0-2) 

It does not invalidate or roll back any slot data the pusher wrote before revocation. A pusher who monitors the mempool and sees a `removePushers` transaction can front-run it with a `fallback()` call carrying an extreme or manipulated price and a current timestamp. The write succeeds because the pusher is still mapped at that block. After `removePushers` lands, the pusher is detached, but the corrupted slot value remains in storage with a fresh timestamp, blocking any older legitimate update from overwriting it.

The `priceGuard` is not checked at write time in either `fallback()` or `updateBySignature()`. It is only evaluated later in `PriceProvider._getBidAndAskPrice()`:

```solidity
(uint128 guardMin, uint128 guardMax) = offchainOracle.priceGuard(offchainFeedId);
guardMax = guardMax == 0 ? type(uint128).max : guardMax;
if (mid < guardMin || mid > guardMax) {
    return (0, type(uint128).max);
}
``` [4](#0-3) 

If no price guard is configured (the default — `{min: 0, max: 0}` is treated as unlimited), the manipulated price passes through to pool swaps. If a price guard is configured, the pool swap stalls (`FeedStalled`), causing a DoS of pool functionality.

---

### Impact Explanation

- **No price guard set (default)**: The manipulated price is returned by `CompressedOracle.price()` and consumed by `PriceProvider.getBidAndAskPrice()`. Pools execute swaps at the attacker-controlled bid/ask, causing direct loss of trader principal or LP assets.
- **Price guard set**: The out-of-range price causes `PriceProvider` to return `(0, type(uint128).max)`, which the pool treats as a stall. All swaps against that pool are blocked until the creator pushes a corrective update with a newer timestamp — a broken core pool functionality impact.

In both cases the corrupted slot persists until a legitimate push with a strictly newer timestamp overwrites it. If the creator has no other active pusher, this window can be extended indefinitely.

---

### Likelihood Explanation

- The pusher was explicitly delegated by the creator via `allowPushers()` (requiring the pusher's EIP-191 signature), making them a valid semi-trusted actor, not a privileged admin.
- The creator revokes a pusher precisely because the pusher has become untrustworthy. At that moment the pusher has both motive and capability to front-run.
- The `fallback()` push path is permissionless for any currently-mapped pusher; no additional setup is required to execute the attack.
- Mempool monitoring is standard practice for MEV bots and sophisticated actors.

---

### Recommendation

1. **Write-time price guard enforcement**: Check `priceGuard[feedId]` inside `fallback()` and `updateBySignature()` before committing a slot write. Reject any price outside the configured bounds at write time, not only at read time.
2. **Revocation timelock / two-phase removal**: Introduce a pending-revocation state (analogous to `pendingStateGuard`) that prevents a pusher from writing to the namespace once a removal has been announced, even before it is finalized.
3. **Slot invalidation on revocation**: When `removePushers` executes, zero out or sentinel-mark the slots last written by the revoked pusher so downstream readers immediately see a stale/invalid feed rather than the attacker's data.

---

### Proof of Concept

```
1. Creator calls allowPushers(deadline, [pusher], [sig])
   → namespaceRemapping[pusher] = creator

2. Pusher goes rogue. Creator broadcasts:
   removePushers([pusher])

3. Pusher sees the tx in the mempool. Before it lands, pusher broadcasts
   with higher gas:
   oracle.call(word)   // fallback(), word encodes price=1 (or max), ts=now

   Inside fallback():
     creator = namespaceRemapping[pusher]  // still = creator (not yet revoked)
     timestampMs > stored → write accepted
     slot[creator][slotId] = manipulated_price | current_ts

4. removePushers lands:
   namespaceRemapping[pusher] = address(0)

5. Any pool calling PriceProvider.getBidAndAskPrice()
   → oracle.price(feedId, pool)
   → getOracleData(feedId) returns manipulated price with fresh timestamp
   → if no priceGuard: bad bid/ask reaches swap → trader loss
   → if priceGuard set: FeedStalled → pool DoS

6. State persists until creator pushes a slot word with ts > attacker's ts.
   If creator has no other pusher, the window is unbounded.
``` [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

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

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L191-231)
```text
    function _getBidAndAskPrice() internal returns (uint128, uint128) {
        // 1. Read via the unified price(feedId, pool) path, forwarding the pool (msg.sender).
        //    refTime is already in seconds.
        (uint256 mid, uint256 spread, , uint256 refTime) =
            IPricedOracle(address(offchainOracle)).price(offchainFeedId, msg.sender);

        // 2. Staleness check
        if (_isStale(refTime, block.timestamp, MAX_TIME_DELTA)) {
            return (0, type(uint128).max);
        }

        // 3. Basic validity — price must be positive, spread must not be stalled marker
        if (mid == 0 || spread >= ORACLE_BPS) {
            return (0, type(uint128).max);
        }

        // 4. Price guard check (moved from oracle)
        (uint128 guardMin, uint128 guardMax) = offchainOracle.priceGuard(offchainFeedId);
        guardMax = guardMax == 0 ? type(uint128).max : guardMax;
        if (mid < guardMin || mid > guardMax) {
            return (0, type(uint128).max);
        }

        // 5. Compute bid/ask from mid + confidence-adjusted spread
        //    confidenceParam multiplies oracle spread; 0 means no spread
        uint256 adjustedSpread = spread * confidenceParam;
        (uint256 bid, uint256 ask) = _getBidAskFrom(mid, adjustedSpread);

        // 6. Apply marginStep adjustment
        (uint256 bidOut, bool bidOk) = _applyBidAdjustments(bid);
        if (!bidOk || bidOut > type(uint128).max) return (0, type(uint128).max);

        (uint256 askOut, bool askOk) = _applyAskAdjustments(ask);
        if (!askOk || askOut > type(uint128).max) return (0, type(uint128).max);

        // 7. Hard invariant: bid must be strictly less than ask.
        //    Can be violated when marginStep < 0 and confidence is too small.
        if (bidOut >= askOut) return (0, type(uint128).max);

        return (uint128(bidOut), uint128(askOut));
    }
```
