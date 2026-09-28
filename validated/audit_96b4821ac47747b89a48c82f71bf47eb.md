### Title
Performance fee and feeSplit apply retroactively to interest accrued under old parameters while managementFee is checkpointed - ([File: contracts/IdleCDOCreditVault.sol])

### Summary
In `IdleCDOCreditVault.setFeeParams`, the `managementFee` rate is treated correctly: `_accrueManagementFee()` is called *before* the new rate is stored, so the elapsed period is charged at the old rate. However, the performance `fee` and `feeSplit` are updated in the same transaction with no checkpointing or queuing. The performance fee is only ever applied at `stopEpoch`, where it is charged on the *entire* epoch's gross interest — including the portion that accrued while depositors were still locked in under the old fee. This mirrors the Perennial issue: one fee parameter (`managementFee`) follows the "settle first, then apply new rate" pattern, while the sibling parameters (`fee`, `feeSplit`) do not.

### Finding Description
`setFeeParams` (`contracts/IdleCDOCreditVault.sol:461-470`) does:

```solidity
_accrueManagementFee();   // checkpoint elapsed mgmt fee at OLD rate
fee = _fee;               // new performance fee applies to whole epoch at stopEpoch
feeSplit = _feeSplit;     // new split applies to already-accrued unclaimedFees
managementFee = _managementFee;
```

- `fee` is consumed in `_netGainAfterFees` (`contracts/IdleCDOEpochVariant.sol:889-894`) and at fee settlement in `stopEpoch` (`contracts/IdleCDOEpochVariant.sol:435-459`), where the whole `_grossInterest` of the epoch is reduced by the *current* `fee`. Because epoch interest is fixed at `startEpoch` (`expectedEpochInterest`) and depositors cannot exit mid-epoch (a withdrawal receipt only settles after the *next* epoch, `_withdrawRequestManagementFeeDuration` at `IdleCDOEpochVariant.sol:925-931`), a fee increase mid-epoch retroactively taxes yield that lenders accrued under the previous rate — the exact "traders agreed on previous fees" invariant the Perennial report describes.
- `feeSplit` is read at payout time in `_feeReceiverAmount` (`IdleCDOCreditVault.sol:596-598`). `unclaimedFees` accrued over past periods under the old split are re-split under the new one, retroactively redirecting already-earned fee accrual between `feeReceiver` and `owner()`.
- A lender can also sandwich the change: monitoring the mempool, a tranche holder can call `requestWithdraw` (`IdleCDOEpochVariant.sol:739-791`) in the same phase as a pending fee-raising `setFeeParams`, locking `_totalWithdrawFees` at the old `fee` for their receipt while remaining depositors absorb the new rate — or conversely a user requesting after a queued-but-unspecified change has no protection since the receipt is computed at request time with whatever rate is live.

### Impact Explanation
- Retroactive loss of accrued yield: raising `fee` from `f0` to `f1` at time `t` inside an epoch charges `(f1 - f0)` on all interest accrued before `t`, not just the remainder. For a 365-day epoch, NAV `N`, APR `a`, and fee raised 0 → 10% at day 300, depositors lose ≈ `N * a * (300/365) * 10%` of yield they had already effectively earned — a direct, quantified reduction of `lastEpochInterest`/tranche NAV at `stopEpoch`.
- Retroactive redirection of accrued fees via `feeSplit` on the standing `unclaimedFees` balance.
- The inconsistency is structural: `managementFee` demonstrates the intended "checkpoint-then-update" pattern; `fee` and `feeSplit` bypass it.

### Likelihood Explanation
Requires an owner `setFeeParams` call while an epoch is running (owner is honest, but the call is a legitimate routine operation, and lenders deposit under the fee prevailing at deposit time). No existing guard prevents it: `setFeeParams` is callable in any epoch phase, `requestWithdraw` receipts lock fees at request time only for the requester, and `_accrueManagementFee` checkpoints only the management-fee component. Likelihood is moderate (parameter changes are infrequent), but the loss is deterministic whenever it happens mid-epoch.

### Recommendation
Apply the same checkpoint-then-apply semantics used for `managementFee` to `fee` and `feeSplit`:
- Queue `fee`/`feeSplit` changes when an epoch is running and apply them at `stopEpoch`/`startEpoch` boundary (pending-fee storage consumed in `stopEpoch` before `_updateAccounting`), or
- At minimum, settle/distribute the existing `unclaimedFees` under the current `feeSplit` and snapshot the fee applicable to the in-flight `expectedEpochInterest` at `startEpoch`, so a mid-epoch change only affects the next epoch's accrual.

### Proof of Concept
Foundry test (drop into `test/foundry/IdleCreditVault.t.sol`):

```solidity
function testFeeRaiseMidEpochRetroactivelyTaxesAccruedInterest() external {
    uint256 amount = 10_000 * ONE_SCALE;

    // 0% performance fee at deposit/start; 10% apr, 365-day epoch, no buffer
    _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, 0);
    vm.startPrank(manager);
    cdoEpoch.setEpochParams(365 days, 0);
    IdleCreditVault(address(strategy)).setAprs(10e18, 10e18);
    vm.stopPrank();

    idleCDO.depositAA(amount);
    _transferBurnedTrancheTokens(address(this), true);
    _startEpochAndCheckPrices(0);
    uint256 expectedInterest = cdoEpoch.expectedEpochInterest(); // ~1000

    // 300 days in: interest already accrued under fee = 0
    vm.warp(cdoEpoch.epochEndDate() - 65 days);

    // owner raises performance fee to 10% — no checkpoint/queue for `fee`
    _setFeeParams(TL_MULTISIG, 10_000, FULL_ALLOC, 0);

    deal(defaultUnderlying, borrower, _expectedFundsEndEpoch());
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(10e18, 0);

    // 10% is charged on the FULL epoch interest, including the 300 days
    // accrued under fee = 0 — depositors never had a chance to exit.
    assertEq(
        cdoEpoch.lastEpochInterest(),
        expectedInterest - expectedInterest / 10,
        'new fee retroactively applied to pre-change accrual'
    );
}
```

Uncertain points I could not fully verify within the tool-call budget: whether this retroactive-fee behavior is already documented/acknowledged as accepted admin risk for this deployment, and the exact `_updateAccounting`/`stopEpoch` fee path in `_mintInterest` mode (the retroactivity holds regardless, since `fee` is read live at settlement). If a README/audit note already states fees may change at any time, this reduces to a known-issue/informational item.