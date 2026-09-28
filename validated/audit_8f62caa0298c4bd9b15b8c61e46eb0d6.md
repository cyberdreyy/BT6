### Title
Loss-adjusted withdraw receipt can be re-requested into a par claim because `lastWithdrawRequest` is overwritten - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault` tracks a user's loss-adjusted withdraw receipt through the single mutable pointer `lastWithdrawRequest[_user]`, which stores the *latest* request epoch. When a borrower underfunds `collectWithdrawFunds`, the haircut is stored in `lossRecoveryPriceByEpoch[epochNumber]`. A user who requests a withdrawal, suffers a partial-loss epoch (`lossRecoveryPriceByEpoch[E] < RECOVERY_FULL`), and then files a *new* withdraw request before claiming gets `lastWithdrawRequest[_user]` overwritten to `E+1`. On claim, `_claimLossAdjustedWithdrawRequest` looks up `lossRecoveryPriceByEpoch[E+1]` (zero), skips the haircut path, and `_claimFundedWithdrawRequest` pays the aggregate `withdrawsRequests[_user]` — including the E-epoch receipt — at par. This is the credit-vault analog of the ipset bug: the module reference (the epoch pointer holding the haircut) is dropped/overwritten while the resource (the unpaid loss-adjusted receipt) is still in use.

### Finding Description
- `collectWithdrawFunds(_amount)` clears `pendingWithdraws` and stores `lossRecoveryPriceByEpoch[epochNumber] = _amount * RECOVERY_FULL / pendingBasis` when the borrower partially funds pending receipts. The vault holds only the haircutted amount for those receipts.
- `_claimLossAdjustedWithdrawRequest` (lines 789-801) derives the loss epoch solely from `lastWithdrawRequest[_user]`; if `lossRecoveryPriceByEpoch[lastWithdrawRequest]` is 0 it returns 0 without touching the receipt.
- `_claimFundedWithdrawRequest` (lines 319-350) pays the aggregate `withdrawsRequests[_user]` at par once `epochNumber > lastWithdrawRequest[_user]`, burning receipt tokens and transferring underlying via `_transferFundedClaim`.
- `withdrawsRequestsByEpoch[_user][E]` is only reduced inside `_clearWithdrawClaimForEpoch`, which is reached only through the loss-adjusted or defaulted claim paths — never by a new request. So after a new request bumps `lastWithdrawRequest` to `E+1`, the old basis survives in the aggregate and is paid in full, while the vault was funded only `basis * lossRecoveryPrice` for it.

Attack sequence (running → stopped-with-loss → buffer → running):
1. Attacker (KYC'd tranche holder) requests a withdraw in epoch `E`; `lastWithdrawRequest = E`, basis `B`.
2. Honest manager calls `stopEpochWithDuration`/`collectWithdrawFunds` with `_amount < B`-equivalent partial funding; `lossRecoveryPriceByEpoch[E] = p < 1`.
3. Instead of claiming, the attacker requests another withdraw in epoch `E+1`; `lastWithdrawRequest = E+1`.
4. After the next stop (`epochNumber = E+2 > E+1`), `claimWithdrawRequest` skips `_claimLossAdjustedWithdrawRequest` (`lossRecoveryPriceByEpoch[E+1] == 0`) and pays `B` at par plus the new request through `_claimFundedClaim`, which enforces only the `defaultRecoveryReserve` guard — there is no check that epoch `E`'s haircut was applied.

### Impact Explanation
The vault received `B * p` for the E-epoch receipt but pays out `B`. The difference `B * (1 - p)` is pulled from other holders' funded claims or from strategy balances, breaking solvency/fair-payout: direct theft of up to the full un-recovered loss amount per re-requested receipt, repeatable across epochs and users.

### Likelihood Explanation
Requires an unprivileged tranche holder, a partial-loss `collectWithdrawFunds` event (plausible: borrower underfunding is a supported code path with `previewLossAdjustedWithdrawFunds`), and the user simply requesting again instead of claiming — no privileged cooperation needed. Caveat: I could not fully confirm that `requestWithdraw` does not itself settle or block on an outstanding loss-adjusted receipt; if it forces settlement of the old receipt first, this path closes. That check in `requestWithdraw`/`_requestWithdrawApr0` (which does call `_settleApr0`, but `_settleApr0` only moves APR0 buckets and does not clear `withdrawsRequestsByEpoch`) should be verified in a PoC.

### Recommendation
Track the loss-epoch pointer per receipt rather than via a single `lastWithdrawRequest`, or iterate `withdrawsRequestsByEpoch`/`lossRecoveryPriceByEpoch` across all epochs with non-zero basis in `claimWithdrawRequest`. Alternatively, in `requestWithdraw`, force-clear any prior receipt whose `lossRecoveryPriceByEpoch[requestEpoch] != 0` (or revert) before overwriting `lastWithdrawRequest`.

### Proof of Concept
Foundry fork PoC sketch (in `test/foundry/IdleCreditVault.t.sol` style):
```solidity
// 1. Deposit with attacker, start epoch, request withdraw of tranches (basis B).
// 2. Borrower underfunds: vm.prank(cdo) path -> collectWithdrawFunds(B * 60 / 100);
//    assert lossRecoveryPriceByEpoch[E] == 0.6e18.
// 3. Attacker requests withdraw again (epoch E+1); assert lastWithdrawRequest == E+1.
// 4. Advance to epoch E+2 via honest stopEpoch; borrower funds new pending at par.
// 5. vm.prank(idleCDO) claimWithdrawRequest(attacker);
//    assert payout == B + newBasis at par (expected: B*0.6 + newBasis);
//    assert strategy underlying balance deficit == B * 0.4.
```