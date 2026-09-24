This confirms the analog: `AeraVaultAssetRegistry.spotPrices()` consumes raw `AggregatorV2V3Interface` Chainlink feeds directly, and `_checkOraclePrice` only validates `answer <= 0` and staleness via heartbeat — it never checks the returned `answer` against the aggregator's `minAnswer`/`maxAnswer` circuit-breaker bounds [1](#0-0) . The `AssetInformation.oracle` field is typed as `AggregatorV2V3Interface`, i.e., a real Chainlink feed reference, not a bounded wrapper [2](#0-1) .

### Title
PriceOracle will return the wrong price for asset if underlying Chainlink aggregator hits minAnswer/maxAnswer - (File: v2/AeraVaultAssetRegistry.sol)

### Summary
`AeraVaultAssetRegistry._checkOraclePrice` and `spotPrices()` read `latestRoundData()` directly from a raw Chainlink `AggregatorV2V3Interface` for each registered asset, only checking that `answer <= 0` and that the price isn't stale via `heartbeat` [3](#0-2) . It never validates `answer` against the aggregator's circuit-breaker `minAnswer`/`maxAnswer` bounds, so during an asset crash the feed will keep reporting `minAnswer` as if it were the real market price.

### Finding Description
`spotPrices()` iterates over all non-ERC4626 assets and for each calls `_checkOraclePrice(asset)`, which calls `asset.oracle.latestRoundData()` and only guards against a non-positive answer and staleness [4](#0-3) . If the underlying Chainlink aggregator hits its lower circuit-breaker bound (`minAnswer`) — as happened to LUNA-linked feeds — it continues returning a valid, non-stale, positive `answer` equal to `minAnswer`, which passes every check in `_checkOraclePrice` and is propagated as the asset's spot price into the vault's value/NAV computation used for guardian-submitted rebalances and withdrawal accounting.

### Impact Explanation
This affects `AeraVaultV2`/`AeraVaultAssetRegistry`-based vaults (deployed, in-scope V2 code): an asset that has crashed far below its aggregator's `minAnswer` will still be valued at the inflated floor price in `spotPrices()`, which feeds into the vault's guardian-submission value checks (`minDailyValue` in Hooks) and overall vault accounting. This can allow the vault to be manipulated into holding/valuing a worthless asset at an artificially high price, misrepresenting vault NAV and potentially enabling extraction of value at the expense of depositors — a live Medium-severity oracle mispricing/fund-freezing pattern.

### Likelihood Explanation
This requires no privileged access — it is purely a consequence of an already-deployed, out-of-protocol-control Chainlink feed hitting its circuit breaker during a real-world market crash of one of the registered non-numeraire assets. It's the exact scenario Chainlink documents can occur for volatile/depegging assets and has happened historically (e.g., LUNA on Venus). Given `AssetInformation.oracle` is a raw `AggregatorV2V3Interface` with no bounds validation added in `AeraVaultAssetRegistry`, any registered asset oracle is exposed to this risk if/when the underlying feed's aggregator has configured min/max bounds.

### Recommendation
In `_checkOraclePrice` (`v2/AeraVaultAssetRegistry.sol`), after fetching `answer`, also fetch the aggregator's configured `minAnswer`/`maxAnswer` (e.g., via `IOffchainAggregator` or a stored per-asset bound set at registration time) and revert if `answer <= minAnswer || answer >= maxAnswer`, mirroring the recommendation in the referenced report.

### Proof of Concept
1. Fork mainnet at a block where a registered asset's Chainlink feed's aggregator has known `minAnswer` bounds (e.g., an asset with a low-liquidity/volatile feed).
2. Deploy/point an `AeraVaultAssetRegistry` instance at that live feed as `asset.oracle`.
3. Simulate the underlying asset crashing far below `minAnswer` on the source (or use a mock aggregator returning `minAnswer` with fresh `updatedAt`).
4. Call `spotPrices()` and assert it returns `minAnswer`-derived price instead of reverting, confirming the vault would treat the crashed asset as still worth the floor price — no revert occurs despite the true market price being far lower. [5](#0-4)

### Citations

**File:** v2/AeraVaultAssetRegistry.sol (L447-461)
```text
            } else {
                price = _checkOraclePrice(_assets[i]);
                oracleDecimals = _assets[i].oracle.decimals();

                if (oracleDecimals < 18) {
                    price = price * (10 ** (18 - oracleDecimals));
                } else if (oracleDecimals > 18) {
                    price = price / (10 ** (oracleDecimals - 18));
                }

                prices[index] = AssetPriceReading({
                    asset: _assets[i].asset,
                    spotPrice: price
                });
            }
```

**File:** v2/AeraVaultAssetRegistry.sol (L508-530)
```text
    /// @notice Ensure oracle returns valid value and it's up to date.
    /// @param asset Asset details to check.
    /// @return price Valid oracle price.
    function _checkOraclePrice(AssetInformation memory asset)
        internal
        view
        returns (uint256 price)
    {
        (, int256 answer,, uint256 updatedAt,) = asset.oracle.latestRoundData();

        // Check price staleness
        if (answer <= 0) {
            revert Aera__OraclePriceIsInvalid(asset, answer);
        }
        if (
            asset.heartbeat > 0
                && updatedAt + asset.heartbeat + 1 hours < block.timestamp
        ) {
            revert Aera__OraclePriceIsTooOld(asset, updatedAt);
        }

        price = uint256(answer);
    }
```

**File:** v2/interfaces/IAssetRegistry.sol (L15-20)
```text
    struct AssetInformation {
        IERC20 asset;
        uint256 heartbeat;
        bool isERC4626;
        AggregatorV2V3Interface oracle;
    }
```
