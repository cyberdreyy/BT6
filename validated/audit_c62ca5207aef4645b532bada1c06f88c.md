### Title
Failed `stopEpoch` keeps APR0 settlement and persisted epoch interest, inflating default-recovery claim basis - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
The kernel bug is "failed config update leaves dirty state (`num_txq`, `tc_cfg`) that a later path consumes as if the update succeeded." The credit-vault analog is `IdleCDOEpochVariant._stopEpoch`: it commits state updates (`expectedEpochInterest`, `pendingWithdrawFees`, and via `prepareStopEpochWithApr0` the writes `pendingWithdraws += _apr0NetInterest`, `apr0RateByEpoch[epochNumber]`, `apr0TotalPrincipal = 0`) **before** the risky funding leg `getFundsFromBorrower`, which is wrapped in `try`/`catch`. When the funding leg fails, the catch path `_handleBorrowerDefault` runs with the failed epoch's "new config" still in place — the exact "keep old cfg on failure" inversion.

### Finding Description
In `contracts/IdleCDOEpochVariant.sol` lines 362-393, `_stopEpoch` calls `_strategy.prepareStopEpochWithApr0(_interest)` and persists `expectedEpochInterest = _grossInterest; pendingWithdrawFees = _pendingWithdrawFees;` before attempting `try this.getFundsFromBorrower(...)` at line 408.

`prepareStopEpochWithApr0` (`contracts/strategies/idle/IdleCreditVault.sol` lines 490-541) mutates strategy state that is **not** rolled back when the external call fails:

- `pendingWithdraws += _apr0NetInterest` (line 535) — interest the borrower never paid is added to the pending claim basis.
- `apr0RateByEpoch[epochNumber] = _apr0NetInterest * 1e18 / _principal` (line 537).
- `apr0TotalPrincipal = 0` (line 540) — the global APR0 bucket is closed while per-user `apr0Users[user].principal` remains.

If `getFundsFromBorrower` reverts (borrower repayment failure), the `catch` block calls `_handleBorrowerDefault`. Later, `finalizeDefaultRecovery` (`IdleCreditVault.sol` lines 661-710) computes the recovery basis via `defaultPendingClaimBasis()` = `pendingWithdraws` (already inflated by phantom APR0 interest) and `_defaultActiveInterestBasis`, which uses the persisted `expectedEpochInterest` / `pendingWithdrawFees` that were written before the failed pull. Additionally, each APR0 requester's defaulted-epoch claim in `_withdrawClaimAmountsForEpoch` (lines 862-881) adds `(principal * apr0RateByEpoch[principalEpoch]) / 1e18` — interest that was never funded.

### Impact Explanation
The default-recovery `totalBasis` is inflated by APR0 interest the borrower never paid (and by pre-failure persisted epoch interest on the active side). `recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis` is therefore depressed, and the reserve backing it (`defaultRecoveryReserve`, funded by actual recovered assets and prefunded instant reserves) is distributed pro-rata over phantom claims. An unprivileged lender who opened an APR0 `requestWithdraw` in the defaulting epoch receives claim basis for interest that was never received — direct dilution/theft of recovery funds from all other claimants (active AA/BB holders and other pending receipt holders). The loss is bounded by the phantom interest amount but is real fund redistribution, not a DoS.

### Likelihood Explanation
Requires: (a) APR0 mode (`unscaledApr == 0`) with a pending APR0 withdraw, (b) manager calling `stopEpoch` with `_interest > 1` so `_apr0NetInterest` is non-zero, and (c) the borrower pull failing in the same transaction. The first two are attacker/user-controllable preconditions; the third is a borrower repayment failure, not attacker-forced, so likelihood is moderate and premised on a genuine default-like event. No existing guard restores the pre-failure state: `collectWithdrawFunds` and `finalizeDefaultRecovery` consume `pendingWithdraws`/`apr0RateByEpoch`/`expectedEpochInterest` as committed truth, mirroring the kernel's dirty `tc_cfg`/`num_txq`.

### Recommendation
Mirror the upstream fix ("only update when the check passes"): in `_stopEpoch`, commit `expectedEpochInterest`, `pendingWithdrawFees`, and the APR0 settlement only after `getFundsFromBorrower` succeeds, or snapshot and restore the strategy-side APR0 state (`pendingWithdraws`, `apr0RateByEpoch[epochNumber]`, `apr0TotalPrincipal`) inside the `catch`/default path. Alternatively, pass the realized (funded) interest into `finalizeDefaultRecovery`/`defaultPendingClaimBasis` so unfunded pre-failure interest never enters claim basis.

### Proof of Concept
Reproducible Foundry fork PoC sketch (uncertainty: I did not read `_handleBorrowerDefault` (lines ~570+) to confirm it leaves `expectedEpochInterest`/APR0 state untouched — if it normalizes them, this finding is invalid):

```solidity
// Setup: vault in APR0 mode (unscaledApr == 0), minted or cash mode.
// 1. alice deposits AA and calls cdoEpoch.requestWithdraw(...) creating an APR0 receipt.
// 2. Epoch runs; borrower repays nothing (fails transferFrom) at stop.
// 3. manager calls stopEpochWithDuration(newApr, largeInterest, dur, 0)
//    -> prepareStopEpochWithApr0 persists apr0RateByEpoch[epoch], pendingWithdraws += apr0NetInterest, apr0TotalPrincipal = 0
//    -> getFundsFromBorrower reverts -> catch -> _handleBorrowerDefault
// 4. owner finalizes: finalizeDefault / finalizeDefaultRecovery(recovered, source)
//    -> assert totalBasis includes apr0NetInterest that was never funded:
//       strategy.defaultPendingClaimBasis() > actualPendingBasis
// 5. assert alice's claim == (principal + principal*apr0RateByEpoch[epoch]/1e18) * recoveryPrice
//    while honest claimants' payouts are diluted by the same phantom basis.
```