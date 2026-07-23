### Title
Removed liquidity tokens are never transferred to the position owner — (`File: metric-core/contracts/MetricOmmPool.sol`)

### Summary

`MetricOmmPool.removeLiquidity()` updates internal accounting and computes `amount0Removed` / `amount1Removed` but contains no `transferToken0` / `transferToken1` call to deliver those tokens to the owner. The tokens remain permanently locked in the pool contract.

### Finding Description

The `removeLiquidity` function burns the caller's bin shares via `LiquidityLib.removeLiquidity`, which updates `binTotals`, `_binStates`, `_binTotalShares`, and `_positionBinShares` in storage and returns the owed external token amounts. However, unlike every other outbound-payment path in the contract, no transfer to the owner follows. [1](#0-0) 

Compare with `collectFees`, which explicitly calls `transferToken0` / `transferToken1` for every non-zero amount: [2](#0-1) 

And `swap`, which transfers the output token to the recipient before invoking the callback: [3](#0-2) 

`LiquidityLib.removeLiquidity` receives only accounting-level storage references (`binTotals`, `_binStates`, `_binTotalShares`, `_positionBinShares`) and a `PoolContext` struct containing scale multipliers and bin bounds — no transfer mechanism. The library is structurally incapable of pushing tokens to the owner. [4](#0-3) 

The `addLiquidity` counterpart correctly pulls tokens from the user via `metricOmmModifyLiquidityCallback` → `pay()` in `MetricOmmPoolLiquidityAdder`: [5](#0-4) 

There is no symmetric push path for `removeLiquidity`.

### Impact Explanation

Any LP who calls `removeLiquidity` has their position shares permanently burned and their pro-rata token0/token1 entitlement permanently locked inside the pool. The pool's `binTotals` accounting is decremented (tokens are no longer attributed to any LP), yet the pool's ERC-20 balances are unchanged. This is a direct, irrecoverable loss of user principal with no recovery path — the pool holds the tokens but no accounting entry credits them to anyone.

### Likelihood Explanation

Every LP who removes liquidity triggers this path. The function is permissionlessly callable by any position owner (`msg.sender == owner` check passes for any legitimate LP). No special conditions, price manipulation, or privileged access is required. Every `removeLiquidity` call results in total loss of the withdrawn amount.

### Recommendation

Add explicit token transfers to the owner immediately after `LiquidityLib.removeLiquidity` returns, mirroring the pattern used in `collectFees` and `swap`:

```solidity
(amount0Removed, amount1Removed) = LiquidityLib.removeLiquidity(
    _liquidityContext(), owner, salt, deltas, binTotals, _binStates, _binTotalShares, _positionBinShares
);
// ADD:
if (amount0Removed > 0) transferToken0(owner, amount0Removed);
if (amount1Removed > 0) transferToken1(owner, amount1Removed);
_afterRemoveLiquidity(msg.sender, owner, salt, deltas, amount0Removed, amount1Removed, extensionData);
```

### Proof of Concept

1. Alice adds liquidity via `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, salt, deltas, ...)`. The callback pulls 1000 token0 and 500 token1 from Alice into the pool. Alice's position shares are recorded in `_positionBinShares`.
2. Alice calls `pool.removeLiquidity(alice, salt, deltas, "")` directly (she is the owner).
3. `LiquidityLib.removeLiquidity` burns Alice's shares, decrements `binTotals.scaledToken0` and `binTotals.scaledToken1`, and returns `amount0Removed = 1000`, `amount1Removed = 500`.
4. The function emits no transfer, calls no `transferToken0`/`transferToken1`, and returns `(1000, 500)` to the caller.
5. Alice's token balances are unchanged. The 1000 token0 and 500 token1 remain in the pool with no accounting owner — permanently locked.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L199-212)
```text
  function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, bytes calldata extensionData)
    external
    nonReentrant(PoolActions.REMOVE_LIQUIDITY)
    returns (uint256 amount0Removed, uint256 amount1Removed)
  {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    if (msg.sender != owner) revert NotPositionOwner();
    _beforeRemoveLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Removed, amount1Removed) = LiquidityLib.removeLiquidity(
      _liquidityContext(), owner, salt, deltas, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterRemoveLiquidity(msg.sender, owner, salt, deltas, amount0Removed, amount1Removed, extensionData);
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L250-278)
```text
    if (zeroForOne) {
      if (amount1Delta < 0) {
        // casting to uint256 is safe because amount1Delta is negative and the ammount of tokens in pool is capped by uint128.max
        // forge-lint: disable-next-line(unsafe-typecast)
        transferToken1(recipient, uint256(-amount1Delta));
      }

      uint256 balance0Before = balance0();
      IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(amount0Delta, amount1Delta, callbackData);
      // casting to uint256 is safe because amount0Delta is positive and the ammount of tokens in pool is capped by uint128.max
      // forge-lint: disable-next-line(unsafe-typecast)
      if (amount0Delta > 0 && balance0Before + uint256(amount0Delta) > balance0()) {
        revert IncorrectDelta();
      }
    } else {
      if (amount0Delta < 0) {
        // casting to uint256 is safe because amount0Delta is negative and the ammount of tokens in pool is capped by uint128.max
        // forge-lint: disable-next-line(unsafe-typecast)
        transferToken0(recipient, uint256(-amount0Delta));
      }

      uint256 balance1Before = balance1();
      IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(amount0Delta, amount1Delta, callbackData);
      // casting to uint256 is safe because amount1Delta is positive and the ammount of tokens in pool is capped by uint128.max
      // forge-lint: disable-next-line(unsafe-typecast)
      if (amount1Delta > 0 && balance1Before + uint256(amount1Delta) > balance1()) {
        revert IncorrectDelta();
      }
    }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L416-427)
```text
      if (totalFee0ToAdmin > 0) {
        transferToken0(adminFeeDestination_, totalFee0ToAdmin);
      }
      if (totalFee1ToAdmin > 0) {
        transferToken1(adminFeeDestination_, totalFee1ToAdmin);
      }
      if (totalFee0ToProtocol > 0) {
        transferToken0(FACTORY, totalFee0ToProtocol);
      }
      if (totalFee1ToProtocol > 0) {
        transferToken1(FACTORY, totalFee1ToProtocol);
      }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L172-177)
```text
    if (amount0Delta > 0) {
      pay(token0, payer, msg.sender, amount0Delta);
    }
    if (amount1Delta > 0) {
      pay(token1, payer, msg.sender, amount1Delta);
    }
```
