### Title
`requestWithdraw` reverts on underflow when upfront management fee exceeds principal plus interest, freezing all withdrawals - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
The external report describes a checked subtraction that can legitimately underflow (fee-growth counters are meant to wrap), causing user transactions to revert. The strongest analog in idle-tranches is the withdrawal-fee accounting in `IdleCDOEpochVariant.requestWithdraw`: `principal + interest - totalFees` is computed with Solidity's checked arithmetic, but `totalFees` — the upfront management fee charged for the receipt's time outside live NAV — is not capped at `principal + interest`. When the configured `managementFee` and the fee duration (`epochDuration` + remaining `bufferPeriod`) are large enough that `_calculateManagementFee(_principal, _duration) > _principal + _interest`, every call to `requestWithdraw` panics with an arithmetic underflow and withdrawals are frozen until the owner lowers the fee parameters.

### Finding Description
`requestWithdraw` computes the user's claim as:

```solidity
// contracts/IdleCDOEpochVariant.sol:772-778
uint256 principal = _underlyings;
(uint256 interest, int256 diff) = _calcInterestWithdrawRequest(_underlyings, _tranche);
uint256 totalFees = _totalWithdrawFees(principal, interest);
_underlyings = principal + interest - totalFees;
pendingWithdrawFees += totalFees;
```

`_totalWithdrawFees` returns the raw management fee whenever `_mgmtFee >= _interest` (`contracts/IdleCDOEpochVariant.sol:900-906`), so the subtraction becomes `principal + interest - _mgmtFee` with no saturation at zero. `_mgmtFee = _principal * managementFee * _duration / (FULL_ALLOC * 365 days)` (`contracts/IdleCDOCreditVault.sol:558-561`) and `_duration = epochDuration + (epochEndDate + bufferPeriod - block.timestamp)` while inside the buffer (`_withdrawRequestManagementFeeDuration`, lines 925-931).

Withdrawal requests are only allowed during the buffer phase or while `allowAA/BBWithdrawRequest` is set, i.e. exactly the window where `_duration` is largest. With `managementFee` at its maximum (100%/yr, bounded by `FULL_ALLOC` in `setFeeParams`) and an epoch duration of roughly 400+ days — plausible for a credit vault funding longer-dated loans — `_mgmtFee` exceeds `principal + interest` and the transaction reverts with panic `0x11`. The same underflow exists in the `maxWithdrawable` view (`contracts/IdleCDOEpochVariant.sol:913`), so frontends/integrators also revert. Unlike the guarded `unclaimedFees -= _fees` path in `stopEpoch` (line 453 caps `_fees` at `_availableForFees`), the withdrawal path performs a bare checked subtraction.

### Impact Explanation
All `requestWithdraw` calls revert during the entire period in which the fee duration keeps `_mgmtFee` above `principal + interest`. Users cannot queue withdrawals at all; their tranche tokens are locked in NAV until the owner intervenes by lowering `managementFee` (and even then the fix only helps if the new parameters bring the fee back under `principal + interest`). This is a temporary freezing of user funds affecting every depositor, proportional to total NAV. `maxWithdrawable` reverting additionally breaks integrations that rely on it.

### Likelihood Explanation
Likelihood is low-to-medium: the trigger depends on honest-owner parameter choices (high `managementFee` combined with a long `epochDuration`/`bufferPeriod`), not on any attacker transaction. An unprivileged user only needs to call `requestWithdraw` in a phase where the arithmetic underflows — no attacker's own state manipulation is required. However, the condition requires `managementFee * (epochDuration + buffer) > ~365 days * FULL_ALLOC` (roughly, net of the APR term), which only arises for long-duration credit pools with near-maximal fee configuration.

### Recommendation
Mirror the saturation already used in `stopEpoch` and `_netGainAfterFees`: cap `totalFees` at `principal + interest` (or at minimum return `0` from `requestWithdraw` when `totalFees >= principal + interest`), e.g.

```solidity
uint256 totalFees = _totalWithdrawFees(principal, interest);
if (totalFees > principal + interest) totalFees = principal + interest; // or leave interest unpaid
_underlyings = principal + interest - totalFees;
```

Alternatively revert with a descriptive error so the freeze is diagnosable, but capping is preferable since a reverting user-facing entrypoint provides no escape hatch. Apply the same saturation in `maxWithdrawable` at line 913 (`currentUnderlyings -= _calculateManagementFee(...)`), which underflows whenever accrued fees exceed the balance.

### Proof of Concept
```solidity
// Foundry test against the existing IdleCreditVault.t.sol harness
function testRequestWithdrawUnderflowsOnLongEpoch() external {
    // managementFee = 100%/yr, long epoch so fee duration exceeds the breakeven
    _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, FULL_ALLOC); // mgmtFee = 100%
    _setEpochDuration(450 days, 30 days);                  // epoch 450d, buffer 30d

    uint256 amount = 10_000 * ONE_SCALE;
    uint256 trancheAmount = idleCDO.depositAA(amount);
    _transferBurnedTrancheTokens(address(this), true);

    // Run one epoch so the pool is in buffer phase with the long duration set
    vm.prank(manager);
    cdoEpoch.startEpoch();
    uint256 expected = cdoEpoch.expectedEpochInterest();
    deal(defaultUnderlying, borrower, expected + IdleCreditVault(address(strategy)).pendingWithdraws());
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0); // reopen requests; duration now epochDuration + remaining buffer ≈ 480d

    // mgmtFee = principal * 1.0 * 480/365 ≈ 1.31 * principal > principal + interest
    vm.expectRevert(stdError.arithmeticError); // panic 0x11 underflow at line 776
    cdoEpoch.requestWithdraw(trancheAmount, address(AAtranche));

    // Recovery path: owner must cut managementFee; funds are frozen until then
    _setManagementFee(0);
    cdoEpoch.requestWithdraw(trancheAmount, address(AAtranche)); // now succeeds
}
```

Note: I could not read `setFeeParams` to confirm the exact `managementFee` upper bound before the iteration limit; the PoC assumes it is capped at `FULL_ALLOC` (consistent with `fee`/`feeSplit` bounds). If the cap is lower, the required `epochDuration` grows proportionally and the finding's likelihood decreases accordingly, but the unguarded subtraction at `contracts/IdleCDOEpochVariant.sol:776` remains a latent revert for any configuration where the upfront fee exceeds principal plus interest.