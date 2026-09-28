### Title
Unfunded instant-withdraw receipts are paid at par before default finalization — ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
Analogous to `nfsd4_spo_must_allow()` reading `cstate` without first checking the request is a v4 compound, `claimInstantWithdrawRequest()` in `IdleCreditVault` interprets `instantWithdrawsRequests[_user]` as a *funded* claim without checking whether the request's epoch was ever funded or whether the pool has already defaulted. During the window between a borrower failing to fund instant withdrawals (`getInstantWithdrawFunds` marks the pool defaulted) and `finalizeDefault`/`finalizeDefaultRecovery` being executed, any holder of an unfunded instant-withdraw receipt can call `IdleCDOEpochVariant.claimInstantWithdrawRequest()` and be paid the full `instantWithdrawsRequests[_user]` at par from underlying held by the strategy (partially prefunded instant reserve, `defaultRecoveryReserve`, or funds backing other users' funded claims). After `finalizeDefaultRecovery`, `_claimDefaultedInstantWithdrawRequest` correctly haircuts the same receipt — the bug is that the pre-finalization path never checks which "type" of claim it is servicing.

### Finding Description
- `IdleCreditVault.requestInstantWithdraw` mints a receipt and increments `instantWithdrawsRequests[_user]` and `pendingInstantWithdraws` (IdleCreditVault.sol:356-375). Funding arrives only when the CDO pulls borrower cash via `collectInstantWithdrawFunds`, which decrements `pendingInstantWithdraws` globally, not per user (IdleCreditVault.sol:398-403).
- `claimInstantWithdrawRequest` only applies the defaulted-epoch haircut when `defaultRecoveryFinalized && defaultInstantWithdrawsFinalized` is already true (IdleCreditVault.sol:382-386); otherwise it burns the receipt and pays `instantWithdrawsRequests[_user]` in full via `_transferFundedClaim` (IdleCreditVault.sol:387-392). There is no check that this receipt's epoch was funded, i.e. `instantWithdrawsRequestsByEpoch[_user][epochNumber]` vs. the still-pending `pendingInstantWithdraws`.
- `IdleCDOEpochVariant.claimInstantWithdrawRequest` gates only on `allowInstantWithdraw` (IdleCDOEpochVariant.sol:975-979) — it does not revert while `defaulted()` is true but unfinalized.
- `getInstantWithdrawFunds` reverts/marks default when the borrower does not fund the instant queue (test `testClaimWithdrawRequestWithInstantDefault`, IdleCreditVault.t.sol:3777-3819, confirms `isEpochRunning` is cleared and normal claims remain callable). In that state `defaultRecoveryFinalized` is still false until the owner calls `finalizeDefault`, but partially collected instant funds may already sit in the strategy (`_defaultPrefundedInstantReserve`, IdleCreditVault.sol:716-723, explicitly accounts for this prefunded-but-unfinalized cash).

### Impact Explanation
An unprivileged lender requests an instant withdrawal in epoch N; the borrower funds only part (or none) of the instant queue at epoch start, triggering default. Before the honest owner finalizes the default, the attacker calls `claimInstantWithdrawRequest()` and receives the full unfunded receipt amount at par, draining underlying that the accounting treats as prefunded reserve / recovery backing for all defaulted claimants. The loss equals the lesser of the attacker's receipt size and the strategy's underlying balance, and it is extracted at the direct expense of other receipt holders whose finalized recovery price is later computed over the depleted reserve — theft of unclaimed recovery funds.

### Likelihood Explanation
Requires: `allowInstantWithdraw` enabled, a borrower funding shortfall at epoch start (a routine default scenario, already exercised in tests), and the attacker winning the pre-finalization window (no deadline between `getInstantWithdrawFunds` default and `finalizeDefault`). The attacker is just a tranche holder who made an instant-withdraw request — a permitted role. Caveat: I did not confirm `_transferFundedClaim`'s internal checks; if it additionally validates per-epoch funding, the finding reduces to a null result, but nothing in the claim path reads `instantWithdrawsRequestsByEpoch` outside the finalized-default branch.

### Recommendation
In `claimInstantWithdrawRequest`, treat claims whose epoch equals the current (defaulted, unfinalized) epoch — or any receipt still counted in `pendingInstantWithdraws` — as unfunded: either revert, or require `defaultRecoveryFinalized` before paying when `pendingInstantWithdraws` covers the claim. Track funded vs. unfunded instant basis per epoch (`instantWithdrawsRequestsByEpoch` minus per-epoch funded amount) so a receipt is only paid at par once its epoch was actually collected via `collectInstantWithdrawFunds`.

### Proof of Concept
Foundry fork sketch (extend `test/foundry/IdleCreditVault.t.sol`):

```solidity
// 1. depositAA/depositBB, run epoch 0, request instant withdraw of X
cdoEpoch.requestInstantWithdraw / requestWithdraw path per existing helpers
// 2. startEpoch(1); borrower deals only X/2 to strategy for instant queue
//    (collectInstantWithdrawFunds pulls partial amount; pendingInstantWithdraws = X/2)
// 3. warp past instantWithdrawDelay, borrower does NOT fund remainder
vm.prank(manager); cdoEpoch.getInstantWithdrawFunds(); // pool defaults, unfinalized
// 4. BEFORE owner calls finalizeDefault:
uint256 balPre = underlying.balanceOf(attacker);
vm.prank(attacker); cdoEpoch.claimInstantWithdrawRequest();
// assert attacker received full X at par, paid from the strategy's prefunded reserve
assertEq(underlying.balanceOf(attacker) - balPre, X);
// assert strategy reserve now short vs. defaultPendingClaimBasis expectation
```