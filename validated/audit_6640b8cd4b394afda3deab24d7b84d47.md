### Title
`addLiquidity` Lacks Pause Guard, Allowing Token Deposits Into a Paused Pool - (File: `metric-core/contracts/MetricOmmPool.sol`)

---

### Summary

`MetricOmmPool.addLiquidity` does not apply the `whenNotPaused` modifier, so users can deposit tokens into a pool that has been paused by the pool admin or protocol — directly analogous to the CCMPSendMessageFacet `addGasFee` bug where deposits were accepted despite a paused gateway.

---

### Finding Description

`MetricOmmPool` exposes three user-facing state-changing functions:

| Function | Pause guard |
|---|---|
| `swap()` | `whenNotPaused` ✓ |
| `addLiquidity()` | **none** ✗ |
| `removeLiquidity()` | **none** (intentional for exits) |

`swap` is correctly gated: [1](#0-0) 

`_checkNotPaused` reverts whenever `pauseLevel != 0`: [2](#0-1) 

But `addLiquidity` carries no such guard: [3](#0-2) 

The pool supports two pause levels set by the factory:

- `pauseLevel = 1` — pool admin pause (e.g., oracle anomaly, adaptor security issue)
- `pauseLevel = 2` — protocol pause (e.g., systemic risk) [4](#0-3) 

Both are reachable by non-privileged-relative actors (pool admin is a semi-trusted role, protocol owner is trusted but the pause itself is a legitimate operational event, not a malicious setup assumption). [5](#0-4) 

---

### Impact Explanation

When a pool is paused because of an oracle manipulation event, a compromised price provider, or a security incident with an extension, the pause is intended to freeze all pool activity. However, because `addLiquidity` is unguarded:

1. Users (or bots) can still deposit `token0`/`token1` into the paused pool.
2. The deposited tokens are immediately accounted in `binTotals` and `_binStates`.
3. When the pool is later unpaused — possibly with a corrected but still-unfavorable oracle price — those freshly deposited tokens are exposed to adversarial swaps at the first available block, causing direct loss of user principal.

This satisfies the **direct loss of user principal** impact gate: tokens deposited during the pause window are at risk the moment the pool resumes.

---

### Likelihood Explanation

- Pool pauses are a normal operational response to oracle anomalies or security events (the factory explicitly provides `pausePool` / `protocolPausePool`).
- Users unaware of the pause (or racing to add liquidity before a known event) will call `addLiquidity` during the pause window.
- No special attacker capability is required; any user can trigger the loss by simply adding liquidity while the pool is paused.

Likelihood: **Medium** (pause events are infrequent but the window is fully open when they occur).

---

### Recommendation

Apply `whenNotPaused` to `addLiquidity`, mirroring the guard already on `swap`:

```solidity
function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
) external whenNotPaused nonReentrant(PoolActions.ADD_LIQUIDITY) returns (...) {
```

`removeLiquidity` should intentionally remain unguarded so LPs can always exit a paused pool.

---

### Proof of Concept

1. Pool admin calls `MetricOmmPoolFactory.pausePool(pool)` → `pauseLevel = 1` due to a suspected oracle issue.
2. Alice calls `MetricOmmPool.addLiquidity(...)` depositing 10,000 USDC. The call succeeds — no revert — because `addLiquidity` has no `whenNotPaused` check.
3. Pool admin investigates, concludes the oracle is safe, calls `unpausePool(pool)` → `pauseLevel = 0`.
4. An MEV bot immediately calls `swap(...)` at the (potentially still-stale or manipulated) oracle price, draining value from Alice's newly deposited position.
5. Alice loses principal she deposited during the pause window, which the pause was specifically intended to protect against. [3](#0-2) [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L71-72)
```text
  /// @dev 0 = active, 1 = paused by admin, 2 = paused by protocol. Transitions enforced by factory.
  uint8 internal pauseLevel;
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

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L460-471)
```text
  function pausePool(address pool) external override nonReentrant onlyPoolAdmin(pool) {
    (uint8 cur,,,,,) = PoolStateLibrary._slot0(pool);
    if (cur != 0) revert InvalidPauseTransition(cur, 1);
    IMetricOmmPoolFactoryActions(pool).setPause(1);
  }

  /// @inheritdoc IMetricOmmPoolFactoryPoolAdmin
  function unpausePool(address pool) external override nonReentrant onlyPoolAdmin(pool) {
    (uint8 cur,,,,,) = PoolStateLibrary._slot0(pool);
    if (cur != 1) revert InvalidPauseTransition(cur, 0);
    IMetricOmmPoolFactoryActions(pool).setPause(0);
  }
```
