### Title
Arbitrary price provider accepted without factory-origin check, enabling bad-price execution — (File: metric-core/contracts/MetricOmmPoolFactory.sol)

---

### Summary

`MetricOmmPoolFactory._validatePriceProvider()` only checks that the provider returns matching `token0()`/`token1()` addresses. It never verifies that the provider was deployed by `PriceProviderFactory` or `AnchoredProviderFactory`. The `AnchoredProviderFactory` explicitly documents `isProvider()` as the **"public-pool eligibility predicate"** — a machine-checkable factory-origin guard — but `MetricOmmPoolFactory` never calls it. Any contract that returns the correct token addresses passes validation and can be installed as a pool's price provider, feeding arbitrary bid/ask prices into swaps.

---

### Finding Description

`MetricOmmPoolFactory._validatePriceProvider()` is the sole gate for price provider acceptance at both pool creation and price provider update time:

```solidity
// metric-core/contracts/MetricOmmPoolFactory.sol L541-L546
function _validatePriceProvider(address token0, address token1, address priceProvider) internal view {
    if (priceProvider == address(0)) revert InvalidPriceProvider();
    if (IPriceProvider(priceProvider).token0() != token0 || IPriceProvider(priceProvider).token1() != token1) {
        revert PriceProviderTokenMismatch();
    }
}
```

This check is invoked in three places:

1. **`createPool`** → `_validatePoolParameters` → `_validatePriceProvider` (permissionless)
2. **`proposePoolPriceProvider`** → `_validatePriceProvider` (pool admin)
3. **`executePoolPriceProviderUpdate`** → `_validatePriceProvider` (pool admin, post-timelock)

The `AnchoredProviderFactory` explicitly documents the factory-origin check as the eligibility predicate for public pools:

```solidity
// smart-contracts-poc/contracts/AnchoredProviderFactory.sol L13
// public-pool eligibility is then the machine-checkable predicate `recognizedFactory.isProvider(p)`.
```

```solidity
// smart-contracts-poc/contracts/AnchoredProviderFactory.sol L281-L283
function isProvider(address provider) external view returns (bool) {
    return _providers.contains(provider);
}
```

`PriceProviderFactory` has an identical `isProvider()` view. Neither is ever called by `MetricOmmPoolFactory`. The invariant the system documents — that only factory-vetted providers with envelope-validated clamp parameters reach pools — is never enforced on-chain.

A malicious price provider needs only to implement:
- `token0()` → correct base token
- `token1()` → correct quote token
- `getBidAndAskPrice()` → any arbitrary `(bid, ask)` pair

It passes `_validatePriceProvider` unconditionally. Unlike a factory-deployed `AnchoredPriceProvider`, it carries none of the immutable safety guarantees: no `MAX_REF_STALENESS`, no `MAX_SPREAD_BPS` circuit breaker, no `minMargin` band, no `priceGuard` check, and no oracle attribution.

---

### Impact Explanation

A price provider that returns manipulated bid/ask prices directly controls swap execution in `MetricOmmPool`. The pool's bin-curve math uses the provider's output to determine which bin is active and what price ratio governs each swap. An unclamped or inverted quote can:

- **Swap conservation failure**: a trader receives more token output than the pool's bin curve permits at the true market price.
- **Pool insolvency**: repeated bad-price swaps drain one side of the pool below LP claims, making full withdrawal impossible.
- **Bad-price execution**: stale, inverted, or unbounded bid/ask reaches the swap path with no circuit breaker.

This matches the allowed impact gate: *bad-price execution* and *factory/oracle role checks bypassed*.

---

### Likelihood Explanation

- **Pool creation path**: permissionless — any caller can supply an arbitrary `priceProvider` to `createPool`. The pool is registered in `idxToPool`/`poolToIdx` and appears legitimate to integrators querying the factory.
- **Price provider update path**: requires pool admin role (semi-trusted, not the factory owner or oracle admin). A pool admin can propose and, after the timelock elapses, execute an update to an arbitrary provider. The timelock delays but does not prevent the substitution.

The `AnchoredProviderFactory` oracle allow-list and envelope system exist precisely to prevent unvetted providers from reaching pools; the absence of the `isProvider()` call in `MetricOmmPoolFactory` nullifies that entire subsystem.

---

### Recommendation

Add a factory-origin check inside `_validatePriceProvider`. `MetricOmmPoolFactory` should store the addresses of `PriceProviderFactory` and `AnchoredProviderFactory` (set at construction or by the owner) and require that at least one recognizes the provider:

