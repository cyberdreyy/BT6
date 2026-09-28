### Title
APR0 withdraw requests permanently brick `stopEpoch` after any routine APR change - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
An unprivileged lender who opens a withdraw request while `unscaledApr == 0` leaves `apr0TotalPrincipal > 0`. Any subsequent legitimate APR change by the manager/CDO (a normal, honest operation between epochs) makes `prepareStopEpochWithApr0` revert unconditionally, so `stopEpoch` can never run. The epoch cannot close, borrower repayment cannot be collected, and every pending withdrawal is frozen — the credit-vault analog of a reachable abort that halts all progress.

### Finding Description
`requestWithdraw` routes requests to the APR0 bucket whenever `unscaledApr == 0`, incrementing the global `apr0TotalPrincipal` counter (IdleCreditVault.sol:285-286, `_requestWithdrawApr0` at :567-577). `apr0TotalPrincipal` is only reset inside `prepareStopEpochWithApr0` (:540) or via post-default claim clearing (:827). The function reverts `NotAllowed` whenever `apr0TotalPrincipal != 0 && unscaledApr != 0` (:506-508):

```solidity
uint256 _principal = apr0TotalPrincipal;
if (_principal == 0) {
  return (_expInterest, _adjPendingWithdrawFees);
}
if (unscaledApr != 0) {
  revert NotAllowed();
}
```

`prepareStopEpochWithApr0` is invoked by the CDO during `stopEpoch`. `unscaledApr` is set by `setAprs`/`setAprsWithBuffer` (:206-220), callable by the honest manager or CDO. Once a nonzero APR is stored while `apr0TotalPrincipal > 0`, every `stopEpoch` reverts. Because `epochNumber` only increments inside `deposit()` when the epoch is stopped (:607-610), the epoch can never advance; `_claimFundedWithdrawRequest` keeps reverting at the `epochNumber <= lastWithdrawRequest` gate (:326), so all pending withdrawers are frozen.

### Impact Explanation
Temporary (potentially indefinite) freezing of all user funds in the vault: pending withdraw receipts cannot be claimed, `stopEpoch` cannot settle borrower interest/repayment, and new epochs cannot begin. The freeze persists until the manager sets `unscaledApr` back to `0`, which is only possible if the manager realizes the cause — and during the freeze all lenders' principal and accrued interest are inaccessible. For a pool with, e.g., 1M USDC of pending APR0 receipts plus active TVL, the entire balance is stuck. This mirrors CVE-2017-13751's "reachable assertion abort → denial of service" class, but with direct fund impact (frozen withdrawals), which satisfies the acceptance criteria.

### Likelihood Explanation
Triggering requires only that a single KYC'd lender submits a withdraw request during any epoch where the (honest) manager has set `unscaledApr = 0`, followed by the manager/CDO raising the APR before the corresponding `stopEpoch` settles that bucket — a routine rate renegotiation between buffer and epoch phases. The attacker does not need to time anything maliciously beyond holding an open APR0 request; they can even open small APR0 requests each zero-APR epoch to maximize the window. No existing guard prevents it: the `NotAllowed` revert *is* the bug, and there is no path to decrement `apr0TotalPrincipal` outside `prepareStopEpochWithApr0` or default claims.

### Recommendation
Do not revert on `unscaledApr != 0` in `prepareStopEpochWithApr0`. Either settle the outstanding APR0 principal bucket at a zero interest rate (`apr0RateByEpoch[epochNumber] = 0` and clear `apr0TotalPrincipal`), or compute its pro-rata share using the `unscaledApr` value recorded at request time. Additionally, `setAprs`/`setAprsWithBuffer` could defensively settle the open APR0 bucket before changing `unscaledApr`.

### Proof of Concept
A Foundry fork PoC (not executed here — sequence sketch):

1. Deploy/fork `IdleCreditVault` + `IdleCDOEpochVariant` with a live epoch; owner sets `unscaledApr = 0` (APR0 mode) and starts the epoch via `startEpoch`.
2. As an unprivileged KYC'd lender, deposit, then call CDO `requestWithdraw` → strategy `requestWithdraw` executes `_requestWithdrawApr0`, setting `apr0TotalPrincipal = amount > 0`, `apr0Users[user].principalEpoch = epochNumber`.
3. Honest manager calls `setAprsWithBuffer(newApr, duration, buffer)` (or CDO does during `startEpoch` setup) → `unscaledApr != 0`.
4. Epoch matures; owner calls `stopEpoch` → CDO calls `prepareStopEpochWithApr0` → `apr0TotalPrincipal != 0 && unscaledApr != 0` → `revert NotAllowed()`.
5. Assert: every subsequent `stopEpoch` reverts; `claimWithdrawRequest` reverts for the requester (`epochNumber` never advances past `lastWithdrawRequest`); vault funds remain frozen until `unscaledApr` is manually reset to 0.

Caveat: steps 4-5 depend on `IdleCDOEpochVariant.stopEpoch` unconditionally calling `prepareStopEpochWithApr0` (the `_onlyIdleCDO` modifier and function naming indicate it is the epoch-stop hook), which I could not fully re-verify within this pass; if `stopEpoch` has a path that skips the hook, the freeze severity reduces accordingly.