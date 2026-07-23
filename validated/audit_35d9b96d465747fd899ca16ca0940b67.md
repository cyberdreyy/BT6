### Title
`addLiquidity()` and `removeLiquidity()` Missing `whenNotPaused` Modifier Allows Liquidity Operations During Paused State - (File: `metric-core/contracts/MetricOmmPool.sol`)

---

### Summary

`MetricOmmPool.swap()` correctly enforces `whenNotPaused`, but `addLiquidity()` and `removeLiquidity()` carry no such guard. Both functions execute full state-changing liquidity operations while the pool is paused at any level (`pauseLevel == 1` or `pauseLevel == 2`), defeating the incident-response purpose of the pause mechanism.

---

### Finding Description

`MetricOmmPool` defines a three-level pause system: [1](#0-0) 

- `0` = active, `1` = paused by pool admin, `2` = paused by protocol.

The `whenNotPaused` modifier and its check are defined: [2](#0-1) [3](#0-2) 

`swap()` correctly applies it: [4](#0-3) 

But `addLiquidity()` and `removeLiquidity()` do not: [5](#0-4) [6](#0-5) 

Both functions mutate `binTotals`, `_binStates`, `_binTotalShares`, and `_positionBinShares` unconditionally regardless of `pauseLevel`.

---

### Impact Explanation

The pause mechanism exists to freeze pool state during an incident (oracle compromise, accounting anomaly, exploit in progress). Blocking `swap()` alone is insufficient if `removeLiquidity()` remains open:

1. An incident is detected; admin or protocol pauses the pool (`pauseLevel = 1` or `2`).
2. Swaps are blocked. However, any LP — including an attacker who triggered the incident — can immediately call `removeLiquidity()` and withdraw their share of both tokens.
3. If the incident involves an accounting discrepancy (e.g., `binTotals` diverged from actual balances due to a bug), the first LP to exit receives a disproportionate share, leaving remaining LPs with claims that exceed the pool's actual token balances — **pool insolvency**.
4. `addLiquidity()` being unguarded allows an attacker to inject liquidity during a paused state to manipulate share ratios before the pool is unpaused, front-running honest LPs.

This is a direct broken-invariant: the protocol's own documentation states `pauseLevel != 0` means the pool is paused, yet two of the three core state-changing user-facing functions ignore it entirely.

---

### Likelihood Explanation

- Any pool admin or the protocol owner can pause a pool; pausing is a normal operational action.
- Once paused, any LP (no special role required) can call `removeLiquidity()` immediately.
- The attacker only needs to hold LP shares in the pool — a normal, unprivileged position.
- No flash loan or complex setup is required.

---

### Recommendation

Add `whenNotPaused` to both functions, mirroring the pattern already used on `swap()`:

```solidity
function addLiquidity(...) external whenNotPaused nonReentrant(PoolActions.ADD_LIQUIDITY) ...

function removeLiquidity(...) external whenNotPaused nonReentrant(PoolActions.REMOVE_LIQUIDITY) ...
```

If the protocol intentionally wants to allow LP exits during a pause (a common design choice), only `removeLiquidity()` should be exempted and this decision must be explicitly documented; `addLiquidity()` should always be blocked when paused.

---

### Proof of Concept

```solidity
// 1. Pool is active; attacker adds liquidity normally.
pool.addLiquidity(attacker, salt, deltas, callbackData, "");

// 2. Incident detected; admin pauses the pool.
factory.pausePool(address(pool)); // pauseLevel = 1

// 3. swap() now reverts with PoolPaused().
// pool.swap(...) → reverts ✓

// 4. But removeLiquidity() succeeds — no whenNotPaused guard.
pool.removeLiquidity(attacker, salt, exitDeltas, "");
// → attacker withdraws full share while pool is paused,
//   leaving remaining LPs with insolvent claims if binTotals
//   diverged from actual balances during the incident.
``` [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L71-72)
```text
  /// @dev 0 = active, 1 = paused by admin, 2 = paused by protocol. Transitions enforced by factory.
  uint8 internal pauseLevel;
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
