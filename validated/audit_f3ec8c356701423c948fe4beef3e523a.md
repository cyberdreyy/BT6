### Title
`AnchoredPriceProvider` Synthetic Mode Accepts `baseFeedId == quoteFeedId`, Producing a Constant Price of `1e8` Fed to Pool Swaps — (`File: smart-contracts-poc/contracts/AnchoredPriceProvider.sol`)

### Summary

`AnchoredPriceProvider` and its factory `AnchoredProviderFactory` do not validate that `baseFeedId != quoteFeedId` when deploying a synthetic-ratio provider. When both feed IDs are identical and non-zero, the synthetic ratio path computes `mid / mid = 1e8` (constant 1.0 in 8-decimal format) regardless of the actual market price, and feeds that corrupted bid/ask into every pool swap.

### Finding Description

`AnchoredPriceProvider` supports a two-feed synthetic ratio mode: when `quoteFeedId != bytes32(0)`, `_getBidAndAskPrice` reads both legs and computes `mid = mulDiv(mid1, ORACLE_DECIMALS, mid2)`. [1](#0-0) 

The constructor stores both feed IDs without any equality check: [2](#0-1) 

The factory `createAnchoredProvider` likewise passes `baseFeedId` and `quoteFeedId` straight through, validating only envelope parameters (margin, staleness, spread bounds) — never that the two feed IDs differ: [3](#0-2) 

When `baseFeedId == quoteFeedId` (both non-zero), `_readLeg` is called twice on the same feed, returning the same `mid` value both times. The ratio then collapses:

```
mid = mulDiv(mid, 1e8, mid) ≈ 1e8   (constant 1.0 in 8-decimal)
spreadBps += spreadBps2              (spread doubled, but still within MAX_SPREAD_BPS)
```

The resulting bid/ask is computed from a mid of `1e8` — completely detached from the real market price — and returned to the pool's swap path.

By contrast, the token-pair identity check (`_baseToken != _quoteToken`) is correctly enforced in the same constructor: [4](#0-3) 

The analogous guard for feed IDs is absent.

### Impact Explanation

Any pool whose `IPriceProvider` is an `AnchoredPriceProvider` deployed with `baseFeedId == quoteFeedId` will receive a constant oracle price of `1.0` (in 8-decimal units) for every swap. If the real market price is, for example, BTC/ETH ≈ 20, the pool prices at 1/20th of fair value. Traders can drain the pool's token0 reserves by buying at the artificially low ask, or dump token1 at the artificially high bid. LP principal is directly at risk.

The `_computeBidAsk` band clamp does not protect against this: it only enforces that the final quote is no tighter than the reference band, but the reference band itself is computed from the corrupted `mid = 1e8`. [5](#0-4) 

### Likelihood Explanation

`createAnchoredProvider` is a public, permissionless function — any address can call it. The factory enforces envelope bounds but not feed-ID distinctness. A pool admin (semi-trusted) who accidentally or deliberately passes the same feed ID for both legs will deploy a permanently broken provider with no on-chain rejection. Because the factory is the intended guardrail for parameter correctness, this gap is reachable through the normal, documented deployment path. [6](#0-5) 

### Recommendation

Add a validation in both the `AnchoredPriceProvider` constructor and `AnchoredProviderFactory.createAnchoredProvider` that rejects equal, non-zero feed IDs:

```solidity
// In AnchoredPriceProvider constructor, after storing feed IDs:
if (_quoteFeedId != bytes32(0)) {
    require(_baseFeedId != _quoteFeedId, "identical feed IDs");
}
```

Apply the same check in `createAnchoredProvider` before deploying the provider, mirroring the existing pattern used for token-pair validation.

### Proof of Concept

```solidity
// Deploy a provider with baseFeedId == quoteFeedId
bytes32 sameFeed = oracle.feedIdOf(creator, 0, 0); // any valid feed

AnchoredPriceProvider badProvider = new AnchoredPriceProvider(
    factory,
    address(oracle),
    sameFeed,   // baseFeedId
    sameFeed,   // quoteFeedId — identical, no revert
    FLOOR, MAX_REF_STALENESS, MAX_SPREAD_BPS,
    false, 0,
    BASE_TOKEN, QUOTE_TOKEN
);

// Push a real price: BTC/USD = 65_000e8
oracle.setData(sameFeed, 65_000e8, 5, 0, block.timestamp);

// Pool calls getBidAndAskPrice() — mid collapses to 1e8, not 65_000e8
(uint128 bid, uint128 ask) = badProvider.getBidAndAskPrice();
// bid/ask are computed from mid = 1e8 ≈ 1.0, not 65_000
// Swappers drain the pool at a 65_000x price discrepancy
```

### Citations

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L139-141)
```text
        offchainOracle = IOffchainOracle(_oracle);
        baseFeedId = _baseFeedId;
        quoteFeedId = _quoteFeedId;
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L146-148)
```text
        require(_baseToken != address(0) && _quoteToken != address(0) && _baseToken != _quoteToken);
        baseToken = _baseToken;
        quoteToken = _quoteToken;
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L258-270)
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

```

**File:** smart-contracts-poc/contracts/AnchoredProviderFactory.sol (L156-194)
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

        // Feeds without an explicit class fall back to the admin-configured DEFAULT_CLASS envelope.
        bytes32 classId = feedClass[baseFeedId];
        if (classId == bytes32(0)) classId = DEFAULT_CLASS;

        Envelope storage env = envelopes[classId];
        if (!env.exists) revert EnvelopeNotFound(classId);
        if (
            minMargin < env.minMarginMin || minMargin > env.minMarginMax
            || maxRefStaleness < env.stalenessMin || maxRefStaleness > env.stalenessMax
            || maxSpreadBps < env.maxSpreadMin || maxSpreadBps > env.maxSpreadMax
        ) revert ParamsOutOfEnvelope();

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
