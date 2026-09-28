### Title
Loss-adjusted withdraw haircut keyed to funding epoch can be bypassed by receipts requested in earlier epochs, letting users claim at par and drain funded withdrawals - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`collectWithdrawFunds` applies a `stopEpochWithDuration` loss to the aggregate `pendingWithdraws` and stores the haircut under the *current* `epochNumber` (`lossRecoveryPriceByEpoch[epochNumber]`, line 421). But per-user receipts are recorded under the *request* epoch (`withdrawsRequestsByEpoch[_user][currentEpoch]`, line 293) and `lastWithdrawRequest[_user]` stores the request epoch (line 282). A user whose pending receipt was requested in an earlier epoch reads `lossRecoveryPriceByEpoch[lastWithdrawRequest]` — which is `0` — in `_claimLossAdjustedWithdrawRequest` (line 790-792) and falls through to `_claimFundedWithdrawRequest`, which pays the full basis at par (line 341-349). The user withdraws funds that were haircutted, directly stealing the haircut difference from the funded pool and leaving later claimants underfunded.

### Finding Description
When a realized loss occurs at `stopEpochWithDuration`, the CDO calls `collectWithdrawFunds(_amount)` with `_amount < pendingBasis`. The strategy then:

- computes `lossRecoveryPrice = _amount * 1e18 / pendingBasis`,
- sets `pendingWithdraws = 0`,
- stores `lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice` (line 421),
- pulls only `_amount` (the funded, post-loss amount) into the strategy (line 428).

Later, `claimWithdrawRequest` calls `_claimLossAdjustedWithdrawRequest`, which looks up `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]`. Because `lastWithdrawRequest` holds the epoch in which the user *requested*, not the epoch in which the loss was *applied*, any receipt requested in epoch `N < M` (loss epoch) sees a zero price and skips the haircut path entirely. `_claimFundedWithdrawRequest` then only requires `epochNumber > lastWithdrawRequest[_user]` (line 326), which holds since `stopEpoch`/`deposit` bumps `epochNumber` past `N`, and pays `withdrawsRequests[_user]` at par from `_transferFundedClaim`.

The broken invariant is the loss waterfall: the haircut is computed against an aggregate `pendingBasis` that includes old receipts, but the stored haircut key only reaches receipts whose `lastWithdrawRequest` equals the loss epoch. Old receipts keep a par claim while their funding was reduced — an over-read of entitlement versus what was actually validated/funded, mirroring the kernel bug where the bounds check covers a prefix but the read extends past it.

Note: the guard at lines 263-271 only blocks a *new* `requestWithdraw` when `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` is non-zero; it does not prevent the mismatch for a receipt requested before the loss epoch, and `_transferFundedClaim`'s reserve guard (lines 899-905) does not apply pre-default (`reserve == 0`).

### Impact Explanation
Direct theft / insolvency. An attacker with a stale, unclaimed withdraw receipt claims `basis` while the strategy only received `basis * lossRecoveryPrice / 1e18` for the whole pending bucket. The excess `basis * (1 - lossRecoveryPrice)` is paid out of funds earmarked for other pending withdraw claimants (or, post-default, is only limited by the reserve check). Quantified loss: up to the attacker's full receipt basis times the haircut deficit; with a 50% loss price the attacker doubles their claim relative to entitlement, and the last claimants' transfers revert or are short-changed.

### Likelihood Explanation
Preconditions: (1) a pending withdraw receipt requested in an epoch earlier than the loss epoch — trivially achieved by requesting during a buffer period and simply not claiming across subsequent epochs (the code comments at lines 323-324 explicitly document that requests can remain unclaimed across epochs); (2) a `stopEpochWithDuration` loss — this is an honest manager/borrower action that the attacker sequences around, not attacker-controlled. Attacker is a KYC-passing tranche holder, which is in scope. No privileged collusion needed.

### Recommendation
Store the loss haircut against every epoch that contributes to `pendingBasis`, or decouple the lookup: record a `lossEpoch`/global pending epoch range so `_claimLossAdjustedWithdrawRequest` checks whether `withdrawsRequestsByEpoch[_user][e] != 0` for any `e` covered by the haircut (e.g., iterate or store `lossAppliedAtEpoch` and apply the price to all receipts with `lastWithdrawRequest <= lossAppliedAtEpoch`). Alternatively, track the earliest pending request epoch and route claims through the loss path whenever `lossRecoveryPriceByEpoch` was set after the request epoch.

### Proof of Concept
Fork test against `IdleCreditVault` + `IdleCDOEpochVariant` (as in `test/foundry/IdleCreditVault.t.sol`):

```solidity
function testLossHaircutBypassOldEpochReceipt() public {
  // fixture: deposit AA via CDO, epoch 1 running normally
  _depositAA(1000e18);

  // Epoch 1 stops, epoch 2 not yet started (buffer): request withdraw
  // receipt recorded at withdrawsRequestsByEpoch[user][N], lastWithdrawRequest = N
  cdoEpoch.requestWithdraw(500e18, address(AAtranche));

  // Epochs progress without user claiming: start epoch N+1, run, stop...
  // At stopEpochWithDuration of epoch M > N a loss L is applied:
  // collectWithdrawFunds(funded < pendingBasis) ->
  //   lossRecoveryPriceByEpoch[M] = funded*1e18/pendingBasis; pendingWithdraws = 0

  // USER CLAIM
  vm.prank(user);
  cdoEpoch.claimWithdrawRequest(); // routes to strategy.claimWithdrawRequest

  // _claimLossAdjustedWithdrawRequest: lossRecoveryPriceByEpoch[N] == 0 -> skipped
  // _claimFundedWithdrawRequest: epochNumber(M+1) > N -> pays full 500e18-equivalent at par
  // assert: user received basis while strategy only holds basis*price for the bucket
  assertGt(underlying.balanceOf(user), fundedShareExpected);
  // subsequent claimants' claims revert / underpay -> insolvency
}
```

Confidence note: confirmed by code analysis of `IdleCreditVault.sol` lines 243-295, 326-349, 411-430, 789-801; the fix-style comments around loss accounting suggest heavy prior hardening, so this path should be double-checked against the project's known-issues list, but no existing guard in the code stops the described sequence.