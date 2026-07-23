### Title
Zero `priceProviderTimelock` Allows Pool Admin to Atomically Swap Oracle With No Delay — (`metric-core/contracts/MetricOmmPoolFactory.sol`)

### Summary

`MetricOmmPoolFactory` enforces a timelock between `proposePoolPriceProvider` and `executePoolPriceProviderUpdate` to give LPs time to exit before an oracle change takes effect. When `priceProviderTimelock[pool]` is zero — a value the factory accepts without restriction — the timelock check `block.timestamp < execAfter` evaluates to `block.timestamp < block.timestamp`, which is always `false`. The revert never fires, and the pool admin can propose and execute an oracle replacement atomically in the same block, feeding a malicious price provider into the pool with no warning.

### Finding Description

`proposePoolPriceProvider` computes the execution deadline as:

```solidity
uint256 executeAfter = block.timestamp + timelock;   // timelock == 0 → executeAfter == block.timestamp
```

`executePoolPriceProviderUpdate` then enforces:

```solidity
if (block.timestamp < execAfter) revert PriceProviderTimelockNotElapsed(execAfter, block.timestamp);
```

When `timelock == 0`, `execAfter == block.timestamp`, so `block.timestamp < block.timestamp` is `false` and the guard is silently skipped. Both calls can be batched in a single transaction (e.g., via `Multicall` or a wrapper contract), replacing the live oracle with an attacker-controlled one before any LP can react.

`_validatePoolParameters` imposes no lower bound on `priceProviderTimelock`; the only special value is `type(uint256).max` (immutable mode). Any value from `0` to `type(uint256).max - 1` is accepted and stored verbatim. [1](#0-0) [2](#0-1) [3](#0-2) 

### Impact Explanation

A pool admin who created the pool with `priceProviderTimelock = 0` can, at any time after LP deposits accumulate:

1. Deploy a malicious `IPriceProvider` that returns an arbitrarily skewed bid/ask (e.g., bid = 0, ask = `type(uint128).max`).
2. In one transaction: call `proposePoolPriceProvider(pool, maliciousProvider)` then `executePoolPriceProviderUpdate(pool)`.
3. Immediately execute a swap against the pool at the manipulated price, extracting LP principal.

The pool's swap math reads the oracle price once per swap and uses it for all bin computations. A sufficiently extreme price causes the pool to hand the attacker far more output tokens than the input is worth, directly draining LP balances. This matches the allowed impact: **bad-price execution** and **LP principal loss**. [4](#0-3) [5](#0-4) 

### Likelihood Explanation

Any pool creator can set `priceProviderTimelock = 0` at deployment — the factory accepts it without complaint. The pool appears legitimate (it has a real oracle, real tokens, real fees). Once LPs deposit, the admin can execute the attack in a single block. No off-chain monitoring can react in time because the propose and execute happen atomically. The trigger is a valid pool admin action, not a privileged factory-owner action, so it is reachable by any pool deployer. [6](#0-5) 

### Recommendation

Enforce a protocol-level minimum timelock in `_validatePoolParameters`:

```solidity
uint256 public constant MIN_PRICE_PROVIDER_TIMELOCK = 1 days;

// inside _validatePoolParameters:
if (params.priceProviderTimelock != type(uint256).max
    && params.priceProviderTimelock < MIN_PRICE_PROVIDER_TIMELOCK) {
    revert TimelockTooShort();
}
```

Alternatively, change the guard in `executePoolPriceProviderUpdate` to `<=` so that `block.timestamp == execAfter` still reverts, and require at least 1 second of delay at proposal time. [7](#0-6) 

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Attacker deploys pool with priceProviderTimelock = 0 (mutable, zero delay)
PoolParameters memory params = ...;
params.priceProviderTimelock = 0;          // NOT type(uint256).max → mutable mode, zero delay
address pool = factory.createPool(params); // accepted without revert

// LPs deposit over time...

// Attacker (pool admin) atomically swaps oracle in one tx:
MaliciousOracle evil = new MaliciousOracle(); // returns bid=0, ask=type(uint128).max
evil.setTokens(token0, token1);

factory.proposePoolPriceProvider(pool, address(evil));
// executeAfter = block.timestamp + 0 = block.timestamp
// block.timestamp < block.timestamp → false → no revert

factory.executePoolPriceProviderUpdate(pool);
// oracle is now evil, effective immediately

// Attacker swaps at manipulated price, draining LP funds
pool.swap(...);
``` [8](#0-7)

### Citations

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L164-164)
```text
    bool immutablePriceProvider = params.priceProviderTimelock == type(uint256).max;
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L213-213)
```text
    priceProviderTimelock[pool] = params.priceProviderTimelock;
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L481-507)
```text
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

**File:** metric-core/docs/POOL_CONFIGURATION_AND_MANAGEMENT.md (L36-36)
```markdown
| **`priceProviderTimelock`** | If **`type(uint256).max`**, the pool treats the oracle as **immutable** (`IMMUTABLE_PRICE_PROVIDER` is set; no rotations). Otherwise, seconds to wait after `proposePoolPriceProvider` before `executePoolPriceProviderUpdate`, stored in **`priceProviderTimelock[pool]`**. | Use `type(uint256).max` only when you want the oracle address fixed forever. For rotatable oracles, pick a finite delay that balances security and operations. |
```
