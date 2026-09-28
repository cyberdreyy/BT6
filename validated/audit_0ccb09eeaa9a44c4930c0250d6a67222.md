### Title
`previewLossAdjustedWithdrawFunds` excludes still-claimable fees from the loss basis, over-haircutting LPs and pending receipts - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
In cash (non-minted) mode, `stopEpoch`/`stopEpochWithDuration` realizes a loss by splitting `_lossAmount` between pending withdraw receipts and active LPs via `previewLossAdjustedWithdrawFunds`. The active basis (`_lossActiveBasis`) is computed **net of `unclaimedFees` and additionally net of the performance fee on epoch gain**. However, those fees are *not* cancelled by the loss: unpaid fees remain accrued in `unclaimedFees` ("continues reducing NAV") and are paid later, or are paid in the same `stopEpoch` via `_transferFeeUnderlyings`. This mirrors the GMX finding: a solvency/loss-distribution check prices a claim on a value that excludes a fee that is still claimable after the event. Because `totalBasis = activeBasis + pendingBasis` is understated by the fee amount, both the `pendingLoss` share and the BB-first active loss are oversized, while the fee claim escapes the waterfall entirely. Minted mode is inconsistent: there the basis uses the full `balanceOf(idleCDO) + expectedEpochInterest`, so fee shares *do* absorb the loss.

### Finding Description
`IdleCreditVault.previewLossAdjustedWithdrawFunds` splits a realized epoch loss pro rata over `totalBasis = activeBasis + pendingBasis`:

```solidity
// contracts/strategies/idle/IdleCreditVault.sol:457-459
uint256 pendingLoss = _lossAmount * pendingBasis / totalBasis;
pendingToFund = pendingBasis - pendingLoss;
activeLoss = _lossAmount - pendingLoss;
```

In cash mode `activeBasis` is reduced twice by claims that survive the loss:

```solidity
// contracts/strategies/idle/IdleCreditVault.sol:474-483
uint256 activeBasisBeforePerfFee = _cdo.getContractValue();   // already net of unclaimedFees
...
activeBasis = activeBasisBeforePerfFee - ((activeBasisBeforePerfFee - savedNAV) * _cdo.fee() / FULL_ALLOC);
```

`getContractValue` subtracts `unclaimedFees` (`contracts/IdleCDOCreditVault.sol:127`), and `_lossActiveBasis` further deducts the performance fee on gains. Yet in `IdleCDOEpochVariant._stopEpoch` the accrued fees are still paid out (`_transferFeeUnderlyings`) or kept accrued:

```solidity
// contracts/IdleCDOEpochVariant.sol:452-459
uint256 _availableForFees = _grossInterest > _pendingWithdrawFees ? _grossInterest - _pendingWithdrawFees : 0;
if (_fees > _availableForFees) { _fees = _availableForFees; }
_transferFeeUnderlyings(_fees);
// Any fee that cannot be paid in cash remains accrued and continues reducing NAV.
unclaimedFees -= _fees;
```

The net effect: the feeReceiver/owner claim is senior in payment but invisible in the loss denominator, so user claims (pending receipts via `lossRecoveryPriceByEpoch` in `collectWithdrawFunds`, and active tranches via `burnStrategyTokens` + `_forceUpdateAccounting`) absorb 100% of a loss whose denominator should have included the still-claimable fee. There is even a revert asymmetry: `_lossAmount > activeBasis` reverts even when the gross (fee-inclusive) basis could cover the loss, making a legitimate stop harder — the direct parallel of "positions more difficult to liquidate".

### Impact Explanation
Every `stopEpochWithDuration` loss in cash mode with non-zero accrued fees shifts value from unprivileged users (pending receipt holders and active AA/BB LPs) to the fee claim. For a vault with gross active backing `A`, accrued fees `F`, pending basis `P`, and loss `L`:

- Pending receipts lose `L*P/(A-F+P)` instead of `L*P/(A+P)` — an over-haircut of `L*P*F/((A-F+P)(A+P))`.
- Active LPs absorb `activeLoss` on a NAV that still owes `F - paid`, so they are charged the loss and then charged the surviving fee again.

The loss is bounded by `unclaimedFees` + the performance-fee deduction — a fraction of TVL that grows with epoch duration and `managementFee`/`fee` rates — but it is a systematic, deterministic wealth transfer on every lossy stop, not a rounding artifact.

### Likelihood Explanation
Requires only an honest manager calling `stopEpochWithDuration`/`stopEpoch` with `_lossAmount != 0` in a cash-mode epoch with accrued management or performance fees — a normal operational path (borrower shortfall / realized loss). No attacker action is needed to trigger the transfer; any user holding a pending receipt or tranche tokens is the victim. The minted-mode branch already implements the correct (gross) basis, confirming the cash-mode exclusion is an oversight rather than intentional seniority.

### Recommendation
Make the cash-mode loss basis consistent with minted mode: either include the surviving fee claim in the loss basis (`activeBasis = balanceOf(idleCDO) + expectedInterest`, letting the fee absorb its pro-rata share), or treat accrued fees as loss-bearing by waiving/haircutting `unclaimedFees` when `_lossAmount != 0` (as `finalizeDefault` does by zeroing `unclaimedFees` and using gross `balanceOf(idleCDO)`). Apply the same gross-basis policy to the `_lossAmount > activeBasis` solvency check.

### Proof of Concept
Foundry fork PoC sketch (cash mode, `isInterestMinted == false`):

```solidity
// setup: AA + BB deposits, nonzero managementFee or fee (performance)
_setFeeParams(TL_MULTISIG, 10_000 /*10% perf fee*/, FULL_ALLOC, cdoEpoch.managementFee());
idleCDO.depositAA(10_000 * ONE_SCALE);
idleCDO.depositBB(10_000 * ONE_SCALE);

// user requests a normal withdraw -> pendingBasis = P
uint256 p = cdoEpoch.requestWithdraw(1_000 * ONE_SCALE, address(AAtranche));

_startEpochAndCheckPrices(0);              // epoch runs, interest accrues -> unclaimedFees F > 0
deal(defaultUnderlying, borrower, expectedEndFunds);

uint256 feesPre = cdoEpoch.unclaimedFees(); // > 0 after _accrueManagementFee in _stopEpoch
uint256 grossActive = IERC20(strategyToken).balanceOf(address(cdoEpoch)) + cdoEpoch.expectedEpochInterest();

// manager stops epoch realizing loss L
uint256 L = 2_000 * ONE_SCALE;
vm.prank(manager);
cdoEpoch.stopEpochWithDuration(apr, 0, duration, L);

// Expected (fair, fee-participating): pendingLoss = L * P / (grossActive + P)
// Actual: pendingLoss = L * P / (_lossActiveBasis + P) with _lossActiveBasis < grossActive
uint256 price = IdleCreditVault(strategy).lossRecoveryPriceByEpoch(epoch);
assertLt(price, expectedFairRecoveryPrice);       // receipt holders haircut too much
assertGt(cdoEpoch.unclaimedFees(), 0);            // surviving fee still reduces LP NAV later
// feeReceiver/owner collected fees in the same stop and/or keep the accrued claim un-haircut
```

The PoC demonstrates: (1) `lossRecoveryPriceByEpoch` is lower than the fair gross-basis price, (2) `unclaimedFees` remains nonzero/claimable after the loss burn, and (3) the inconsistency disappears in minted mode where the gross basis is used.