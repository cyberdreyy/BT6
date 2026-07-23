### Title
Missing watermark pre-settlement before `decayPerSecondE8` update retroactively misapplies decay, corrupting stop-loss thresholds — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`executeOracleStopLossDecay` overwrites `decayPerSecondE8` without first committing the pending decay to each bin's stored watermark under the old rate. Because decay is computed lazily at swap time using `dt = block.timestamp − lastDecayTs` and the **current** rate, the rate change is applied retroactively to the entire elapsed interval, producing watermark values that are wrong in both directions depending on whether the rate was raised or lowered.

---

### Finding Description

The `OracleValueStopLossExtension` tracks per-bin high-watermarks and decays them linearly at `decayPerSecondE8` (E8-scaled units per second). Decay is **lazy**: it is never written to storage until a swap touches the bin. At that point `_checkAndUpdateWatermarks` computes:

```solidity
// OracleValueStopLossExtension.sol L268-284
uint256 dt = block.timestamp - hwmS.lastDecayTs;
(uint256 hwm0, ...) = _applyWatermark(metricT0, _decayed(hwmS.token0, decayRate, dt), ...);
...
hwmS.lastDecayTs = uint32(block.timestamp);
```

`_decayed` applies the **current** `decayPerSecondE8` to the full `dt`:

```solidity
// L319-324
function _decayed(uint256 hwm, uint256 ratePerSecondE8, uint256 dt) private pure returns (uint256) {
    uint256 factor = ratePerSecondE8 * dt;
    if (factor >= E8) return 0;
    return hwm - (hwm * factor) / E8;
}
```

When the pool admin calls `executeOracleStopLossDecay`, the rate is overwritten with no pre-settlement:

```solidity
// L139-147
function executeOracleStopLossDecay(address pool_) external onlyPoolAdmin(pool_) {
    ...
    uint32 decay = sched.pendingDecayPerSecondE8;
    oracleStopLossConfig[pool_].decayPerSecondE8 = decay;   // ← rate changed
    // ← lastDecayTs for every bin is NOT updated
    ...
}
```

`lastDecayTs` for every bin remains at the timestamp of the last swap. The next swap computes `dt` spanning the entire gap — including the period that elapsed under the **old** rate — and applies the **new** rate to all of it.

**Case A — rate increased (e.g., 58 → E8):**
The new, higher rate is applied retroactively to the full `dt`. Watermarks are driven far below their correct values (possibly to zero via the `factor >= E8` floor). The stop-loss floor `hwm * floorMultiplier / E6` collapses, so a subsequent swap that extracts value from LPs passes the breach check when it should have been blocked.

**Case B — rate decreased to 0:**
Zero decay is applied retroactively to the full `dt`. Watermarks remain at their last-stored values with no decay at all, as if time never passed. The floor stays artificially high, causing the stop-loss to revert on legitimate swaps that should have been permitted after the old decay eroded the watermark.

The NatSpec guarantee — *"value per share at oracle marks cannot fall faster than drawdown (one-time) + decay × t (ongoing)"* — is violated in Case A.

---

### Impact Explanation

**Case A (rate raised):** The stop-loss fails to block a swap that extracts LP value. The extension's entire purpose is to prevent value leakage; bypassing it means LP principal is directly at risk. This matches *"bad-price execution reaches a pool swap"* and *"broken core pool functionality causing loss of funds."*

**Case B (rate lowered/zeroed):** Legitimate swaps revert with `OracleStopLossTriggered` because the watermark was never decayed. This is a broken swap flow for the pool.

---

### Likelihood Explanation

The pool admin is a valid semi-trusted actor who is explicitly permitted to change the decay rate via the propose/execute timelock flow. The bug fires on every such change. The magnitude of the error scales with `dt` — the time since the last swap touched the affected bin — making it worst in pools with low activity, exactly the same amplifier identified in M-13.

---

### Recommendation

Before overwriting `decayPerSecondE8`, commit the pending decay to every bin's stored watermark under the old rate. Because the extension does not maintain a list of touched bins, the cleanest approach is to iterate over all valid bin indices (−128 to 127) and, for any bin with a non-zero watermark, write the decayed value and reset `lastDecayTs` to `block.timestamp`:

```solidity
function executeOracleStopLossDecay(address pool_) external onlyPoolAdmin(pool_) {
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    if (sched.pendingDecayExecuteAfter == 0) revert OracleStopLossNoPendingDecay(pool_);
    _requireElapsed(sched.pendingDecayExecuteAfter);

    // ── Pre-settle all bins under the old rate ──────────────────────────
    uint256 oldRate = oracleStopLossConfig[pool_].decayPerSecondE8;
    if (oldRate > 0) {
        for (int8 b = type(int8).min; ; b++) {
            BinHighWatermarks storage hwmS = highWatermarks[pool_][b];
            if (hwmS.token0 != 0 || hwmS.token1 != 0) {
                uint256 dt = block.timestamp - hwmS.lastDecayTs;
                hwmS.token0 = uint104(_decayed(hwmS.token0, oldRate, dt));
                hwmS.token1 = uint104(_decayed(hwmS.token1, oldRate, dt));
                hwmS.lastDecayTs = uint32(block.timestamp);
            }
            if (b == type(int8).max) break;
        }
    }
    // ────────────────────────────────────────────────────────────────────

    uint32 decay = sched.pendingDecayPerSecondE8;
    oracleStopLossConfig[pool_].decayPerSecondE8 = decay;
    (sched.pendingDecayPerSecondE8, sched.pendingDecayExecuteAfter) = (0, 0);
    emit OracleStopLossDecaySet(pool_, decay);
}
```