```solidity
function _validatePriceProvider(address token0, address token1, address priceProvider) internal view {
    if (priceProvider == address(0)) revert InvalidPriceProvider();
    if (IPriceProvider(priceProvider).token0() != token0 || IPriceProvider(priceProvider).token1() != token1) {
        revert PriceProviderTokenMismatch();
    }
    // Add: factory-origin check
    if (!IPriceProviderFactory(priceProviderFactory).isProvider(priceProvider) &&
        !IAnchoredProviderFactory(anchoredProviderFactory).isProvider(priceProvider)) {
        revert PriceProviderNotFromFactory();
    }
}
```

This mirrors the recommendation in the external report: maintain a registry in the provider factories (already present via `_providers` sets) and query it from the pool factory.

---

### Proof of Concept

```solidity
// Attacker deploys a minimal malicious price provider
contract MaliciousPriceProvider {
    address public immutable token0;
    address public immutable token1;
    constructor(address t0, address t1) { token0 = t0; token1 = t1; }
    // Returns an inverted/manipulated quote
    function getBidAndAskPrice() external returns (uint128 bid, uint128 ask) {
        // e.g. price 10x above market: drains token0 from pool on every buy
        bid = uint128(10_000e8 << 64) / 1e8;
        ask = bid + 1;
    }
}

// Step 1: deploy malicious provider with correct token pair
MaliciousPriceProvider mp = new MaliciousPriceProvider(token0, token1);

// Step 2: create a pool — _validatePriceProvider passes (token0/token1 match)
factory.createPool(PoolParameters({
    ...,
    priceProvider: address(mp),
    ...
}));
// Pool is now registered in idxToPool; isPool(pool) == true

// Step 3: LPs add liquidity (pool looks legitimate from factory registry)
// Step 4: swaps execute against mp.getBidAndAskPrice() — manipulated prices
// Step 5: pool drained; LP withdrawals undercollateralized
```

The same attack applies via `proposePoolPriceProvider` + `executePoolPriceProviderUpdate` on an existing pool after the timelock, replacing a legitimate provider with `MaliciousPriceProvider` post-deployment. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L474-507)
```text
  function proposePoolPriceProvider(address pool, address newPriceProvider)
    external
    override
    nonReentrant
    onlyPoolAdmin(pool)
  {
    PoolImmutables memory p = IMetricOmmPool(pool).getImmutables();
    uint256 timelock = priceProviderTimelock[pool];
    if (p.immutablePriceProvider != address(0)) revert PriceProviderImmutable();
    _validatePriceProvider(p.token0, p.token1, newPriceProvider);

    address mutableProvider = PoolStateLibrary._slot3(pool);
    address current = mutableProvider != address(0) ? mutableProvider : p.immutablePriceProvider;
    uint256 executeAfter = block.timestamp + timelock;
    pendingPriceProvider[pool] = newPriceProvider;
    pendingPriceProviderExecuteAfter[pool] = executeAfter;
    emit PoolPriceProviderChangeProposed(pool, current, newPriceProvider, executeAfter);
  }

  /// @inheritdoc IMetricOmmPoolFactoryPoolAdmin
  function executePoolPriceProviderUpdate(address pool) external override nonReentrant onlyPoolAdmin(pool) {
    address pending = pendingPriceProvider[pool];
    if (pending == address(0)) revert NoPriceProviderChangeProposed();
    uint256 execAfter = pendingPriceProviderExecuteAfter[pool];
    // forge-lint: disable-next-line(block-timestamp) -- timelock enforcement legitimately relies on `block.timestamp`.
    if (block.timestamp < execAfter) revert PriceProviderTimelockNotElapsed(execAfter, block.timestamp);
    PoolImmutables memory p = IMetricOmmPool(pool).getImmutables();
    if (p.immutablePriceProvider != address(0)) revert PriceProviderImmutable();
    _validatePriceProvider(p.token0, p.token1, pending);
    IMetricOmmPoolFactoryActions(pool).setPriceProvider(pending);
    delete pendingPriceProvider[pool];
    delete pendingPriceProviderExecuteAfter[pool];
    emit PoolPriceProviderUpdated(pool, pending);
  }
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

**File:** smart-contracts-poc/contracts/AnchoredProviderFactory.sol (L279-283)
```text
    /// @notice The public-pool eligibility predicate: deployed by this factory ⇒ clamp-bounded quotes
    ///         with parameters that were inside the envelope at deploy time.
    function isProvider(address provider) external view returns (bool) {
        return _providers.contains(provider);
    }
```

**File:** smart-contracts-poc/contracts/PriceProviderFactory.sol (L147-150)
```text

    function isProvider(address provider) external view returns (bool) {
        return _providers.contains(provider);
    }
```
