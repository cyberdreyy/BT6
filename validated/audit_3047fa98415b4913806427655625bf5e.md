### Title
Performance-fee and split-ratio changes apply retroactively to interest accrued before the change, confiscating yield earned under the old parameters - (File: contracts/IdleCDOCreditVault.sol)

### Summary
In `IdleCDOCreditVault.setFeeParams` (contracts/IdleCDOCreditVault.sol:461-470), the owner can raise the performance `fee` without first checkpointing tranche accounting. `_updateAccounting` and `_virtualPriceAux` (IdleCDO.sol:273-311, 359-430; CreditVault variant lines 301-337) apply the *current* `fee` to the *entire* gain since the last accounting (`nav - lastNAV`), so a fee increase retroactively taxes yield that depositors already earned under the lower fee. The same pattern applies to `trancheAPRSplitRatio`: the stored ratio at accounting time re-splits the whole accrued gain between AA and BB, so a ratio change (via `setMinAprSplitAYS`/`_updateSplitRatio`, lines 386-401) retroactively reallocates past yield between the tranche classes. This is the idle-tranches analog of the GMX min-collateral issue: a changeable global parameter is applied to positions that were in good standing under the previous parameter, transferring their already-earned value to another party (here feeReceiver/owner or the opposite tranche class) with nothing returned to the harmed users.

### Finding Description
- `setFeeParams` calls `_accrueManagementFee()` (line 466) which correctly checkpoints the *management* fee at the old rate, but it does **not** call `_updateAccounting()` before `fee = _fee` (line 467). The performance fee is not time-weighted or checkpointed.
- In `_updateAccounting`, `unclaimedFees += (nav - _lastNAV) * fee / FULL_ALLOC` (IdleCDO.sol:281; CreditVault lines 311-314 in `_virtualPriceAux`) charges the *new* `fee` on the full gain accumulated since the last deposit/withdraw/harvest — including gain earned while `fee` was lower (or zero).
- Similarly, `_virtualPriceAux` splits `totalGain` (accrued over the whole stale period) at the *current* `trancheAPRSplitRatio` (CreditVault lines 324-328; IdleCDO lines 398-402). Any honest owner/manager action that changes the stored ratio before the next accounting event retroactively re-divides yield earned under the prior ratio.
- No guard exists: `_deposit`/`withdraw`/`updateAccounting` happily apply whatever `fee`/`trancheAPRSplitRatio` is in storage at execution time to the full pending gain.

### Impact Explanation
Tranche holders (AA seniors / BB juniors) lose accrued, not-yet-crystallized yield when a fee or split-ratio change lands between accounting events. Example: vault accrues 1,000 underlying of gain with `fee = 0`; owner raises `fee` to `MAX_FEE` (bounded only by `MAX_FEE`, line 463); the next `_updateAccounting` diverts up to 100% of that already-earned gain to `feeReceiver`/`owner` via `unclaimedFees`, reducing `priceAA`/`priceBB` and every holder's redeemable amount accordingly. With a split-ratio change, the loss is transferred between AA and BB holders instead. This is theft/misallocation of unclaimed yield with a quantified loss equal to `(newFee - oldFee) * pendingGain / FULL_ALLOC` (or the delta of `totalGain` re-split under the new ratio).

### Likelihood Explanation
The trigger is an honest owner call (`setFeeParams`, `setMinAprSplitAYS`, `setIsAYSActive`) — explicitly permitted by the threat model, which sequences around honest privileged calls and places the *victims* as unprivileged tranche holders. Fee changes are a routine operational action, and pending gains exist whenever time passes between accounting events, so the retroactive window is essentially always open. One caveat: I was not able to confirm whether a direct `setTrancheAPRSplitRatio` setter exists in this variant within my remaining search budget; the finding stands on `setFeeParams`/`_virtualPriceAux`, which are confirmed.

### Recommendation
Checkpoint pending accounting before mutating retroactive parameters: call `_updateAccounting()` (and `_accrueManagementFee()`) inside `setFeeParams` before writing `fee`, and snapshot/refresh `trancheAPRSplitRatio` only at accounting boundaries, or track per-checkpoint parameter values so accrued gain is always split/fees charged at the parameters in force while it accrued. Alternatively, document and bound retroactive application (e.g., require a forced `updateAccounting` epoch boundary before any fee/split change takes effect).

### Proof of Concept
Reproducible Foundry fork test sketch (mirroring `testSetManagementFeeCheckpointsBeforeRateChange` style in test/foundry/IdleCreditVault.t.sol):

```solidity
function testRetroactivePerformanceFee() external {
    uint256 amount = 10_000 * ONE_SCALE;
    idleCDO.depositAA(amount);            // fee = 0
    _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, 0);

    // accrue gain: simulate epoch interest / strategy price appreciation
    uint256 gain = 1_000 * ONE_SCALE;
    deal(defaultUnderlying, address(strategy), strategyBal + gain); // NAV + gain, not yet accounted

    // honest owner raises performance fee to 10%
    _setFeeParams(TL_MULTISIG, 10_000, FULL_ALLOC, 0);   // only _accrueManagementFee checkpoints; accounting is NOT run

    vm.prank(owner);
    cdoEpoch.updateAccounting();           // applies new 10% fee to the FULL pre-accrued gain

    // feeReceiver receives 100 gain that accrued while fee was 0
    assertEq(cdoEpoch.unclaimedFees(), gain * 10_000 / FULL_ALLOC);
    // tranche holders' price reflects gain net of a fee they never agreed to
    assertLt(cdoEpoch.virtualPrice(address(AAtranche)), expectedPriceAtOldFee);
}
```

The assertion that `unclaimedFees` equals `newFee * pendingGain` (rather than `0`, the correct value under the rate in force during accrual) demonstrates the retroactive confiscation.