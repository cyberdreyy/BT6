### Title
APR=0 withdraw receipts escape `stopEpochWithDuration` loss haircuts and claim at par - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
The WebKit bug class (memory corruption → attacker-controlled state leading to arbitrary impact) maps here to stale/corrupted receipt accounting in the epoch withdrawal state machine. In `IdleCreditVault`, when `stopEpochWithDuration` realizes a loss, `collectWithdrawFunds` records a haircut via `lossRecoveryPriceByEpoch[epochNumber]` and zeroes `pendingWithdraws`. Normal per-epoch receipts are later haircut in `_claimLossAdjustedWithdrawRequest`. APR=0 receipts, however, are never haircut: their principal/interest live in `apr0Users` (not `withdrawsRequestsByEpoch`), `_settleApr0` settles them at the full `apr0RateByEpoch` rate, and `_claimFundedWithdrawRequest` pays `settledPrincipal + settledInterest` at par through `_transferFundedClaim` — which only excludes `defaultRecoveryReserve`, not loss-recovery obligations. An unprivileged lender with an APR0 receipt therefore withdraws un-haircut funds, forcing the entire realized loss onto normal receipt holders and active LPs.

### Finding Description
In `requestWithdraw` (strategy side), APR=0 flows store the receipt only in `apr0Users[_user]` and `apr0TotalPrincipal`; `withdrawsRequestsByEpoch` and `withdrawsRequests` are untouched ([IdleCreditVault.sol:283-294](contracts/strategies/idle/IdleCreditVault.sol)). On a loss stop, `collectWithdrawFunds` computes `lossRecoveryPrice = funded * 1e18 / pendingBasis` where `pendingBasis` includes APR0 principal and accrued APR0 interest ([lines 411-421](contracts/strategies/idle/IdleCreditVault.sol)). The aggregate basis is haircut, but the per-user APR0 buckets are not reduced by `lossRecoveryPrice`.

When the user later calls `claimWithdrawRequest` via the CDO:
- `_claimDefaultedWithdrawRequest` is skipped (no default).
- `_claimLossAdjustedWithdrawRequest` calls `_withdrawClaimAmountsForEpoch`, which only counts `apr0User.principal` when `principalEpoch == claimEpoch` — after `_settleApr0`, `principal` is 0 and `principalEpoch` is cleared, so `claimBasis` is 0 and no haircut is applied ([lines 862-881](contracts/strategies/idle/IdleCreditVault.sol)).
- `_claimFundedWithdrawRequest` then calls `_settleApr0`, which moves `principal → settledPrincipal` and accrues `settledInterest` at the full `apr0RateByEpoch`, and pays `normalAmount + settledPrincipal + principal + settledInterest` at par ([lines 330-349, 545-565](contracts/strategies/idle/IdleCreditVault.sol)).

The blocking guard in `requestWithdraw` ([lines 263-271](contracts/strategies/idle/IdleCreditVault.sol)) also fails to catch this after settlement: it checks `apr0Users[_user].principal != 0 && principalEpoch == lossEpoch`, but settlement zeroes `principal`, so an APR0 user can also open a new request and move `lastWithdrawRequest` past the loss epoch — permanently orphaning the haircut even if some residual basis remained.

### Impact Explanation
Direct theft / unfair loss escape with quantified impact. The loss that `previewLossAdjustedWithdrawFunds`/`collectWithdrawFunds` allocated pro-rata to the pending bucket is paid out only by normal receipts; APR0 receipts are made whole at par plus full APR0 interest. Effect: APR0 claimants recover 100% while normal same-epoch claimants recover `lossRecoveryPrice/1e18`. If the strategy's funded balance only covers the intended haircut payouts, APR0 claimants extracting par value either (a) drain underlying belonging to other claimants, or (b) leave later claimants' `_transferFundedClaim` underfunded — insolvency for the residual claimants. Broken invariant: loss socialization / "one receipt one payout" fairness across the pending bucket.

### Likelihood Explanation
Requires only unprivileged actions: a KYC-passed lender makes an APR0 `requestWithdraw` (when `unscaledApr == 0`), then an honest manager executes `stopEpochWithDuration(_lossAmount)` with a partial fund from the borrower — a normal loss-mode path, not a privileged attack. The APR0 user then calls `claimWithdrawRequest` after the epoch boundary. No malicious privileged role, no oracle manipulation. The APR0 mode is a supported deployment configuration explicitly handled by `_requestWithdrawApr0` and `prepareStopEpochWithApr0`.

### Recommendation
Track APR0 receipts per epoch (`apr0PrincipalByEpoch[user][epoch]`) or record the loss multiplier on the user's bucket: on the first post-loss interaction, apply `lossRecoveryPriceByEpoch[principalEpoch]` to `principal` before moving it to `settledPrincipal` in `_settleApr0`, and scale `settledInterest` the same way. Alternatively, make `_withdrawClaimAmountsForEpoch` also consider `settledPrincipal`/`settledInterest` attributed to the loss epoch, and extend the `requestWithdraw` re-request guard to block when any APR0 bucket (open or settled) originated in an epoch with a nonzero `lossRecoveryPriceByEpoch`.

### Proof of Concept
Foundry fork outline (pattern follows existing tests in `test/foundry/IdleCreditVault.t.sol`, e.g. `testApr0WithdrawRollsAcrossEpochsNoDoubleAccrual`):

```solidity
// Setup: two KYC'd lenders, userA (APR0 requester) and userB (normal receipt).
idleCDO.depositAA(10_000e6); // userA
// force APR to 0 via _forceLastEpochAprToZero() helper / manager setAprs(0, 0)
uint256 principalA = cdoEpoch.requestWithdraw(balA, address(AAtranche)); // apr0 path
// userB does a normal fixed-APR receipt in the same epoch would need apr != 0;
// instead compare userA claim vs lossRecoveryPrice applied to aggregate.

vm.prank(manager); cdoEpoch.startEpoch();
vm.warp(cdoEpoch.epochEndDate() + 1);
// Borrower funds only part of pendingWithdraws → realized loss
uint256 pending = strategy.pendingWithdraws();
deal(underlying, borrower, pending / 2 + activeInterest);
vm.prank(manager);
cdoEpoch.stopEpochWithDuration(lossAmount); // triggers collectWithdrawFunds(partial)
// lossRecoveryPriceByEpoch[epoch] = 0.5e18

// userA claims AFTER settlement (epochNumber bumped)
uint256 pre = underlying.balanceOf(userA);
vm.prank(userA); cdoEpoch.claimWithdrawRequest();
// BUG: userA receives principalA + full apr0 interest at par
// Expected: principalA * lossRecoveryPrice/1e18 (+ scaled interest)
assertGt(underlying.balanceOf(userA) - pre,
         principalA * strategy.lossRecoveryPriceByEpoch(lossEpoch) / 1e18);
```

Expected PoC result: the claimed amount equals the un-haircut receipt value (plus `apr0RateByEpoch` interest), exceeding the haircut entitlement by `(1 - lossRecoveryPrice/1e18) * claimBasis`, which is extracted from the strategy's funded balance at the expense of other claimants.