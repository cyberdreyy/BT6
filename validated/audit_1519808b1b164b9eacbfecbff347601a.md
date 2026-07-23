### Title
Immutable Extension Hooks With No Emergency Bypass Can Permanently Brick Pool Swap and Liquidity Withdrawal — (File: `metric-core/contracts/ExtensionCalling.sol`)

---

### Summary

Extension addresses and call-order bitmaps are stored as **immutable** constructor arguments in `ExtensionCalling.sol`. If any registered extension enters a permanent-revert state (e.g., a stop-loss oracle goes stale, a price-velocity guard trips indefinitely, or an extension admin key is compromised), every pool operation that invokes that hook reverts with no recovery path. LP funds become permanently unwithdrawable.

---

### Finding Description

`ExtensionCalling` stores up to seven extension addresses and six call-order bitmaps as Solidity `immutable` variables set once at pool construction: [1](#0-0) 

The dispatcher `_callExtensionsInOrder` iterates the packed bitmap and calls each extension unconditionally via `CallExtension.callExtension`: [2](#0-1) 

There is **no try/catch**, no skip-on-revert flag, and no admin function to disable or replace an extension after deployment. A revert inside any extension propagates directly to the caller.

This affects all six hook points: [3](#0-2) 

The periphery ships production extensions that are designed to revert under market conditions:

- `OracleValueStopLossExtension` — reverts when oracle price crosses a threshold
- `PriceVelocityGuardExtension` — reverts when price moves too fast



If either of these is registered on `BEFORE_REMOVE_LIQUIDITY_ORDER` or `AFTER_REMOVE_LIQUIDITY_ORDER` and its trigger condition becomes permanent (oracle feed goes stale, price stays below stop-loss, or the extension's own admin pauses it), LPs can never call `removeLiquidity` successfully.

---

### Impact Explanation

- **Direct loss of user principal**: LP shares represent real token balances. If `beforeRemoveLiquidity` or `afterRemoveLiquidity` permanently reverts, those tokens are locked in the pool contract forever.
- **Broken core pool functionality**: Swap operations are equally bricked if the extension is on `BEFORE_SWAP_ORDER` or `AFTER_SWAP_ORDER`, making the pool completely unusable.
- This matches the allowed impact gate: *"Broken core pool functionality causing loss of funds or unusable withdraw/swap/liquidity flows."*

---

### Likelihood Explanation

- **Medium**. The trigger does not require a malicious actor. It requires only that a legitimately deployed extension (stop-loss, velocity guard) enters a state where it permanently reverts — a realistic scenario given oracle staleness, extreme market volatility, or an extension admin revoking access.
- Pool creation is permissionless; any LP who deposits into a pool with such an extension is exposed without necessarily understanding the risk.
- The factory's `ValidateExtensionsConfig.sol` validates structural correctness of the extension config (non-zero addresses, valid order encoding) but cannot validate the runtime behavior of extension logic. [4](#0-3) 

---

### Recommendation

1. **Emergency extension bypass**: Add a pool-admin-controlled flag (behind a timelock) that can disable a specific extension slot, analogous to a circuit breaker.
2. **Try/catch with configurable failure mode**: Wrap `CallExtension.callExtension` in a try/catch and allow the pool deployer to declare each extension as `REQUIRED` (revert on failure) or `OPTIONAL` (skip on failure).
3. **Upgradeable extension registry**: Instead of immutable addresses, store extensions in a governance-controlled mapping so a broken extension can be replaced.

---

### Proof of Concept

1. Pool is deployed with `OracleValueStopLossExtension` registered on both `BEFORE_SWAP_ORDER` and `BEFORE_REMOVE_LIQUIDITY_ORDER`.
2. The oracle feed used by the stop-loss extension goes stale or the price permanently crosses the stop-loss threshold.
3. Every call to `swap()` hits `_beforeSwap` → `_callExtensionsInOrder(BEFORE_SWAP_ORDER, ...)` → `callExtension(stopLossExtension, ...)` → **revert**.
4. Every call to `removeLiquidity()` hits `_beforeRemoveLiquidity` → same path → **revert**.
5. LP tokens are permanently stranded. There is no admin function, no emergency exit, and no bypass path in `ExtensionCalling.sol` or `MetricOmmPool.sol`. [5](#0-4)

### Citations

**File:** metric-core/contracts/ExtensionCalling.sol (L17-35)
```text
  address internal immutable EXTENSION_1;
  address internal immutable EXTENSION_2;
  address internal immutable EXTENSION_3;
  address internal immutable EXTENSION_4;
  address internal immutable EXTENSION_5;
  address internal immutable EXTENSION_6;
  address internal immutable EXTENSION_7;
  /// @dev Order of extension calls for before add liquidity.
  uint256 internal immutable BEFORE_ADD_LIQUIDITY_ORDER;
  /// @dev Order of extension calls for after add liquidity.
  uint256 internal immutable AFTER_ADD_LIQUIDITY_ORDER;
  /// @dev Order of extension calls for before remove liquidity.
  uint256 internal immutable BEFORE_REMOVE_LIQUIDITY_ORDER;
  /// @dev Order of extension calls for after remove liquidity.
  uint256 internal immutable AFTER_REMOVE_LIQUIDITY_ORDER;
  /// @dev Order of extension calls for before swap.
  uint256 internal immutable BEFORE_SWAP_ORDER;
  /// @dev Order of extension calls for after swap.
  uint256 internal immutable AFTER_SWAP_ORDER;
```

**File:** metric-core/contracts/ExtensionCalling.sol (L75-86)
```text
  function _callExtensionsInOrder(uint256 order, bytes memory data) private {
    if (order == 0) return;

    while (true) {
      uint256 extensionIndex = order & 0x7;
      if (extensionIndex == 0) break;
      address extension = _extensionAddress(extensionIndex);
      if (extension == address(0)) revert PanicEmptyExtension();
      CallExtension.callExtension(extension, data);
      order >>= 3;
    }
  }
```

**File:** metric-core/contracts/ExtensionCalling.sol (L88-147)
```text
  function _beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
  }

  function _afterAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 amount0Added,
    uint256 amount1Added,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      AFTER_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.afterAddLiquidity, (sender, owner, salt, deltas, amount0Added, amount1Added, extensionData)
      )
    );
  }

  function _beforeRemoveLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_REMOVE_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeRemoveLiquidity, (sender, owner, salt, deltas, extensionData))
    );
  }

  function _afterRemoveLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 amount0Removed,
    uint256 amount1Removed,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      AFTER_REMOVE_LIQUIDITY_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.afterRemoveLiquidity,
        (sender, owner, salt, deltas, amount0Removed, amount1Removed, extensionData)
      )
    );
  }
```

**File:** metric-core/contracts/libraries/ValidateExtensionsConfig.sol (L1-5)
```text
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

import {ExtensionOrders} from "../types/PoolExtensionsConfig.sol";

```
