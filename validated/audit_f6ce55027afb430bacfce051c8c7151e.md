### Title
Withdraw receipts haircutted by `stopEpochWithDuration` can be claimed at par when the user has pending receipts in a different request epoch than `lastWithdrawRequest` - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The kernel bug is a cursor (`seq->count`) advanced one slot past the bound the overflow check inspects, so the guard no longer detects the violation. The analog in `IdleCreditVault` is `lastWithdrawRequest`: a single per-user epoch cursor that is the *only* slot inspected by both the re-request guard in `requestWithdraw` and the haircut lookup in `_claimLossAdjustedWithdrawRequest`, while receipts (`withdrawsRequestsByEpoch`, `apr0Users`) and haircuts (`lossRecoveryPriceByEpoch`) are stored per epoch. A user holding receipts in multiple request epochs advances the cursor past the checked slot, defeating the loss-haircut check and being paid at par.

### Finding Description
In `requestWithdraw`, the loss guard reads only the epoch pointed to by `lastWithdrawRequest[_user]`: it reverts only when `lossRecoveryPriceByEpoch[lossEpoch] != 0` *and* a receipt exists in that epoch (lines 261-271). Similarly, `claimWithdrawRequest` routes through `_claimLossAdjustedWithdrawRequest`, which again reads only `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` and clears only that epoch's receipt via `_clearWithdrawClaimForEpoch` (lines 789-800, 811-837). Whatever remains in the aggregate `withdrawsRequests[_user]` then falls into `_claimFundedWithdrawRequest`, which pays the entire remaining aggregate **at par** (lines 338-349) once `epochNumber > lastWithdrawRequest`.

Attack sequence (unprivileged lender, buffer phase of each epoch):

1. Epoch N running: attacker requests withdraw. `lastWithdrawRequest = N`, `withdrawsRequestsByEpoch[N] += A1`, `pendingWithdraws += A1`.
2. Epoch N stops cleanly; epoch N+1 starts and stops; buffer: attacker requests again. Guard reads `lossRecoveryPriceByEpoch[N] == 0`, allowed. `lastWithdrawRequest = N+1`, `withdrawsRequestsByEpoch[N+1] += A2`.
3. Epoch N+2 runs; borrower repays with a shortfall, manager calls `stopEpochWithDuration(_lossAmount)`. `pendingWithdraws` (A1+A2) is haircutted pro-rata (`previewLossAdjustedWithdrawFunds`, lines 440-460) and the recovery price is recorded per epoch. Crucially, the epoch marker the attacker left — `lastWithdrawRequest = N+1` — differs from the epoch under which the A1 receipt's haircut is recoverable, and only one epoch slot is ever read.
4. Claim path: `_claimLossAdjustedWithdrawRequest` resolves only the receipt in `lastWithdrawRequest`'s epoch; `_clearWithdrawClaimForEpoch` zeroes `lastWithdrawRequest` and removes only that epoch's slice from `withdrawsRequests`. The remaining `withdrawsRequests[_user]` (the A1 receipt) then flows through `_claimFundedWithdrawRequest`, whose only gating is `epochNumber > lastWithdrawRequest` — now satisfied — and pays it at par although it was part of the loss-adjusted `pendingBasis`.

Like the seqcount bug, the epoch cursor has been moved past the slot the check inspects, so the haircut invariant (loss receipts pay `claimBasis * price / 1e18`, never par) is bypassed.

### Impact Explanation
Each haircutted receipt paid at par overpays by `basis * (1 - lossRecoveryPrice)`. The loss-adjusted funding deposited by the borrower is only `pendingBasis - pendingLoss`, so par payment either drains underlying belonging to other claimants/lenders or makes later claims revert — direct theft / insolvency proportional to the haircutted basis and loss ratio.

### Likelihood Explanation
Requires a lender to hold withdraw receipts spanning two request epochs before a `stopEpochWithDuration` loss — a routine, honest-looking sequence (request twice across consecutive buffers). No privileged misbehavior needed. A caveat: the exploit depends on the exact epoch keying used when `lossRecoveryPriceByEpoch` is written at the loss-event (I verified the read paths; the write site should be confirmed to key by request epoch rather than uniformly covering all pending epochs — if a single price covers all pending epochs but only `lastWithdrawRequest`'s slot is read, the same mismatch applies in reverse).

### Recommendation
Replace the single `lastWithdrawRequest` cursor with per-epoch resolution: iterate or track all epochs with nonzero `withdrawsRequestsByEpoch[_user][*]`/`apr0Users` having `lossRecoveryPriceByEpoch != 0`, or maintain a bitmap/queue of request epochs per user. The re-request guard and the loss-adjusted claim must inspect *every* epoch that holds a receipt, not just the latest marker — the moral equivalent of checking `seq_has_overflowed()` after each write rather than trusting the cursor.

### Proof of Concept
Foundry fork sketch against `IdleCreditVault`/`IdleCDOEpochVariant`:

```solidity
// user = attacker EOA; victim = another lender with a pending receipt
// 1) epoch N buffer: attacker requestWithdraw(t1, AAtranche)
//    -> lastWithdrawRequest == N
// 2) stopEpoch(N), startEpoch(N+1), stopEpoch(N+1)  (clean, full funding)
// 3) epoch N+1 buffer: attacker requestWithdraw(t2, AAtranche)
//    -> guard passes (lossRecoveryPriceByEpoch[N] == 0); lastWithdrawRequest == N+1
// 4) startEpoch(N+2); borrower under-funds; manager stopEpochWithDuration(lossAmount)
//    -> both receipts haircutted; lossRecoveryPriceByEpoch recorded
// 5) attacker claimWithdrawRequest()
//    -> _claimLossAdjustedWithdrawRequest clears only the lastWithdrawRequest epoch slice
//    -> _claimFundedWithdrawRequest pays residual withdrawsRequests at par
// assert(attackerReceived > t1*t2_basis * lossRecoveryPrice / 1e18)
// assert(strategy underlying balance < sum of remaining haircuts owed) // or victim claim reverts
```

Because the write-site epoch keying of `lossRecoveryPriceByEpoch` was not fully verified in this pass, the PoC should first log which epoch slot carries the nonzero price after step 4 and orient `lastWithdrawRequest` to a different slot; the core defect (only one epoch slot is ever consulted for both the guard and the payout ratio) holds regardless.