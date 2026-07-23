### Title
`uint32` Overflow in `_afterTimelock` Allows Pool Admin to Bypass Timelock — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

### Summary
The `_afterTimelock` helper truncates `block.timestamp + timelock` to `uint32`. When the pool admin sets `timelock` to a value large enough that the sum exceeds `type(uint32).max`, the result wraps to a timestamp already in the past. Every subsequent `_requireElapsed` check passes immediately, letting the pool admin execute any pending parameter change (drawdown, decay, watermarks) without waiting for the intended delay.

### Finding Description
`_afterTimelock` is the sole source of `executeAfter` for all four propose-paths in `OracleValueStopLossExtension`:

```solidity
// OracleValueStopLossExtension.sol L297-299
function _afterTimelock(address pool_) private view returns (uint32) {
    return uint32(block.timestamp + oracleStopLossConfig[pool_].timelock);
}
``` [1](#0-0) 

The addition is performed in `uint256` space (because `block.timestamp` is `uint256`), then silently truncated to `uint32`. No cap is enforced on `newTimelock` in `proposeOracleStopLossTimelock`:

```solidity
// L78-84
function proposeOracleStopLossTimelock(address pool_, uint32 newTimelock) external onlyPoolAdmin(pool_) {
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    uint32 executeAfter = _afterTimelock(pool_);
    sched.pendingTimelock = newTimelock;
    sched.pendingTimelockExecuteAfter = executeAfter;
    ...
}
``` [2](#0-1) 

The guard that enforces the delay is:

```solidity
// L301-303
function _requireElapsed(uint32 executeAfter) private view {
    if (block.timestamp < executeAfter) revert OracleStopLossTimelockNotElapsed(...);
}
``` [3](#0-2) 

At `block.timestamp ≈ 1_700_000_000` (current epoch):

```
block.timestamp + type(uint32).max
= 1_700_000_000 + 4_294_967_295
= 5_994_967_295

uint32(5_994_967_295) = 5_994_967_295 mod 2^32 = 1_699_999_999
```

`executeAfter = 1_699_999_999 < block.timestamp = 1_700_000_000` → `_requireElapsed` never reverts.

The same overflow applies to `proposeOracleStopLossDrawdown`, `proposeOracleStopLossDecay`, and `proposeOracleStopLossHighWatermarks`, all of which call `_afterTimelock`: [4](#0-3) [5](#0-4) [6](#0-5) 

### Impact Explanation
The timelock is the only mechanism protecting LPs from immediate pool-admin parameter changes. The contract's own NatSpec states: *"Drawdown and decay changes are timelocked so LPs can react."* [7](#0-6) 

Once the pool admin installs `timelock = type(uint32).max`, every future proposal's `executeAfter` is permanently in the past. The admin can then:

1. Immediately set `drawdownE6 = 0` — disabling the stop-loss entirely, removing LP protection against value extraction.
2. Immediately set watermarks to near-zero — causing `OracleStopLossTriggered` on every normal swap, making the pool unusable (broken core swap functionality).
3. Immediately change `decayPerSecondE8` — accelerating or freezing watermark decay to manipulate when the stop-loss fires.

This matches the allowed impact gate: **Admin-boundary break — pool admin bypasses timelocks** and **broken core pool functionality causing unusable swap flows**.

### Likelihood Explanation
The pool admin is a semi-trusted role whose actions the timelock is specifically designed to constrain. There is no cap or validation on `newTimelock`; any `uint32` value is accepted. A malicious pool admin needs only one transaction (when the current timelock is 0) or one wait period (when it is non-zero) to permanently disable all future timelock enforcement. The threshold for overflow is `timelock > type(uint32).max − block.timestamp ≈ 2.6 billion seconds ≈ 82 years`, which is a valid `uint32` value.

### Recommendation
Add an upper bound on the timelock value before storing it, and use `uint256` for `executeAfter` to avoid silent truncation:

```solidity
uint256 private constant MAX_TIMELOCK = 365 days;

function proposeOracleStopLossTimelock(address pool_, uint32 newTimelock) external onlyPoolAdmin(pool_) {
    require(newTimelock <= MAX_TIMELOCK, TimelockTooLarge());
    ...
}

function _afterTimelock(address pool_) private view returns (uint256) {
    return block.timestamp + oracleStopLossConfig[pool_].timelock;
}
```

Store and compare `executeAfter` as `uint256` throughout the schedule structs, or at minimum assert `block.timestamp + timelock <= type(uint32).max` before truncating.

### Proof of Concept

```solidity
// Assumes pool is initialized with timelock = 0 (or attacker waits for current timelock to expire)

// Step 1: Set timelock to type(uint32).max
vm.prank(poolAdmin);
extension.proposeOracleStopLossTimelock(pool, type(uint32).max);
// _afterTimelock returns uint32(block.timestamp + 0) = block.timestamp (current timelock = 0)
// executeAfter == block.timestamp → _requireElapsed passes immediately

vm.prank(poolAdmin);
extension.executeOracleStopLossTimelock(pool);
// timelock is now type(uint32).max

// Step 2: Immediately disable stop-loss (no waiting)
vm.prank(poolAdmin);
extension.proposeOracleStopLossDrawdown(pool, 0);
// _afterTimelock: uint32(block.timestamp + type(uint32).max) wraps to past value
// executeAfter < block.timestamp → _requireElapsed passes immediately

vm.prank(poolAdmin);
extension.executeOracleStopLossDrawdown(pool);
// drawdownE6 = 0: stop-loss disabled, LPs unprotected
// All future proposals also execute immediately
```

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L13-28)
```text
/// @title OracleValueStopLossExtension
/// @notice Tracks per-bin value per share in token0 and token1 terms at the oracle mid,
///         against decaying high watermarks. Drawdown and decay changes are timelocked so LPs
///         can react; monitor at least as often as the timelock or trust the pool admin.
/// @dev Value formulas (Q64.64 mid = token1 per token0), per-share in bin scaled units:
///
///      metricToken0 = t0*SCALE/shares + (t1 * 2^64 / mid) * SCALE / shares
///      metricToken1 = (t0 * mid / 2^64) * SCALE / shares + t1*SCALE/shares
///
///      A pure mid move pushes the metrics in opposite directions; a value leak pushes both down.
///        - metricToken0 breach (mid suspect-high) blocks zeroForOne == true  (token1 outflow)
///        - metricToken1 breach (mid suspect-low)  blocks zeroForOne == false (token0 outflow)
///        - both breached blocks both directions
///
///      Watermarks decay linearly at decayPerSecondE8 (lazy, per bin). Guarantee: value per
///      share at oracle marks cannot fall faster than drawdown (one-time) + decay * t (ongoing).
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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L103-110)
```text
  function proposeOracleStopLossDrawdown(address pool_, uint256 newMaxDrawdownE6) external onlyPoolAdmin(pool_) {
    _validateDrawdown(newMaxDrawdownE6);
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    uint32 executeAfter = _afterTimelock(pool_);
    sched.pendingDrawdownE6 = uint32(newMaxDrawdownE6);
    sched.pendingDrawdownExecuteAfter = executeAfter;
    emit OracleStopLossDrawdownProposed(pool_, newMaxDrawdownE6, executeAfter);
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L130-137)
```text
  function proposeOracleStopLossDecay(address pool_, uint256 newDecayPerSecondE8) external onlyPoolAdmin(pool_) {
    _validateDecay(newDecayPerSecondE8);
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    uint32 executeAfter = _afterTimelock(pool_);
    sched.pendingDecayPerSecondE8 = uint32(newDecayPerSecondE8);
    sched.pendingDecayExecuteAfter = executeAfter;
    emit OracleStopLossDecayProposed(pool_, newDecayPerSecondE8, executeAfter);
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L157-165)
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
