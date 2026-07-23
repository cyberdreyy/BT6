### Title
`block.number` Dependency in `PriceVelocityGuardExtension` Causes Over-Constrained Velocity Guard on L2s — (`metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol`)

---

### Summary

`PriceVelocityGuardExtension` uses `block.number` to track when the last price was observed and to compute how many blocks have elapsed between swaps. On L2 networks such as Arbitrum, `block.number` returns the **L1 block number**, not the L2 block number. Because many L2 blocks are produced per L1 block (~48 on Arbitrum at 0.25 s L2 / 12 s L1), consecutive swaps that occur in different L2 blocks but within the same L1 block will compute `blockDiff = 0`. This collapses the allowed price-movement budget to its minimum (`maxChange²`), causing the guard to revert legitimate swaps whose oracle price has moved by more than `maxChange` within that L1 block window.

---

### Finding Description

`PriceVelocityGuardExtension.beforeSwap` records `block.number` as `lastUpdateBlock` and computes the allowed price movement as:

```
allowedSq = maxChange² × (1 + blockDiff)
```

where `blockDiff = block.number − prevBlock`. [1](#0-0) 

The design intent (stated in the NatSpec) is that the budget grows as `maxChange × sqrt(1 + blockDifference)` — i.e., more elapsed blocks permit more price movement. This is calibrated against L2 block cadence.

On Arbitrum, `block.number` returns the L1 block number. A single L1 block spans ~48 Arbitrum L2 blocks. Two swaps that arrive in different L2 blocks but the same L1 block will both read the same `block.number`, so `blockDiff = 0` and `allowedSq = maxChange²`. The guard treats them as if they happened in the same block, even though real time (and real oracle price movement) has elapsed across those L2 blocks.

`setLastMidPrice` (the admin seed path) also stamps `block.number`: [2](#0-1) 

So the seed block is also an L1 block number, compounding the mismatch.

---

### Impact Explanation

Any pool on Arbitrum (or a similar L2) that opts into `PriceVelocityGuardExtension` with a non-zero `maxChangePerBlockE18` will have its `beforeSwap` hook revert with `PriceVelocityExceeded` whenever the oracle mid price moves by more than `maxChange` between two swaps that fall within the same L1 block — even if those swaps are many L2 blocks apart and the oracle movement is entirely legitimate. This renders the pool's swap flow **unusable** during periods of normal oracle price movement on Arbitrum, matching the "Broken core pool functionality causing unusable swap flows" impact gate.

---

### Likelihood Explanation

Arbitrum is a primary deployment target for DeFi protocols. The Metric OMM codebase already contains L2-specific contracts (`PriceProviderL2.sol`, `ProtectedPriceProviderL2.sol`, deployment scripts under `script/l2/`), confirming L2 deployment is in scope. Any pool admin who enables `PriceVelocityGuardExtension` on Arbitrum with a `maxChangePerBlockE18` tuned to L2 block cadence will trigger this condition during ordinary oracle updates. The trigger requires no adversarial action — normal oracle price movement is sufficient.

---

### Recommendation

Replace `block.number` with `block.timestamp` in both `beforeSwap` and `setLastMidPrice`, and rename the state variable and parameter accordingly (e.g., `lastUpdateTimestamp`, `maxChangePerSecondE18`). The allowed budget then becomes:

```
allowedSq = maxChangePerSecond² × (1 + timeDiff)
```

`block.timestamp` is consistent across all EVM-compatible L2s and directly reflects elapsed real time, which is what the velocity constraint is semantically measuring. [3](#0-2) 

---

### Proof of Concept

1. Deploy `PriceVelocityGuardExtension` on Arbitrum.
2. Pool admin calls `setMaxChangePerBlock(pool, 1e16)` (1% per block, calibrated to L2 blocks of ~0.25 s).
3. Pool admin calls `setLastMidPrice(pool, P0)` — stamps `lastUpdateBlock = L1_block_N`.
4. Oracle updates the mid price by 1.5% (legitimate movement over ~6 L2 blocks, still within the same L1 block `N`).
5. A user calls `swap(...)` on the pool. `beforeSwap` fires:
   - `block.number` is still `L1_block_N` → `blockDiff = 0`
   - `allowedSq = (1e16)² × 1 = 1e32`
   - `changeE18 = 1.5e16` → `actualSq = 2.25e32`
   - `2.25e32 > 1e32` → **revert `PriceVelocityExceeded`**
6. The swap is blocked despite the oracle movement being entirely legitimate and the pool being correctly priced. [4](#0-3)

### Citations

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L29-33)
```text
  function setLastMidPrice(address pool_, uint128 newLastMidPriceX64) external onlyPoolAdmin(pool_) {
    PriceVelocityState storage s = priceVelocityState[pool_];
    s.lastMidPriceX64 = newLastMidPriceX64;
    s.lastUpdateBlock = uint64(block.number);
    emit LastMidPriceUpdated(pool_, newLastMidPriceX64);
```

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L55-74)
```text
    uint64 prevBlock = s.lastUpdateBlock;

    s.lastMidPriceX64 = midPrice;
    s.lastUpdateBlock = uint64(block.number);

    if (prevMid != 0) {
      uint64 maxChange = s.maxChangePerBlockE18;
      if (maxChange != 0) {
        uint256 blockDiff = block.number - prevBlock;

        uint256 delta = midPrice > prevMid ? uint256(midPrice - prevMid) : uint256(prevMid - midPrice);

        uint256 changeE18 = (delta * 1e18) / uint256(prevMid);

        uint256 actualSq = changeE18 * changeE18;
        uint256 allowedSq = uint256(maxChange) * uint256(maxChange) * (1 + blockDiff);

        if (actualSq > allowedSq) {
          revert PriceVelocityExceeded(actualSq, allowedSq);
        }
```
