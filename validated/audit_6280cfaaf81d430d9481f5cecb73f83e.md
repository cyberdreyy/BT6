### Title
PriceGuard Bounds Are Never Enforced in the On-Chain `price()` Read Path — (`smart-contracts-poc/contracts/oracles/providers/OracleBase.sol`)

---

### Summary

`OracleBase` (providers) exposes `setPriceGuard` to configure per-feed `[min, max]` price bounds, but the internal `_readPrice` function that actually serves prices to pools never consults those bounds. Because `_readPrice` is not marked `virtual`, child contracts (`PythOracle`, `ChainlinkOracle`) cannot override it to add the check. Every on-chain price read through `price(feedId, pool)` returns raw oracle data unconditionally, making PriceGuard a dead feature.

---

### Finding Description

`OracleBase` (providers) stores per-feed safety configuration in `priceGuard[feedId]`: [1](#0-0) 

The `price()` function — the sole gated on-chain read path for pools — calls `_readPrice`: [2](#0-1) 

`_readPrice` returns raw storage data with no bounds check and no staleness check: [3](#0-2) 

Critically, `_readPrice` is declared `internal view` **without** the `virtual` keyword. In Solidity, a non-`virtual` function cannot be overridden by child contracts. Therefore `PythOracle` and `ChainlinkOracle` are structurally prevented from inserting a PriceGuard check into this path. The `priceGuard` mapping is written by `setPriceGuard` but is never read anywhere in the execution path that delivers prices to pools.

---

### Impact Explanation

Any price stored in `oracleData[feedId]` — regardless of whether it falls outside the configured `[priceGuard.min, priceGuard.max]` window — is returned verbatim to the calling pool. A corrupted Pyth/Chainlink push, a flash-crash price, or a deliberately crafted payload that passes the provider's signature check but carries an extreme value will reach `MetricOmmPool.swap()` unclamped. The pool's bin math will execute the swap at that bad price, causing the trader to receive more tokens than the oracle curve permits or the LP to be drained at an incorrect rate — a direct loss of LP principal.

---

### Likelihood Explanation

The trigger requires a valid provider-signed payload carrying an out-of-bounds price. Pyth and Chainlink data anomalies (flash crashes, feed misconfiguration, sequencer lag) are documented real-world events. No privileged action is needed after the initial oracle push; the bad price is served automatically on the next swap. The `feedExists` modifier only checks that `timestampMs != 0`, not that the price is sane. [4](#0-3) 

---

### Recommendation

Mark `_readPrice` as `virtual` so child contracts can override it, **and** add the PriceGuard check directly in the base implementation as a fallback:

```solidity
function _readPrice(bytes32 feedId)
    internal
    view
    virtual
    returns (uint256 mid, uint256 spread, uint16 spread1, uint256 refTime)
{
    OracleData memory data = _oracleDataRaw(feedId);
    PriceGuard memory guard = priceGuard[feedId];
    if (guard.max != 0) {
        require(data.price >= guard.min && data.price <= guard.max, PriceOutOfBounds(feedId, data.price));
    }
    // staleness check
    require(
        data.timestampMs.toSeconds() + MAX_TIME_DRIFT >= block.timestamp,
        StalePrice(feedId)
    );
    return (uint256(data.price), uint256(data.spread0), data.spread1, data.timestampMs.toSeconds());
}
```

---

### Proof of Concept

1. Admin calls `setPriceGuard(feedId, 1e8, 2e8)` — configuring a $1–$2 bound for a feed.
2. A valid Pyth-signed payload arrives with `price = 1e12` (e.g., a flash-crash spike). `PythOracle` stores it via its push path.
3. A pool registered for `feedId` initiates a swap. `MetricOmmPool` calls `PriceProvider → OracleBase.price(feedId, pool)`.
4. `price()` passes all abuse-protection checks (inSwap, registration, blacklist) and calls `_readPrice(feedId)`.
5. `_readPrice` returns `mid = 1e12` — the raw stored value — without consulting `priceGuard[feedId]`.
6. The pool executes the swap at `mid = 1e12`, draining LP assets at a price 10,000× above the intended ceiling. [3](#0-2) [5](#0-4)

### Citations

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L30-30)
```text
    mapping(bytes32 => PriceGuard) public priceGuard;
```

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L57-61)
```text
    modifier feedExists(bytes32 feedId) {
        require(TimeMs.unwrap(oracleData[feedId].timestampMs) != 0, FeedNotFound(feedId));

        _;
    }
```

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
