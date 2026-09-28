### Title
Pending withdraw receipts from earlier epochs escape `stopEpochWithDuration` loss haircuts and claim at par, draining funded cash from other claimants - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.collectWithdrawFunds` records a single `lossRecoveryPriceByEpoch[epochNumber]` when the borrower under-funds `pendingWithdraws` at `stopEpoch`, but `pendingWithdraws` is a global aggregate that includes receipts requested in earlier epochs. Because the loss-adjusted claim path only clears receipts for `lastWithdrawRequest[_user]` (the user's latest request epoch), a user holding receipts from an earlier epoch has that earlier basis haircut at funding time yet is later paid at par through `_claimFundedWithdrawRequest`. This breaks the loss-waterfall invariant and pays out more than was funded, stealing from other pending claimants or the recovery reserve.

### Finding Description
The relevant flow in `contracts/strategies/idle/IdleCreditVault.sol`:

1. `requestWithdraw` records receipts per epoch: `withdrawsRequestsByEpoch[_user][currentEpoch] += _amount` and `pendingWithdraws += _amount`, and updates `lastWithdrawRequest[_user] = currentEpoch` (lines 279-293). The guard at lines 262-271 only blocks a new request when the *last* request epoch already has a stored `lossRecoveryPrice` — it does not prevent accumulating receipts across multiple epochs when previous stops were fully funded.

2. At `stopEpoch`, `IdleCDOEpochVariant._stopEpoch` calls `previewLossAdjustedWithdrawFunds(_lossAmount)`, which splits the loss pro rata over `activeBasis + pendingBasis` where `pendingBasis = pendingWithdraws` is the *aggregate* of all pending receipts regardless of request epoch (lines 440-460). `collectWithdrawFunds(_amount)` then either fully funds the aggregate or, when `_amount < pendingBasis`, stores `lossRecoveryPriceByEpoch[epochNumber]` and zeroes `pendingWithdraws` (lines 411-430) — implicitly haircutting every pending receipt, including ones from older epochs.

3. At claim time, `claimWithdrawRequest` calls `_claimLossAdjustedWithdrawRequest(_user)` (lines 789-801), which only looks at `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` and only clears `withdrawsRequestsByEpoch[_user][lossEpoch]` plus the same-epoch APR0 bucket via `_clearWithdrawClaimForEpoch` (lines 811-837). It then resets `lastWithdrawRequest[_user] = 0`.

4. Immediately after, `_claimFundedWithdrawRequest` pays the *remaining* aggregate `withdrawsRequests[_user]` — which still contains the earlier-epoch receipt — **at par** via `_transferFundedClaim` (lines 319-350). The epoch gate at line 326 passes because `lastWithdrawRequest` was just zeroed (`epochNumber <= 0` is false).

So an earlier-epoch receipt is included in the haircut basis at funding time (reducing `pendingToFund` for everyone) but paid out at 100% at claim time.

### Impact Explanation
Direct theft / insolvency. The strategy only received `pendingToFund = pendingBasis - pendingLoss` underlying at `stopEpoch`, but the claimant receives `claimBasis(lastEpoch) * lossRecoveryPrice + earlierEpochBasis * 1.0`. The excess over what was funded is paid out of strategy underlying that belongs to other pending claimants or, after default finalization, is only stopped by the `defaultRecoveryReserve` guard in `_transferFundedClaim` (lines 897-907) — meaning in the best case later claimants' transactions revert and their funded receipts are permanently unclaimable; in the worst case (before any reserve exists) earlier claimants in the same loss epoch or other users' funded balances are drained.

Concretely: user requests 100 in epoch A and 100 in epoch B. `stopEpoch` B applies a 50% pending loss, so only 100 underlying is funded. The user claims 50 via the loss-adjusted path (epoch B basis) plus 100 at par via the funded path (epoch A basis) = 150 paid from 100 funded — a 50-underlying deficit socialized onto the remaining claimants.

### Likelihood Explanation
Requires only unprivileged actions: two `requestWithdraw` calls in different buffer periods (a legitimate pattern explicitly supported by the code's "request another withdraw and wait another epoch" comment at lines 323-324), followed by a `stopEpochWithDuration`/`stopEpoch` where the borrower partially under-funds pending withdraws — i.e., any realized loss event while multi-epoch pending receipts exist. No privileged cooperation, no oracle manipulation, no timing edge cases. The only precondition is that the earlier stop fully funded (no `lossRecoveryPrice` stored), which is the common path, and that receipts remain unclaimed across an epoch boundary, which the protocol explicitly allows.

### Recommendation
Apply the haircut to the user's *entire* pending basis, not just the last-request epoch. Options:
- In `_claimLossAdjustedWithdrawRequest`, iterate/clear all `withdrawsRequestsByEpoch` entries (or track a per-user unclaimed-pending aggregate) and apply `lossRecoveryPrice` to the full remaining `withdrawsRequests[_user]` and open APR0 principal, since `pendingWithdraws` was haircut as an aggregate.
- Alternatively, in `requestWithdraw`, revert whenever the user has *any* unclaimed pending receipt (not only when the last epoch has a stored loss price), so a user can never hold receipts spanning a loss boundary.
- Store the loss against a claim-scope marker that covers all receipts created up to that stop (e.g., a global "loss epoch watermark" compared against each `withdrawsRequestsByEpoch` entry), rather than keying claims solely off `lastWithdrawRequest`.

### Proof of Concept
Foundry fork PoC sketch (based on the `test/foundry/IdleCreditVault.t.sol` harness):

```solidity
function testEarlierEpochReceiptEscapesHaircut() external {
    // epoch 0 runs normally
    _depositWithUser(user, 200_000 * ONE_SCALE, true);
    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, apr, _expectedFundsEndEpoch());   // epochNumber -> 1

    // buffer of epoch 1: user requests 100 (request epoch = 1)
    vm.prank(user);
    cdoEpoch.requestWithdraw(amount100, address(AAtranche));

    _startEpochAndCheckPrices(1);
    _stopEpochAndCheckPrices(1, apr, _expectedFundsEndEpoch());   // fully funded, no loss; epochNumber -> 2

    // buffer of epoch 2: user requests another 100 (request epoch = 2)
    vm.prank(user);
    cdoEpoch.requestWithdraw(amount100, address(AAtranche));

    // epoch 2 runs; borrower under-funds pending withdraws by 50%
    // manager calls stopEpochWithDuration with _lossAmount s.t. pendingLoss = 50% of 200
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(0, interest, duration, lossAmount); // lossRecoveryPriceByEpoch[2] = 0.5e18

    // only 100 underlying was collected for 200 of pending basis
    vm.prank(user);
    cdoEpoch.claimWithdrawRequest();

    // BUG: user receives 100*0.5 (epoch-2 receipt) + 100*1.0 (epoch-1 receipt) = 150
    // while the strategy only holds 100 funded underlying -> 50 taken from other
    // claimants' funded cash / causes subsequent claims to revert.
}
```

Key lines: the funding-side aggregate haircut at `contracts/strategies/idle/IdleCreditVault.sol:411-430`, the last-epoch-only claim at `IdleCreditVault.sol:789-801`, the stale-epoch clearing at `IdleCreditVault.sol:811-837`, and the par payout of the remainder at `IdleCreditVault.sol:319-350`.