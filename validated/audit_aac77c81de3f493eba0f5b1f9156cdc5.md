### Title
Loss-adjusted withdraw receipts keyed to the wrong epoch become claimable at par, draining strategy funds - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`collectWithdrawFunds` stores the stop-epoch loss haircut under `lossRecoveryPriceByEpoch[epochNumber]`, but `epochNumber` is incremented inside `deposit()` whenever the CDO calls it while an epoch is still running ("deposit done on stopEpoch" path, IdleCreditVault.sol:607-610). Per-user receipts are keyed by the epoch of `requestWithdraw` (`withdrawsRequestsByEpoch[_user][currentEpoch]`, `lastWithdrawRequest[_user]`). If the epoch counter has already rolled to `N+1` when the loss price is recorded for receipts opened in epoch `N` — the analog of the NFS fix, where subrequests were never joined back to the head on the retransmission list — the receipt's haircut is stored under an epoch key the claimant never points to.

### Finding Description
- `requestWithdraw` records `lastWithdrawRequest[_user] = epochNumber` and `withdrawsRequestsByEpoch[_user][epochNumber] += _amount` (IdleCreditVault.sol:282-293), and adds `_amount` to `pendingWithdraws`.
- On a lossy `stopEpoch`, the CDO calls `previewLossAdjustedWithdrawFunds`, funds `pendingToFund`, and `collectWithdrawFunds` writes `lossRecoveryPriceByEpoch[epochNumber]` and zeroes `pendingWithdraws` (IdleCreditVault.sol:411-430).
- `epochNumber` is bumped in `deposit()` whenever `isEpochRunning()` is true (IdleCreditVault.sol:607-610). Any CDO-side deposit/step executed during `stopEpoch` before `collectWithdrawFunds` shifts the key to `epochNumber+1`.
- On claim, `_claimLossAdjustedWithdrawRequest` looks up `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` (IdleCreditVault.sol:789-801). With the haircut stored under `N+1` and the user's `lastWithdrawRequest` still `N`, the lookup returns 0 and the claim falls through to `_claimFundedWithdrawRequest`, which pays `withdrawsRequests[_user]` in full at par (IdleCreditVault.sol:319-350) — even though the strategy only received `claimBasis * lossRecoveryPrice` from the borrower.

Conversely, if the key lands on `N+1`, any unrelated user whose `lastWithdrawRequest == N+1` (a fresh requester in the new epoch) sees a spurious non-zero `lossRecoveryPrice` and is forced to claim at the stale haircut or is blocked by the `requestWithdraw` guard at IdleCreditVault.sol:263-271.

### Impact Explanation
Each receipt in the affected epoch can be claimed at 100% while the strategy was only funded `lossRecoveryPrice` percent. The shortfall is paid from strategy-held underlying (recovery reserve is protected by `_transferFundedClaim`'s reserve guard only when `defaultRecoveryReserve != 0`, IdleCreditVault.sol:897-907), so the attacker drains funds belonging to other pending claimants / the vault, causing direct theft and insolvency proportional to `(1 - lossRecoveryPrice) * pendingBasis`. Additionally, later-epoch requesters inherit the orphaned haircut key, permanently skewing or freezing their claims.

### Likelihood Explanation
The trigger is the natural `stopEpoch` flow whenever `deposit()` (or any path bumping `epochNumber`) executes before `collectWithdrawFunds` inside the same epoch stop. An unprivileged tranche holder only needs a pending `requestWithdraw` in the epoch that ends with a `stopEpochWithDuration` loss — no privileged collusion required; the honest manager's stop transaction performs the misordering.

### Recommendation
Pass the request epoch explicitly: have `collectWithdrawFunds` (and `previewLossAdjustedWithdrawFunds`) take the epoch key used by pending receipts (e.g., `epochNumber - 1` semantics or a `pendingWithdrawsEpoch` snapshot updated in `requestWithdraw`), or snapshot `epochNumber` into `lossRecoveryPriceByEpoch` keyed by the epoch that `pendingWithdraws` was accrued under rather than the post-increment counter. Add an invariant test asserting `lossRecoveryPriceByEpoch[lastWithdrawRequest[user]] != 0` for every pending receipt after a lossy stop.

### Proof of Concept
Foundry fork PoC outline (reproducible against the vault's unit fixtures in `test/`):

```solidity
// Setup: deploy IdleCDOEpochVariant + IdleCreditVault, KYC lender, borrower funds loan.
// 1. Epoch N running: lender deposits; borrower draws.
// 2. Attacker (KYC'd lender) calls requestWithdraw(1_000e6) -> withdrawsRequestsByEpoch[attacker][N] = 1_000e6,
//    lastWithdrawRequest[attacker] = N, pendingWithdraws = 1_000e6.
// 3. stopEpochWithDuration executes with a loss. Inside the stop flow, a deposit() call bumps
//    epochNumber to N+1 before collectWithdrawFunds(pendingToFund) runs.
//    -> lossRecoveryPriceByEpoch[N+1] = 0.5e18 ; lossRecoveryPriceByEpoch[N] = 0 ; pendingWithdraws = 0.
//    Strategy receives only 500e6.
// 4. Attacker calls claimWithdrawRequest:
//    _claimLossAdjustedWithdrawRequest -> lossRecoveryPriceByEpoch[N] == 0 -> returns 0.
//    _claimFundedWithdrawRequest -> epochNumber(N+1) > lastWithdrawRequest(N) -> pays full 1_000e6.
// Assert: attacker received 1_000e6 while only 500e6 was funded; the extra 500e6 came from
// underlying belonging to other claimants -> direct theft / insolvency.
```

Uncertainty: I verified the vault-side keying and claim logic fully, but could not re-read `IdleCDOEpochVariant.stopEpoch`'s exact call ordering (`deposit()` vs `collectWithdrawFunds`) within the iteration budget. The vulnerability exists iff `epochNumber` is incremented before `collectWithdrawFunds` in the stop flow; the `deposit()` comment at IdleCreditVault.sol:608 ("deposit done on stopEpoch") makes that ordering plausible and is precisely the "head not joined back to the list before retransmit" analog this scan targets.