The same pre-settlement should be applied in `executeOracleStopLossDrawdown` if the drawdown change is intended to take effect only from the moment of the update.

---

### Proof of Concept

1. Pool is created with `OracleValueStopLossExtension`, `drawdownE6 = 50_000` (5%), `decayPerSecondE8 = 58` (~5%/day).
2. A swap touches bin 0 at `t = 0`. Watermark is set to `hwm = 1000` (arbitrary units). `lastDecayTs = 0`.
3. No further swaps occur for 4 days (`dt = 345_600 s`). Under the old rate the correct decayed watermark is `1000 − (1000 × 58 × 345_600) / 1e8 ≈ 800`.
4. At `t = 4 days`, the pool admin executes a decay-rate change to `E8` (100%/second). `lastDecayTs` for bin 0 is still `0`.
5. Immediately after, a swap touches bin 0. `_checkAndUpdateWatermarks` computes `dt = 345_600`, `factor = E8 × 345_600 ≥ E8`, so `_decayed` returns **0**. The watermark is zeroed.
6. The stop-loss floor is `0 × floorMultiplier / E6 = 0`. Any positive metric passes `metric < 0` as false → **no breach detected**. A swap that extracts 30% of LP value proceeds unchecked.
7. Correct behaviour: the watermark should have been ~800 before the rate change, giving a floor of `800 × 950_000 / 1_000_000 = 760`. A metric of 700 (30% extraction) would have triggered `OracleStopLossTriggered`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L70-76)
```text
  /// @notice Current (decayed) watermarks — what the next check compares against.
  function currentHighWatermarks(address pool, int8 binIdx) external view returns (uint256 hwm0, uint256 hwm1) {
    BinHighWatermarks memory hwm = highWatermarks[pool][binIdx];
    uint256 rate = oracleStopLossConfig[pool].decayPerSecondE8;
    uint256 dt = block.timestamp - hwm.lastDecayTs;
    return (_decayed(hwm.token0, rate, dt), _decayed(hwm.token1, rate, dt));
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L139-147)
```text
  function executeOracleStopLossDecay(address pool_) external onlyPoolAdmin(pool_) {
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    if (sched.pendingDecayExecuteAfter == 0) revert OracleStopLossNoPendingDecay(pool_);
    _requireElapsed(sched.pendingDecayExecuteAfter);
    uint32 decay = sched.pendingDecayPerSecondE8;
    oracleStopLossConfig[pool_].decayPerSecondE8 = decay;
    (sched.pendingDecayPerSecondE8, sched.pendingDecayExecuteAfter) = (0, 0);
    emit OracleStopLossDecaySet(pool_, decay);
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L267-284)
```text
    BinHighWatermarks storage hwmS = highWatermarks[pool_][binIdx];
    uint256 dt = block.timestamp - hwmS.lastDecayTs;

    (uint256 hwm0, bool breach0) = _applyWatermark(metricT0, _decayed(hwmS.token0, decayRate, dt), floorMultiplier);
    if (breach0 && zeroForOne) {
      revert OracleStopLossTriggered(binIdx, true, metricT0, (hwm0 * floorMultiplier) / E6);
    }

    (uint256 hwm1, bool breach1) = _applyWatermark(metricT1, _decayed(hwmS.token1, decayRate, dt), floorMultiplier);
    if (breach1 && !zeroForOne) {
      revert OracleStopLossTriggered(binIdx, false, metricT1, (hwm1 * floorMultiplier) / E6);
    }

    // forge-lint: disable-next-line(unsafe-typecast)
    hwmS.token0 = uint104(hwm0);
    // forge-lint: disable-next-line(unsafe-typecast)
    hwmS.token1 = uint104(hwm1);
    hwmS.lastDecayTs = uint32(block.timestamp);
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L318-324)
```text
  /// @dev Linear decay; floors at 0 (ratchet restores from the live metric on next touch).
  function _decayed(uint256 hwm, uint256 ratePerSecondE8, uint256 dt) private pure returns (uint256) {
    if (ratePerSecondE8 == 0 || dt == 0 || hwm == 0) return hwm;
    uint256 factor = ratePerSecondE8 * dt;
    if (factor >= E8) return 0;
    return hwm - (hwm * factor) / E8;
  }
```
