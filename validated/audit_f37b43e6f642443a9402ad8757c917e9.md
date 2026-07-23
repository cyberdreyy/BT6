### Title
Pusher Can Set Oracle Price to Any Arbitrary Value Without On-Chain Bounds Checking — (File: `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

The `CompressedOracleV1` fallback push path accepts any price value from an authorized pusher with only a timestamp monotonicity check. No price magnitude validation is performed at the oracle layer. The `priceGuard` mechanism exists only at the price-provider layer and is **not enforced by default** (defaults to `{min: 0, max: 0}`, which the provider converts to `{min: 0, max: type(uint128).max}`). A compromised or malicious pusher — a semi-trusted role analogous to the `SwellLib.BOT` in the swETH report — can push an arbitrarily large or small price that flows unchecked into pool bid/ask computation, causing bad-price execution.

---

### Finding Description

The `fallback()` function in `CompressedOracleV1` is the primary push path. It processes each 32-byte slot word with exactly two checks:

1. **Future-timestamp guard** — `timestampMs.revertIfAfterBlockTimeWithDrift(MAX_TIME_DRIFT)` prevents timestamps too far in the future.
2. **Monotonicity gate** — `timestampMs.isAfter(oldTimestampMs)` skips stale updates.

There is **no validation of the price field** (`p`, a 32-bit U64x32-encoded value occupying bits `[255:208..160..112..64]` of the slot word): [1](#0-0) 

The price is written directly to storage after the timestamp check passes: [2](#0-1) 

The `priceGuard` mapping is defined in `OracleBase` and defaults to `{min: 0, max: 0}` for every feed: [3](#0-2) 

At the provider layer, `ProtectedPriceProvider` reads the guard and converts a zero `guardMax` to `type(uint128).max`, meaning **any positive price passes when no guard is configured**: [4](#0-3) 

The test suite explicitly documents this default-unlimited behavior:

```solidity
function testPriceGuardDefaultUnlimited() public {
    // No guard set (default 0/0) → any positive price accepted
    oracle.setData(FEED_ID, 900_000_000, 300, 0, block.timestamp);
    (uint128 bid, uint128 ask) = _read();
    assertGt(bid, 0);
    assertLt(bid, ask);
}
```

The `getOracleData` read path in `CompressedOracle.sol` returns the raw decoded price with no guard enforcement: [5](#0-4) 

**Invariant broken:** The oracle price delivered to a pool must reflect a real market value. Because the push path imposes no magnitude constraint and the provider-level guard is opt-in with an unlimited default, a pusher can write `p = 0xFFFFFFFF` (maximum uint32) into any slot they control, and that value propagates to the pool's bid/ask without rejection.

This is the direct analog of the swETH `reprice()` bug: the trusted bot role (`SwellLib.BOT`) could set `_preRewardETHReserves` to any value, resetting `ethReserves` and `swETHToETHRateFixed` without bounds. Here, the trusted pusher role can set the U64x32 price field to any value, resetting the oracle price without bounds.

---

### Impact Explanation

- **Bad-price execution**: A pool consuming the feed via `ProtectedPriceProvider` (or `PriceProviderL2`) will compute bid/ask from the inflated price. Swappers receive more output tokens than the true market rate permits, draining the pool's reserves.
- **Pool insolvency**: If the price is set far below market, the pool under-charges input, failing to collect owed fees and principal.
- **Affected functions**: `getBidAndAskPrice()` → `_getBidAndAskPrice()` → `_computeBidAsk()` in both `ProtectedPriceProvider` and `ProtectedPriceProviderL2`. [6](#0-5) 

---

### Likelihood Explanation

**Medium.** The trigger requires a compromised or malicious pusher key. Pushers are semi-trusted: they are delegated by the feed creator via `allowPushers` (EIP-191 signature) or `allowContractPushers` (live `isPusher` staticcall). Once delegated, a pusher can push unlimited times with no per-push creator authorization. A single leaked or malicious pusher key is sufficient. [7](#0-6) 

---

### Recommendation

1. **Oracle-level price guard enforcement**: Enforce `priceGuard` inside `getOracleData` (or `_price`) in `CompressedOracle.sol`, not only at the provider layer. A feed with no guard set should either revert or return a stale sentinel.
2. **Mandatory guard before pool registration**: Require a non-zero `priceGuard` to be configured for a `feedId` before any pool can register against it (analogous to the swETH resolution requiring a time-elapsed check and ratio bounds before `reprice()` is accepted).
3. **Rate-of-change check**: Reject pushes where the new price deviates from the stored price by more than a configurable percentage per update, preventing a single push from moving the price by orders of magnitude.

---

### Proof of Concept

```solidity
// 1. Creator deploys feed; pool is registered with ProtectedPriceProvider,
//    no priceGuard configured (default {min:0, max:0}).

// 2. Creator delegates a pusher via allowPushers().

// 3. Pusher constructs a slot word with p = 0xFFFFFFFF (max uint32):
//    raw = (0xFFFFFFFF << 16) | (s0 << 8) | s1
//    word = (tsMs << 8) | slotId | (raw << 208)

// 4. Pusher calls oracle.fallback() with the crafted word.
//    Only check: timestamp is newer than stored → passes.
//    Price 0xFFFFFFFF is written to storage.

// 5. Pool calls provider.getBidAndAskPrice() during a swap.
//    Provider reads price via oracle.price(feedId, pool).
//    priceGuard: min=0, max=0 → guardMax becomes type(uint128).max.
//    Inflated price passes guard.
//    bid/ask computed from inflated mid → pool executes swap at
//    ~4.3 billion × U64x32 scale factor above true market price.

// 6. Swapper receives massively inflated output; pool is drained.
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

**File:** smart-contracts-poc/contracts/oracles/compressed/OracleBase.sol (L17-18)
```text
    mapping(bytes32 => PriceGuard) public priceGuard;
    mapping(bytes32 => address) public pendingStateGuard;
```

**File:** smart-contracts-poc/contracts/ProtectedPriceProvider.sol (L224-229)
```text
}
```

**File:** smart-contracts-poc/contracts/ProtectedPriceProvider.sol (L231-248)
```text

```
