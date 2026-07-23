### Title
`AnchoredPriceProvider` synthetic ratio mode produces a fixed price of 1.0 when `baseFeedId == quoteFeedId` — (`smart-contracts-poc/contracts/AnchoredPriceProvider.sol`)

---

### Summary

Neither `AnchoredPriceProvider`'s constructor nor `AnchoredProviderFactory.createAnchoredProvider()` checks that `baseFeedId != quoteFeedId`. When both are equal, the synthetic ratio computation in `_getBidAndAskPrice()` always resolves to exactly `1e8` (1.0 in 8-decimal terms), regardless of the actual market price. Any pool using such a provider quotes a permanently wrong price, enabling swap conservation failure and direct LP principal loss.

---

### Finding Description

`AnchoredPriceProvider._getBidAndAskPrice()` implements a two-leg synthetic ratio:

```solidity
function _getBidAndAskPrice() internal returns (uint128, uint128) {
    (uint256 mid, uint256 spreadBps, , bool ok) = _readLeg(baseFeedId);
    if (!ok) return (0, type(uint128).max);

    bytes32 _quote = quoteFeedId;
    if (_quote != bytes32(0)) {
        (uint256 mid2, uint256 spreadBps2, , bool ok2) = _readLeg(_quote);
        if (!ok2 || mid2 == 0) return (0, type(uint128).max);
        mid = Math.mulDiv(mid, ORACLE_DECIMALS, mid2);   // ← ratio
        spreadBps += spreadBps2;
    }
    return _computeBidAsk(mid, spreadBps);
}
``` [1](#0-0) 

When `baseFeedId == quoteFeedId`, both `_readLeg` calls hit the same oracle slot and return identical values. The ratio collapses:

```
mid = mulDiv(mid, 1e8, mid) = 1e8   (always 1.0, regardless of market price)
spreadBps = 2 × spreadBps           (doubled, but irrelevant — mid is already wrong)
```

The `AnchoredPriceProvider` constructor stores both feed IDs with no equality check:

```solidity
baseFeedId = _baseFeedId;
quoteFeedId = _quoteFeedId;
``` [2](#0-1) 

The factory's `createAnchoredProvider()` also passes both IDs through without checking they differ:

```solidity
AnchoredPriceProvider p = new AnchoredPriceProvider(
    address(this), oracle,
    baseFeedId, quoteFeedId,   // ← no baseFeedId != quoteFeedId guard
    ...
);
``` [3](#0-2) 

The factory validates clamp parameters (minMargin, maxRefStaleness, maxSpreadBps) against pair-class envelopes but has no semantic check on the feed-ID pair: [4](#0-3) 

---

### Impact Explanation

A pool backed by this provider always receives a mid of `1e8` (= 1.0). For any real asset pair — e.g., ETH/BTC at ~0.05 — the pool quotes bid/ask around 1.0 instead of 0.05. Traders can sell ETH to the pool at 20× the fair price, draining the pool's BTC reserves. LP principal is directly lost; the pool becomes insolvent relative to LP claims. This is a swap conservation failure: the pool pays out more than the oracle/bin curve permits.

---

### Likelihood Explanation

`createAnchoredProvider` is explicitly permissionless — any address may call it as long as the oracle is admin-approved: [5](#0-4) 

The test suite confirms this:

```solidity
function testCreateIsPermissionless() public {
    vm.prank(stranger); // anyone can deploy — only the params are policed
    address provider = factory.createAnchoredProvider(...);
``` [6](#0-5) 

An attacker needs only an admin-approved oracle (the allow-list is public) and a valid envelope class. No privileged role is required.

---

### Recommendation

Add an equality guard in the `AnchoredPriceProvider` constructor:

```solidity
if (_quoteFeedId != bytes32(0)) {
    require(_baseFeedId != _quoteFeedId, "SameFeedId");
}
```

Optionally mirror this in `AnchoredProviderFactory.createAnchoredProvider()` for an earlier, clearer revert.

---

### Proof of Concept

1. Admin adds oracle `O` to the allow-list and configures a DEFAULT_CLASS envelope.
2. Attacker calls `factory.createAnchoredProvider(O, FEED_ETH_USD, FEED_ETH_USD, ...)` — `baseFeedId == quoteFeedId`.
3. Oracle `O` has `FEED_ETH_USD` → mid = `3000e8` (ETH/USD = $3,000).
4. Attacker deploys a pool using this provider and seeds liquidity.
5. On any swap, `_getBidAndAskPrice()` computes:
   - `mid1 = 3000e8`, `mid2 = 3000e8` (same feed)
   - `mid = mulDiv(3000e8, 1e8, 3000e8) = 1e8` ← **1.0, not 3000**
6. Pool quotes bid/ask around 1.0 ETH per USD. A trader sells 1 ETH and receives ~3000 USD worth of the quote token at the pool's expense.
7. LP principal is drained proportional to the price discrepancy.

### Citations

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L139-141)
```text
        offchainOracle = IOffchainOracle(_oracle);
        baseFeedId = _baseFeedId;
        quoteFeedId = _quoteFeedId;
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L258-272)
```text
    function _getBidAndAskPrice() internal returns (uint128, uint128) {
        (uint256 mid, uint256 spreadBps, , bool ok) = _readLeg(baseFeedId);
        if (!ok) return (0, type(uint128).max);

        bytes32 _quote = quoteFeedId;
        if (_quote != bytes32(0)) {
            (uint256 mid2, uint256 spreadBps2, , bool ok2) = _readLeg(_quote);
            if (!ok2 || mid2 == 0) return (0, type(uint128).max);
            // Synthetic ratio (8-decimal): mid1 / mid2. Relative uncertainties of a ratio add.
            mid = Math.mulDiv(mid, ORACLE_DECIMALS, mid2);
            spreadBps += spreadBps2;
        }

        return _computeBidAsk(mid, spreadBps);
    }
```

**File:** smart-contracts-poc/contracts/AnchoredProviderFactory.sol (L156-168)
```text
    function createAnchoredProvider(
        address oracle,
        bytes32 baseFeedId,
        bytes32 quoteFeedId,
        uint256 minMargin,
        uint256 maxRefStaleness,
        uint16  maxSpreadBps,
        bool    mutableParams,
        int256  marginStep,
        address baseToken,
        address quoteToken
    ) external override returns (address provider) {
        if (!_oracles.contains(oracle)) revert OracleNotAllowed(oracle);
```

**File:** smart-contracts-poc/contracts/AnchoredProviderFactory.sol (L176-180)
```text
        if (
            minMargin < env.minMarginMin || minMargin > env.minMarginMax
            || maxRefStaleness < env.stalenessMin || maxRefStaleness > env.stalenessMax
            || maxSpreadBps < env.maxSpreadMin || maxSpreadBps > env.maxSpreadMax
        ) revert ParamsOutOfEnvelope();
```

**File:** smart-contracts-poc/contracts/AnchoredProviderFactory.sol (L182-194)
```text
        AnchoredPriceProvider p = new AnchoredPriceProvider(
            address(this),
            oracle,
            baseFeedId,
            quoteFeedId,
            minMargin,
            maxRefStaleness,
            maxSpreadBps,
            mutableParams,
            marginStep,
            baseToken,
            quoteToken
        );
```

**File:** smart-contracts-poc/test/AnchoredProviderFactory.t.sol (L367-371)
```text
    function testCreateIsPermissionless() public {
        vm.prank(stranger); // anyone can deploy — only the params are policed
        address provider = factory.createAnchoredProvider(address(oracle), FEED_ID, bytes32(0), FLOOR, STALENESS, U_MAX, false, int256(0), BASE_TOKEN, QUOTE_TOKEN);
        assertEq(factory.providerOwner(provider), stranger);
    }
```
