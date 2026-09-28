### Title
Settled APR0 principal loses its epoch association and escapes the `lossRecoveryPriceByEpoch` haircut, overpaying early claimants and insolventing later ones - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The kernel bug stores a pointer to transient data and dereferences its fields later, after the referent is gone. The credit-vault analog is in `IdleCreditVault._settleApr0` / `_withdrawClaimAmountsForEpoch`: when an APR0 withdraw request settles, `apr0Users[_user].principal` is moved to `settledPrincipal` and `principalEpoch` is cleared to 0 (contracts/strategies/idle/IdleCreditVault.sol:556-564). The epoch key — the only handle the loss-adjusted claim path uses to find which receipts belong to a haircut epoch — is discarded while the principal remains inside the global `pendingWithdraws` basis. When `collectWithdrawFunds` later records `lossRecoveryPriceByEpoch[epochNumber]` on a shortfall, the loss price was computed over a `pendingBasis` that includes that settled principal, but `_claimLossAdjustedWithdrawRequest` can no longer attribute it to the loss epoch, so it is paid at par through `_claimFundedWithdrawRequest`.

### Finding Description
- `requestWithdraw` for APR0 adds `_amount` to `pendingWithdraws` and to `apr0Users[_user].principal` tagged with `principalEpoch = epochNumber` (lines 279, 567-577).
- On a later request or claim, `_settleApr0` moves `principal` into `settledPrincipal` and zeroes `principalEpoch` (lines 556-564). The funds stay in `pendingWithdraws` — they are still unclaimed borrower-backed receipts.
- On a `stopEpochWithDuration` loss, the CDO calls `collectWithdrawFunds(_amount < pendingBasis)`, which stores `lossRecoveryPriceByEpoch[epochNumber] = _amount * RECOVERY_FULL / pendingBasis` and zeroes `pendingWithdraws` (lines 411-426). The haircut denominator included the settled principal.
- When the user claims, `_claimLossAdjustedWithdrawRequest` reads `lastWithdrawRequest[_user]` → `lossRecoveryPriceByEpoch[lossEpoch]` and computes `claimBasis` via `_withdrawClaimAmountsForEpoch`, which only counts `withdrawsRequestsByEpoch[user][epoch]` and `apr0User.principal` if `principalEpoch == epoch` (lines 789-800, 862-880). Settled principal matches no epoch — the pointer's referent was cleared — so it is excluded from the haircut.
- `_claimFundedWithdrawRequest` then pays `normalAmount + settledPrincipal + principal + settledInterest` at par via `_transferFundedClaim` (lines 338-349).

Broken invariant: loss waterfall / fair burn. The aggregate funded amount is `pendingBasis - pendingLoss`, but total claims equal `pendingBasis - pendingLoss + settledPrincipalShareOfLoss`. The settled-principal portion of `pendingLoss` is never actually borne by anyone.

### Impact Explanation
Direct insolvency. For each unit of settled APR0 principal outstanding in the loss epoch, `settledPrincipal * (1 - lossRecoveryPrice/RECOVERY_FULL)` underlying is paid out that was never funded. Early claimants (the attacker can be the settled-APR0 requester, or simply claim ordering) drain the strategy's funded underlying; subsequent loss-epoch claimants' `claimWithdrawRequest` calls revert on insufficient balance — permanent freezing/theft of their funded claims. Quantified loss ≈ `settledApr0Principal_in_lossEpoch * pendingLoss / pendingBasis`.

### Likelihood Explanation
Requires (a) APR0 mode (`unscaledApr == 0`), (b) a user whose APR0 request settled (one intervening `stopEpoch` plus a second request or claim path), and (c) a subsequent `stopEpochWithDuration`/partial-funding loss while that settled principal is still unclaimed. All are normal protocol operations reachable by an unprivileged KYC'd lender timing their requests; no privileged misbehavior needed. The honest manager/borrower sequence (lossy stop, partial `collectWithdrawFunds`) is within scope.

### Recommendation
Track per-epoch settled APR0 principal (e.g., `settledPrincipalByEpoch[user][epoch]` or keep `principalEpoch` on the settled bucket) so `_withdrawClaimAmountsForEpoch` includes it in the loss-epoch `claimBasis`, and burn the corresponding receipt. Alternatively, apply the loss haircut pro-rata at settlement time when `lossRecoveryPriceByEpoch[principalEpoch]` is already set.

### Proof of Concept
```solidity
// Foundry fork test against IdleCreditVault + IdleCDOEpochVariant (APR0 mode)
function testSettledApr0EscapesLossHaircut() external {
    // Setup: pool with unscaledApr == 0, user and otherLP both deposited.
    // Epoch N:
    idleCDO.requestWithdraw(userApr0Amount, userTranche);      // APR0 request, principalEpoch = N
    stopEpoch();                                               // epoch -> N+1

    // Epoch N+1: user makes a second APR0 request.
    // _requestWithdrawApr0 -> _settleApr0 moves epoch-N principal to settledPrincipal
    // and clears principalEpoch. Both amounts remain inside pendingWithdraws.
    idleCDO.requestWithdraw(userApr0Amount2, userTranche);

    // Borrower repays short: manager stops epoch N+1 with a loss.
    // collectWithdrawFunds(funded < pendingBasis) stores
    // lossRecoveryPriceByEpoch[N+1] = funded * RECOVERY_FULL / pendingBasis,
    // where pendingBasis includes the settled epoch-N principal.
    stopEpochWithDuration(lossAmount);

    // Attack: user claims. _claimLossAdjustedWithdrawRequest haircuts only the
    // epoch-N+1 receipt; settledPrincipal is paid at par in _claimFundedWithdrawRequest.
    uint256 balPre = underlying.balanceOf(user);
    idleCDO.claimWithdrawRequest();
    uint256 got = underlying.balanceOf(user) - balPre;

    // got > funded share attributable to user: the settled principal's loss share
    // was paid from underlying owed to other epoch-N+1 claimants.
    // Final claimant: idleCDO.claimWithdrawRequest() reverts (insufficient strategy balance).
    vm.expectRevert();
    idleCDO.claimWithdrawRequest(); // otherLP permanently unable to claim funded receipt
}
```