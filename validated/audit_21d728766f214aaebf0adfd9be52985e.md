### Title
`MetricOmmPool::addLiquidity` Missing `whenNotPaused` Modifier Allows Deposits Into a Paused Pool — (`metric-core/contracts/MetricOmmPool.sol`)

### Summary

`MetricOmmPool` defines a `whenNotPaused` modifier and a three-level `pauseLevel` state variable, but only applies the modifier to `swap()`. The `addLiquidity()` function — a principal-bearing, state-changing entry point — is not guarded, so pausing the pool does not prevent new LP deposits.

### Finding Description

`MetricOmmPool` inherits a custom pause system: `pauseLevel` can be set to `0` (active), `1` (admin-paused), or `2` (protocol-paused) via `MetricOmmPoolFactory.pausePool()` / `protocolPausePool()`. The pool exposes a `whenNotPaused` modifier that reverts when `pauseLevel != 0`. [1](#0-0) 

`swap()` correctly carries the modifier: [2](#0-1) 

`addLiquidity()` does not: [3](#0-2) 

`removeLiquidity()` also lacks the modifier, but that is defensible — LPs must always be able to exit. `addLiquidity()` has no such justification: it accepts new token deposits and mints new shares, permanently altering `binTotals`, `_binStates`, and `_positionBinShares` even while the pool is paused. [4](#0-3) 

### Impact Explanation

When a pool is paused — the expected response to an oracle anomaly, price-guard breach, or active exploit — the operator's intent is to freeze all value-bearing state transitions until the root cause is resolved. Because `addLiquidity()` is unguarded, any address can deposit tokens into the compromised pool during the pause window. Those tokens are immediately credited to bins and tracked in `binTotals`. If the pool is later unpaused while the underlying issue persists (e.g., a bad oracle price that the price-guard did not catch in time), the newly deposited principal is exposed to the same exploit that triggered the pause. This is a direct loss of user principal with no recovery path — the depositor cannot distinguish a "maintenance pause" from a "security pause" on-chain.

### Likelihood Explanation

The pool admin or protocol owner must have already paused the pool (a privileged step), but once paused, the trigger for the loss is entirely unprivileged: any user calling `addLiquidity()` with a non-zero delta is sufficient. Users who observe a paused pool and assume deposits are safe (because swaps revert) are the natural victims. The scenario is realistic whenever a pool is paused for a non-trivial duration.

### Recommendation

Add `whenNotPaused` to `addLiquidity()`:

```solidity
function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
) external whenNotPaused nonReentrant(PoolActions.ADD_LIQUIDITY) returns (...) {
```

`removeLiquidity()` should remain unguarded so LPs can always exit.

### Proof of Concept

1. Deploy a pool with a mutable price provider.
2. Pool admin calls `MetricOmmPoolFactory.pausePool(pool)` → `pauseLevel = 1`.
3. Verify `pool.swap(...)` reverts with `PoolPaused`.
4. Call `pool.addLiquidity(owner, salt, deltas, ...)` with a non-zero delta — **succeeds**, tokens are transferred in, `binTotals.scaledToken0/1` increases, shares are minted.
5. Pool admin calls `unpausePool(pool)` → `pauseLevel = 0`.
6. If the reason for the pause was an exploitable oracle state, the newly deposited tokens are now at risk in the next swap. [5](#0-4) [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L79-98)
```text
  // Slot 1 ordering (from left to right):
  //   [16bytes binTotals.scaledToken1] [16bytes binTotals.scaledToken0]
  BinTotals internal binTotals;

  // Slot 2 ordering (from left to right):
  //   [16bytes notionalFeeToken1Scaled] [16bytes notionalFeeToken0Scaled]
  uint128 internal notionalFeeToken0Scaled;
  uint128 internal notionalFeeToken1Scaled;

  // Slot 3 ordering (from left to right):
  //   [16bytes unused] [20 bytes priceProvider]
  /// @dev The price provider address - only used when `IMMUTABLE_PRICE_PROVIDER == address(0)`
  address internal priceProvider;

  mapping(int256 => BinState) internal _binStates;

  // ++++++++++ Unused when swapping ++++++++
  mapping(int256 => uint256) internal _binTotalShares;
  /// @dev Per-bin position shares keyed by `_positionBinKey`.
  mapping(bytes32 => uint256) internal _positionBinShares;
```

**File:** metric-core/contracts/MetricOmmPool.sol (L174-177)
```text
  modifier whenNotPaused() {
    _checkNotPaused();
    _;
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L182-196)
```text
  function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L217-224)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
```

**File:** metric-core/contracts/MetricOmmPool.sol (L643-645)
```text
  function _checkNotPaused() internal view {
    if (pauseLevel != 0) revert PoolPaused();
  }
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L460-464)
```text
  function pausePool(address pool) external override nonReentrant onlyPoolAdmin(pool) {
    (uint8 cur,,,,,) = PoolStateLibrary._slot0(pool);
    if (cur != 0) revert InvalidPauseTransition(cur, 1);
    IMetricOmmPoolFactoryActions(pool).setPause(1);
  }
```
