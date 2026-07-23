### Title
PriceGuard Bounds Are Configured But Never Enforced in the Oracle Read Path — (`smart-contracts-poc/contracts/oracles/providers/OracleBase.sol`)

---

### Summary

The `priceGuard[feedId]` min/max bounds are stored via `setPriceGuard()` but are never consulted in `_readPrice()`, the sole internal function that serves prices to pools and integrators. Neither `PythOracle` nor `ChainlinkOracle` override `_readPrice()`. Any price stored in `oracleData[feedId]` — regardless of whether it violates the configured bounds — is returned verbatim to pool swaps.

---

### Finding Description

`OracleBase` (providers) exposes `setPriceGuard(feedId, minPrice, maxPrice)` which stores per-feed absolute price bounds: [1](#0-0) 

The wiki and inline documentation describe `_readPrice` as the function that "applies `PriceGuard` and staleness checks to the raw data." The actual implementation does neither: [2](#0-1) 

`_readPrice` is called directly by both gated read paths — `price()` (pool swaps) and `integratorPrice()` — after all access-control checks pass: [3](#0-2) 

`PythOracle` does not override `_readPrice()`: [4](#0-3) 

`ChainlinkOracle` does not override `_readPrice()` either. Its ingestion path (`_store`) only checks that the timestamp is not in the future and is monotonically increasing — it does not check `priceGuard`: [5](#0-4) 

The same pattern exists in `CompressedOracleV1._price()`, which calls `getOracleData()` and returns raw data without consulting `priceGuard[feedId]`: [6](#0-5) 

The `priceGuard` mapping is populated and emits events but is never read on any execution path that reaches a pool. It is dead configuration.

---

### Impact Explanation

**Bad-price execution: unbounded or unclamped bid/ask quote reaches a pool swap.**

When the Pyth Lazer or Chainlink DON pushes a price outside the configured `[min, max]` window — due to extreme market conditions, a provider bug, or oracle manipulation — the price passes ingestion (monotonicity + future-timestamp checks only) and is stored. On the next swap, `price(feedId, pool)` returns the out-of-bounds value to the pool's swap math. The pool executes at the corrupted mid-price, causing:

- Traders to receive more output tokens than the oracle curve permits (swap conservation failure), or
- LPs to absorb losses because the pool settles at a price that does not reflect the true market.

This matches the contest-relevant impact gate: **bad-price execution** and **swap conservation failure** causing direct loss of LP assets or owed swap settlement.

---

### Likelihood Explanation

**Low-Medium.** The trigger requires the oracle provider to emit a price outside the configured guard window. This can occur during:

- Extreme market volatility (flash crash / spike) where the Pyth/Chainlink feed briefly exceeds the guard range before correcting.
- A provider-side bug in price normalization (e.g., the `_toMid8` 18→8 decimal conversion in `ChainlinkOracle` producing an out-of-range value).
- A compromised or malfunctioning Pyth Lazer signer set.

The PriceGuard feature was presumably added precisely to protect against these scenarios; its non-enforcement makes the protection illusory.

---

### Recommendation

Apply the `priceGuard` check inside `_readPrice()` in `smart-contracts-poc/contracts/oracles/providers/OracleBase.sol`:

```solidity
function _readPrice(bytes32 feedId)
    internal
    view
    returns (uint256 mid, uint256 spread, uint16 spread1, uint256 refTime)
{
    OracleData memory data = _oracleDataRaw(feedId);

    PriceGuard memory guard = priceGuard[feedId];
    if (guard.min != 0 || guard.max != 0) {
        require(
            data.price >= guard.min && data.price <= guard.max,
            PriceOutOfBounds(feedId, data.price)
        );
    }

    return (uint256(data.price), uint256(data.spread0), data.spread1, data.timestampMs.toSeconds());
}
```

Apply the equivalent fix to `CompressedOracleV1.getOracleData()` in `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol` before the return at line 113.

Additionally, add a staleness check in `_readPrice()` comparing `data.timestampMs` against `block.timestamp` and a configurable `maxStaleness` parameter, since the current read path also does not enforce any age limit on stored prices.

---

### Proof of Concept

1. Admin calls `setPriceGuard(feedId, 1e8, 100e8)` — configuring a $1–$100 guard for a feed.
2. Pyth Lazer pushes a signed payload with `price = 200e8` (outside bounds). `_verifyAndStore` accepts it: the timestamp is newer and not in the future, so `oracleData[feedId].price = 200e8` is stored.
3. A registered pool calls `price(feedId, pool)` during a swap. All access-control checks pass.
4. `_readPrice(feedId)` returns `mid = 200e8` — the PriceGuard is never consulted.
5. The pool's swap math uses `200e8` as the oracle mid-price. Traders receive output tokens priced at $200 instead of the true $50 market price, draining LP reserves at a 4× loss ratio.

### Citations

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L88-97)
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

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L160-172)
```text
    function price(bytes32 feedId, address pool)
        external
        feedExists(feedId)
        notBlacklisted
        returns (uint256 mid, uint256 spread, uint16 spread1, uint256 refTime)
    {
        require(pool != address(0) && IPool(pool).inSwap() == msg.sender, InvalidInSwap());
        require(!blacklisted[pool], Blacklisted(pool));
        require(registeredPool[feedId][pool], NotRegistered(feedId, pool));

        (mid, spread, spread1, refTime) = _readPrice(feedId);
        emit PriceRead(pool, feedId);
    }
```

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L187-194)
```text
    function _readPrice(bytes32 feedId)
        internal
        view
        returns (uint256 mid, uint256 spread, uint16 spread1, uint256 refTime)
    {
        OracleData memory data = _oracleDataRaw(feedId);
        return (uint256(data.price), uint256(data.spread0), data.spread1, data.timestampMs.toSeconds());
    }
```

**File:** smart-contracts-poc/contracts/oracles/providers/PythOracle.sol (L14-28)
```text
contract PythOracle is OracleBase, LazerConsumer {
    string public constant version = "0.0.10";
    /// @notice Oracle family discriminator for off-chain introspection (matches the
    ///         pusher/console `kind` vocabulary).
    string public constant kind = "pyth-lazer";

    constructor(
        address _owner,
        address _lazerAddress,
        uint256 maxTimeDrift,
        uint8[] memory expectedProperties
    )
        OracleBase(_owner, maxTimeDrift)
        LazerConsumer(_lazerAddress, maxTimeDrift, expectedProperties)
    {}
```

**File:** smart-contracts-poc/contracts/oracles/providers/ChainlinkOracle.sol (L85-95)
```text
    function _store(bytes memory reportData) internal {
        (bytes32 feedId, OracleData memory d) = _decodeReport(reportData);

        d.timestampMs.revertIfZero();
        d.timestampMs.revertIfAfterBlockTimeWithDrift(MAX_TIME_DRIFT);

        if (d.timestampMs.isAfter(oracleData[feedId].timestampMs)) {
            oracleData[feedId] = d;
            emit ReportStored(feedId, d.price, d.spread0, d.timestampMs);
        }
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
