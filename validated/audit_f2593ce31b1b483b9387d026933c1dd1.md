### Title
Retroactive `trancheAPRSplitRatio` change lets a tranche holder capture yield accrued under the old ratio - (File: contracts/IdleCDO.sol)

### Summary
`IdleCDO` splits all interest accrued since the last accounting checkpoint using the *current* value of `trancheAPRSplitRatio`. The owner setter for the ratio does not call `_updateAccounting()` first, so a ratio change is applied retroactively to all unsplit yield. An unprivileged tranche holder (e.g. an AA holder) can watch for an owner ratio update, then trigger `_updateAccounting()` via `depositAA`/`withdrawAA`/`harvest` in the next block, capturing yield that accrued under the old ratio and belongs to the other tranche class.

### Finding Description
In `_updateAccounting`, the entire gain accumulated since the last saved NAV (`nav - lastNAV`) is split using the single stored `trancheAPRSplitRatio`:

- `uint256 _aprSplitRatio = trancheAPRSplitRatio;` is loaded once and passed to `_virtualPriceAux` for both tranches.
- In `_virtualPriceAux`, `totalBBGain = totalGain * (FULL_ALLOC - _trancheAPRSplitRatio) / FULL_ALLOC` applies that ratio to the whole `totalGain`, regardless of when the gain accrued or which ratio was in effect while it accrued.

`_updateAccounting()` only runs on `depositAA`/`depositBB`, `withdrawAA`/`withdrawBB`, and `harvest` — there is no time-based accrual checkpoint. `trancheAPRSplitRatio` is mutable by the owner (via `setTrancheAPRSplitRatio` / `setIsAYSActive` / `setFeeParams` paths in `IdleCDO.sol`) without first flushing accrued interest at the old ratio. This is exactly the Neobase `setRewards`/`setBlockTimeParameters` bug class: a configuration parameter is mutated while un-checkpointed state exists, so the new value applies retroactively.

Attack sequence (running epoch / normal CDO operation):

1. AA and BB depositors are in the pool; yield accrues on the strategy over weeks under split ratio R₀ (e.g. 50% AA).
2. Owner (honest, e.g. rebalancing the Adaptive Yield Split off or setting a new ratio) calls `setTrancheAPRSplitRatio(R₁)` with R₁ > R₀, e.g. 90%.
3. Attacker — an ordinary AA tranche holder (or anyone able to call `harvest`/`depositAA`) — calls `depositAA` with a dust amount in a later block. `_updateAccounting()` runs and splits the *entire* multi-week accrued `totalGain` at R₁, not R₀.
4. `lastNAVAA`/`priceAA` are updated with the inflated AA gain; the attacker's AA tokens (minted under the old price regime or bought beforehand) now redeem for more underlying. BB holders permanently lose the share `(R₁ − R₀) × totalGain` they accrued under R₀.

The same works in reverse: if the owner lowers the AA ratio, a BB holder triggers the update and captures the retroactively-larger BB share of past yield. The `_updateSplitRatio`/`_getAARatio` machinery only recomputes the ratio *after* mint/burn and cannot prevent the retroactive re-split of pending gain.

### Impact Explanation
Direct theft of accrued-but-unsplit yield from one tranche class to another. The magnitude is `|R₁ − R₀| × pendingGain`, where `pendingGain` is all NAV growth since the last user interaction — potentially weeks of strategy interest. For a pool with, say, 10% of NAV in unsplit interest and a ratio swing of 40 percentage points, the attacker class captures ~4% of total NAV from the other class. This breaks the fair mint/burn invariant: tranche prices after the update no longer reflect the waterfall rules that were in force while the yield accrued.

### Likelihood Explanation
- Requires only an honest owner ratio change, which is a routine operation (`setIsAYSActive`, `setTrancheAPRSplitRatio`, AYS min-split tuning via `setAprSplitRatio`/`setFeeParams`).
- The trigger transaction (`depositAA` with dust, or `harvest`) is fully permissionless; any EOA can perform it — no privileged role needed.
- Larger pending accrual (low deposit/withdraw/harvest activity) makes the exploit more profitable, and the attacker can hold a small tranche position cheaply until the owner acts.
- No existing guard stops it: `_checkDefault` only checks strategy price drops, `_guarded`/`_checkSameBlock` don't apply, and neither `setTrancheAPRSplitRatio` nor the AYS toggles flush accounting before mutating the ratio.

### Recommendation
Checkpoint accrued interest at the old ratio before mutating it: call `_updateAccounting()` at the top of `setTrancheAPRSplitRatio`, `setIsAYSActive`, `setAprSplitRatio`/`setFeeParams` (anything that changes `trancheAPRSplitRatio` or `fee`), mirroring Neobase's "update all markets first" fix but feasible here because there is a single accounting pair. Alternatively, disallow ratio changes while `getContractValue() != lastNAVAA + lastNAVBB` (i.e. only permit them immediately after a same-block `harvest`/`deposit`/`withdraw`).

### Proof of Concept
Foundry test against the existing `IdleCDO.t.sol`-style harness (adapted to this repo's test setup; drop into a test that already deploys `IdleCDO`, strategy, AA/BB tranches, and deals `token`):

```solidity
function testRetroactiveSplitRatioSteal() public {
    // Setup: AA holder (attacker) and BB holder each deposit 1000e18 while
    // trancheAPRSplitRatio == 50% (50000)
    uint256 amount = 1000 * ONE_SCALE;
    _depositWithUser(attackerAA, amount, true);   // AA tranche
    _depositWithUser(victimBB, amount, false);    // BB tranche

    // Simulate weeks of strategy yield: donate/accrue interest so NAV grows ~10%
    uint256 gain = 200 * ONE_SCALE;               // pending, unsplit yield
    _accrueYield(gain);                            // strategy price increase or direct mint to strategy

    // Snapshot victim BB virtual value under the OLD ratio (view only)
    uint256 bbValueBefore = IdleCDOTranche(BBtranche).balanceOf(victimBB)
        * cdo.virtualPrice(address(BBtranche)) / ONE_TRANCHE;

    // Honest owner changes the split ratio WITHOUT flushing accounting
    vm.prank(owner);
    cdo.setTrancheAPRSplitRatio(90_000);           // AA now gets 90%

    // Attacker (unprivileged EOA / AA holder) triggers _updateAccounting via dust deposit
    _depositWithUser(attackerAA, 1, true);         // splits whole `gain` at 90/10

    // AA NAV captured ~180 of the 200 gain instead of ~100 under the old ratio
    uint256 bbValueAfter = IdleCDOTranche(BBtranche).balanceOf(victimBB)
        * cdo.virtualPrice(address(BBtranche)) / ONE_TRANCHE;

    // BB lost ~80e18 of accrued yield to AA retroactively
    assertLt(bbValueAfter, bbValueBefore);
    assertApproxEqAbs(
        cdo.lastNAVAA(),
        amount + (gain * 90_000 / FULL_ALLOC),
        10,
        'AA captured yield at the new ratio retroactively'
    );
}
```

Run: `forge test --match-test testRetroactiveSplitRatioSteal -vv`. The assertion demonstrates the invariant break: `lastNAVAA` reflects `gain` split at the post-change ratio even though the yield accrued entirely under the pre-change ratio.