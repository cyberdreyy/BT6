### Title
`setPriceGuard` Bounds Are Stored But Never Enforced in `CompressedOracle` Read Path, Allowing Unclamped Prices to Reach Pool Swaps — (File: `smart-contracts-poc/contracts/oracles/compressed/OracleBase.sol` / `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`OracleBase.setPriceGuard` stores a `PriceGuard{min, max}` struct per feedId, giving creators a documented mechanism to clamp the price range their feed may report. However, every read-path function in `CompressedOracle` — `getOracleData`, `_price`, and `price` — decodes and returns the raw stored price without ever consulting `priceGuard[feedId]`. The guard is dead code. Any price pushed by a delegated pusher, including zero or the maximum `U64x32` value, flows unmodified into pool swaps.

---

### Finding Description

`OracleBase.setPriceGuard` writes a bound:

```solidity
// OracleBase.sol (compressed)
function setPriceGuard(bytes32 feedId, uint128 minPrice, uint128 maxPrice)
    external checkRole(feedId)
{
    require(minPrice < maxPrice);
    priceGuard[feedId] = PriceGuard({min: minPrice, max: maxPrice});
    emit PriceGuardUpdated(feedId, minPrice, maxPrice);
}
``` [1](#0-0) 

The entire downstream read path never reads `priceGuard[feedId]`:

```solidity
// CompressedOracle.sol – getOracleData
data.price = U64x32.decode(compressed.p);   // raw, no guard check
data.spread0 = _decodeCodebookIndex(compressed.s0);
data.spread1 = _decodeCodebookIndex(compressed.s1);
data.timestampMs = _layout.timestampMs;
``` [2](#0-1) 

```solidity
// CompressedOracle.sol – _price (called by public price())
function _price(bytes32 feedId) internal view
    returns (uint256 mid, uint256 spread, uint16 spread1, uint256 refTime)
{
    OracleData memory data = getOracleData(feedId);
    return (uint256(data.price), uint256(data.spread0), data.spread1, data.timestampMs.toSeconds());
}
``` [3](#0-2) 

The `fallback` push path also writes any price value without guard enforcement:

```solidity
// fallback – no price validation before sstore
_writeStorage(key, bytes32(bytes32(word & ~uint256(0xff))));
``` [4](#0-3) 

The analog to the RSA finding is exact: just as `_validateKeyBatch` stored and checked the exponent only against zero while ignoring the minimum-threshold invariant, `CompressedOracle` stores the price guard but ignores it entirely during every read, leaving the invariant unenforced.

---

### Impact Explanation

A compromised or malicious delegated pusher (authorized via `allowPushers` or `allowContractPushers`) can push any `U64x32`-encoded price — including `p = 0` (decoded price = 0) or `p = 0xFFFFFFFF` (decoded price ≈ 2^58) — into the creator's namespace. Because `getOracleData` returns the raw decoded value and `price()` forwards it unchanged, the price provider receives and forwards the manipulated quote to `MetricOmmPool.swap`. The pool executes the swap at the bad bid/ask, causing:

- Traders to receive more output than the oracle curve permits (swap conservation failure), or
- LPs to be left with a position priced at zero or at an extreme, making their claims unrecoverable (pool insolvency).

---

### Likelihood Explanation

Medium. The attack surface is any address that has been granted pusher delegation by a creator who also set a price guard. The creator's intent in calling `setPriceGuard` is precisely to bound what delegated pushers may publish; the guard's silence means that intent is silently violated. A single compromised pusher key is sufficient — no privileged role, no admin action, and no non-standard token behavior is required.

---

### Recommendation

Enforce the price guard inside `getOracleData` (or `_price`) immediately after decoding the price:

```solidity
data.price = U64x32.decode(compressed.p);

PriceGuard memory guard = priceGuard[feedId];
if (guard.max != 0) {                          // guard is set
    require(
        data.price >= guard.min && data.price <= guard.max,
        PriceOutOfGuard(feedId, data.price)
    );
}
```

Alternatively, return the sentinel/stalled state (price = 0, spread = BPS_BASE) when the decoded price violates the guard, so downstream consumers treat the feed as unavailable rather than executing at a bad price.

---

### Proof of Concept

1. Creator calls `setPriceGuard(feedId, 1_000_000, 2_000_000)` — bounds the feed to [1 M, 2 M].
2. Creator calls `allowPushers(deadline, [pusherAddr], [sig])` — delegates a pusher.
3. Pusher constructs a slot word with `p = 0` (all mantissa/exponent bits zero → decoded price = 0) and a fresh timestamp, then calls `fallback` with that word.
4. `getOracleData(feedId)` decodes `data.price = 0` and returns it without consulting `priceGuard[feedId]`.
5. `price(feedId, pool)` returns `mid = 0`.
6. The price provider forwards `mid = 0` as the bid/ask to `MetricOmmPool.swap`.
7. The pool executes the swap at price zero: the trader receives the full output reserve for zero input, draining the pool. [5](#0-4) [6](#0-5)

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/OracleBase.sol (L17-17)
```text
    mapping(bytes32 => PriceGuard) public priceGuard;
```

**File:** smart-contracts-poc/contracts/oracles/compressed/OracleBase.sol (L49-58)
```text
    function setPriceGuard(bytes32 feedId, uint128 minPrice, uint128 maxPrice)
        external
        checkRole(feedId)
    {
        require(minPrice < maxPrice);

        priceGuard[feedId] = PriceGuard({min: minPrice, max: maxPrice});

        emit PriceGuardUpdated(feedId, minPrice, maxPrice);
    }
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L171-178)
```text
    function _price(bytes32 feedId)
        internal
        view
        returns (uint256 mid, uint256 spread, uint16 spread1, uint256 refTime)
    {
        OracleData memory data = getOracleData(feedId);
        return (uint256(data.price), uint256(data.spread0), data.spread1, data.timestampMs.toSeconds());
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L340-344)
```text
            bool newer = timestampMs.isAfter(oldTimestampMs);
            if (!newer) continue;

            _writeStorage(key, bytes32(bytes32(word & ~uint256(0xff))));
        }
```
