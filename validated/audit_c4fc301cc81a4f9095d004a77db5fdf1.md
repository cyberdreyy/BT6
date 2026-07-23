### Title
Pool Admin Can Replace Price Provider With Unvalidated Contract, Bypassing Oracle Safety Guarantees — (`metric-core/contracts/MetricOmmPoolFactory.sol`)

### Summary
The `_validatePriceProvider` function only checks that the proposed price provider returns the correct token pair. It does not verify that the provider originates from an approved factory (e.g., `AnchoredProviderFactory`) or that it implements any of the required safety bounds (staleness checks, spread guards, deviation guards, price guards). A pool admin — a semi-trusted role whose fee authority is explicitly capped by the factory — can therefore replace a legitimate, clamp-bounded provider with an arbitrary contract that returns manipulated bid/ask prices, causing LP fund loss through bad-price swap execution.

### Finding Description

`MetricOmmPoolFactory._validatePriceProvider` is the sole gate used both at pool creation and at price-provider update time:

```solidity
// metric-core/contracts/MetricOmmPoolFactory.sol
function _validatePriceProvider(address token0, address token1, address priceProvider) internal view {
    if (priceProvider == address(0)) revert InvalidPriceProvider();
    if (IPriceProvider(priceProvider).token0() != token0 || IPriceProvider(priceProvider).token1() != token1) {
        revert PriceProviderTokenMismatch();
    }
}
``` [1](#0-0) 

The check is trivially satisfied by any contract that hard-codes the correct token addresses in `token0()`/`token1()`. No verification is performed that the provider:
- Is deployed by `AnchoredProviderFactory` (i.e., `isProvider()` returns `true`)
- Enforces `MAX_REF_STALENESS`, `MAX_SPREAD_BPS`, or `minMargin`
- Calls through the attributed `oracle.price(feedId, pool)` path
- Applies any `priceGuard` bounds

The pool admin invokes this path via `proposePoolPriceProvider` followed by `executePoolPriceProviderUpdate`:

```solidity
function proposePoolPriceProvider(address pool, address newPriceProvider)
    external override nonReentrant onlyPoolAdmin(pool)
{
    ...
    _validatePriceProvider(p.token0, p.token1, newPriceProvider);
    uint256 executeAfter = block.timestamp + timelock;
    pendingPriceProvider[pool] = newPriceProvider;
    pendingPriceProviderExecuteAfter[pool] = executeAfter;
    ...
}
``` [2](#0-1) 

```solidity
function executePoolPriceProviderUpdate(address pool) external override nonReentrant onlyPoolAdmin(pool) {
    ...
    if (block.timestamp < execAfter) revert PriceProviderTimelockNotElapsed(execAfter, block.timestamp);
    _validatePriceProvider(p.token0, p.token1, pending);
    IMetricOmmPoolFactoryActions(pool).setPriceProvider(pending);
    ...
}
``` [3](#0-2) 

The pool then calls `getBidAndAskPrice()` on whatever address is stored in `priceProvider` during every swap:

```solidity
function _getBidAndAskPriceX64() internal returns (uint128 bidPriceX64, uint128 askPriceX64) {
    address activePriceProvider = _resolvedPriceProvider();
    try IPriceProvider(activePriceProvider).getBidAndAskPrice() returns (uint128 bid, uint128 ask) {
        if (bid >= ask) revert BidGreaterThanAsk();
        if (bid == 0) revert BidIsZero();
        return (bid, ask);
    } catch (bytes memory reason) {
        revert PriceProviderFailed(reason);
    }
}
``` [4](#0-3) 

The only pool-level checks on the returned price are `bid >= ask` and `bid == 0`. Any value that passes those two checks is used verbatim to compute swap amounts.

Additionally, `createPool` imposes no minimum on `priceProviderTimelock`:

```solidity
bool immutablePriceProvider = params.priceProviderTimelock == type(uint256).max;
``` [5](#0-4) 

A pool created with `priceProviderTimelock = 0` allows the pool admin to propose and execute a provider swap in the same block, giving LPs zero time to react.

### Impact Explanation

After the timelock elapses (or immediately if it is zero), the pool admin activates a malicious provider that returns an extreme bid/ask (e.g., bid = 1, ask = 2 in Q64 units). Every subsequent swap executes at that fabricated price. Traders receive far more output than the real oracle permits, draining LP balances. Alternatively, the malicious provider can quote a price that is inverted relative to the real market, causing LPs to sell token0 at a fraction of its value. Both paths result in direct, unrecoverable loss of LP principal — matching the "bad-price execution" and "pool insolvency" impact categories.

### Likelihood Explanation

The pool admin is a semi-trusted role: the factory explicitly caps their fee authority (`maxAdminSpreadFeeE6`, `maxAdminNotionalFeeE8`) but places no equivalent cap on the oracle they may install. Any pool admin who turns adversarial after LPs have deposited can execute this attack. The attack is more likely when `priceProviderTimelock = 0` (no on-chain warning period) and when the pool has accumulated significant TVL. [6](#0-5) 

### Recommendation

Add a factory-level check in `_validatePriceProvider` that the proposed provider is registered in an approved `AnchoredProviderFactory` (or equivalent allowlist):

```solidity
function _validatePriceProvider(address token0, address token1, address priceProvider) internal view {
    if (priceProvider == address(0)) revert InvalidPriceProvider();
    if (IPriceProvider(priceProvider).token0() != token0 || IPriceProvider(priceProvider).token1() != token1)
        revert PriceProviderTokenMismatch();
    // NEW: require the provider to be from an approved factory
    if (approvedProviderFactory != address(0) &&
        !IAnchoredProviderFactory(approvedProviderFactory).isProvider(priceProvider))
        revert ProviderNotApproved();
}
```

Additionally, enforce a minimum non-zero `priceProviderTimelock` for mutable-provider pools so LPs always have an on-chain withdrawal window before a new provider becomes active.

### Proof of Concept

1. Pool is deployed with `priceProviderTimelock = 0` and a legitimate `AnchoredPriceProvider` (clamp-bounded, staleness-checked).
2. LPs deposit, accumulating TVL.
3. Pool admin deploys `MaliciousProvider` implementing `IPriceProvider`:
   - `token0()` returns the pool's `TOKEN0`
   - `token1()` returns the pool's `TOKEN1`
   - `getBidAndAskPrice()` returns `(1, 2)` — an extreme price far from market
4. Pool admin calls `proposePoolPriceProvider(pool, address(maliciousProvider))`.
   - `_validatePriceProvider` passes (token pair matches).
   - `executeAfter = block.timestamp + 0 = block.timestamp`.
5. Pool admin immediately calls `executePoolPriceProviderUpdate(pool)`.
   - `block.timestamp < block.timestamp` is false → timelock check passes.
   - `setPriceProvider(maliciousProvider)` is called on the pool.
6. Pool admin (or a colluding trader) calls `swap(zeroForOne=true, amountSpecified=large)`.
   - `_getBidAndAskPriceX64()` returns `(1, 2)`.
   - Swap math computes output based on this fabricated price.
   - Trader receives far more `token1` than the real oracle price permits.
   - LP balances are drained.

### Citations

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L164-164)
```text
    bool immutablePriceProvider = params.priceProviderTimelock == type(uint256).max;
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L408-435)
```text
  function setPoolAdminFees(address pool, uint24 newAdminSpreadFeeE6, uint24 newAdminNotionalFeeE8)
    external
    override
    nonReentrant
    onlyPoolAdmin(pool)
  {
    if (newAdminSpreadFeeE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
    if (newAdminNotionalFeeE8 > maxAdminNotionalFeeE8) revert AdminFeeTooHigh();

    PoolFeeConfig memory c = poolFeeConfig[pool];
    IMetricOmmPoolCollectFees(pool)
      .collectFees(
        c.protocolSpreadFeeE6,
        c.adminSpreadFeeE6,
        c.protocolNotionalFeeE8,
        c.adminNotionalFeeE8,
        poolAdminFeeDestination[pool]
      );

    c.adminSpreadFeeE6 = newAdminSpreadFeeE6;
    c.adminNotionalFeeE8 = newAdminNotionalFeeE8;
    poolFeeConfig[pool] = c;

    IMetricOmmPoolFactoryActions(pool)
      .setPoolFees(c.protocolSpreadFeeE6 + c.adminSpreadFeeE6, c.protocolNotionalFeeE8 + c.adminNotionalFeeE8);
    emit PoolAdminSpreadFeeUpdated(pool, newAdminSpreadFeeE6);
    emit PoolAdminNotionalFeeUpdated(pool, newAdminNotionalFeeE8);
  }
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L474-491)
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
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L494-507)
```text
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

**File:** metric-core/contracts/MetricOmmPool.sol (L804-813)
```text
  function _getBidAndAskPriceX64() internal returns (uint128 bidPriceX64, uint128 askPriceX64) {
    address activePriceProvider = _resolvedPriceProvider();
    try IPriceProvider(activePriceProvider).getBidAndAskPrice() returns (uint128 bid, uint128 ask) {
      if (bid >= ask) revert BidGreaterThanAsk();
      if (bid == 0) revert BidIsZero();
      return (bid, ask);
    } catch (bytes memory reason) {
      revert PriceProviderFailed(reason);
    }
  }
```
