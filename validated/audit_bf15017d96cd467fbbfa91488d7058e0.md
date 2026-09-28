### Title
Loss haircut from `stopEpochWithDuration` is only applied to the user's latest request epoch — older unclaimed receipts still pay out at par, draining funded underlyings - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
`collectWithdrawFunds` applies a single aggregate `lossRecoveryPrice` to **all** `pendingWithdraws`, so the borrower funds only `pendingBasis * price`. However, `_claimLossAdjustedWithdrawRequest` only clears and haircuts the receipt recorded for `lastWithdrawRequest[_user]`. Any older per-epoch receipts the same user holds (`withdrawsRequestsByEpoch[_user][olderEpoch]`) then flow into `_claimFundedWithdrawRequest`, which pays them **at par** — even though the funding for them was already haircut. The claimant extracts more underlying than was funded, directly stealing from other pending receipt holders / the recovery reserve.

### Finding Description
Bug-class analog: a crafted *structured index* (here: the epoch key) produces an out-of-bounds read of accounting state — the claim path reads the loss price only at `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` (IdleCreditVault.sol:790-792) and misses that the loss was applied to the aggregate pending bucket.

Sequence:

1. Epoch 1 (running, fixed-APR mode): attacker calls `requestWithdraw(A, attacker, A)` via the CDO. `withdrawsRequestsByEpoch[attacker][1] = A`, `withdrawsRequests[attacker] = A`, `lastWithdrawRequest[attacker] = 1`, `pendingWithdraws = A` (IdleCreditVault.sol:282-293).
2. `stopEpoch` runs; `epochNumber` becomes 2. Attacker does not claim.
3. Epoch 2: attacker calls `requestWithdraw(B, attacker, B)`. The guard at lines 263-271 passes because `lossRecoveryPriceByEpoch[1] == 0`. Now `withdrawsRequestsByEpoch[attacker][2] = B`, `lastWithdrawRequest[attacker] = 2`, `pendingWithdraws = A + B`.
4. `stopEpochWithDuration(_lossAmount)` realizes a loss. `previewLossAdjustedWithdrawFunds` splits the loss between the active basis and the **aggregate** `pendingBasis = A + B`, and `collectWithdrawFunds` pulls only `(A+B) * lossRecoveryPrice` underlying, storing `lossRecoveryPriceByEpoch[2] = price` (IdleCreditVault.sol:417-421, 440-459).
5. Attacker calls `claimWithdrawRequest`:
   - `_claimLossAdjustedWithdrawRequest` computes `claimBasis = B` only for epoch 2 (`_withdrawClaimAmountsForEpoch` reads `withdrawsRequestsByEpoch[user][2]`, lines 862-880), burns `B`, pays `B * price`, and because `lastWithdrawRequest == 2 == claimEpoch`, resets `lastWithdrawRequest` to 0 (lines 832-836).
   - `_claimFundedWithdrawRequest` then sees `epochEndDate != 0 && epochNumber (3) > lastWithdrawRequest (0)`, pays `withdrawsRequests[attacker]` — still `A` — **at par** (lines 319-349).

Total paid: `B * price + A`. Total funded for both receipts: `(A + B) * price`. The attacker over-claims by `A * (1 - price)` — underlyings that belong to other pending receipt claimants or the strategy.

### Impact Explanation
Direct theft / insolvency. The funded pool after a loss-adjusted stop is `pendingBasis * price`; paying a subset of receipts at par means the strategy's underlying is exhausted before all claims are honored — later claimants' funded receipts revert on transfer (permanent loss of their haircut-adjusted payout), or the difference is taken from underlyings reserved elsewhere. With `A = B` and `price = 0.5e18`, the attacker extracts 25% more than their fair share in a single claim. No privileged misbehavior is required — only a lender with receipts spanning two epochs and an ordinary (honest) `stopEpochWithDuration` loss event.

### Likelihood Explanation
The precondition — a user holding unclaimed receipts across two epochs — is explicitly supported by the code comment "if a user does not claim a withdraw request and instead requests another withdraw, he will have to wait for another epoch to claim both requests" (lines 323-324), and the only guard (lines 263-271) blocks re-requesting solely when a loss price already exists for the *last* epoch, not for older ones. `stopEpochWithDuration` with a loss is a normal borrower-shortfall path. Likelihood is moderate: it requires a realized loss epoch while multi-epoch receipts exist, but the attacker can always keep a small stale receipt open to be positioned whenever a loss occurs.

### Recommendation
Apply the loss epoch's recovery price to **all** pending receipts of the claimant, not just the `lastWithdrawRequest` epoch. Concretely: when `lossRecoveryPriceByEpoch[epoch]` is stored, either (a) iterate/clear all `withdrawsRequestsByEpoch[_user][*]` entries at that price in `_claimLossAdjustedWithdrawRequest`, or (b) revert in `requestWithdraw` whenever the user has any unfunded receipt (`_hasWithdrawRequest`), forcing a single live receipt per user, and record per-epoch loss prices only against epochs that can actually contain receipts. Alternatively, store the haircut globally (e.g., a single `pendingLossPrice` applied to `withdrawsRequests[_user]` in full) since `collectWithdrawFunds` already treats the pending bucket as aggregate.

### Proof of Concept
Foundry fork PoC sketch (against an `IdleCDOEpochVariant` + `IdleCreditVault` deployment):

```solidity
function test_staleEpochReceiptClaimsAtPar() public {
    // epoch 1: attacker deposits via CDO, requests withdraw of A
    vm.prank(attacker);
    cdo.withdrawAA(A); // -> IdleCreditVault.requestWithdraw(A, attacker, A)
    // stop epoch 1 normally; epochNumber -> 2
    manager.stopEpoch(); // borrower repays interest, receipts funded normally
    // epoch 2: attacker requests withdraw of B (allowed: no loss price on epoch 1)
    vm.prank(attacker);
    cdo.withdrawAA(B);
    // epoch 2 ends with a partial borrower loss
    manager.stopEpochWithDuration(loss); // collectWithdrawFunds funds < pendingWithdraws
    uint256 balBefore = token.balanceOf(attacker);
    vm.prank(attacker);
    cdo.claimWithdrawRequest();
    uint256 got = token.balanceOf(attacker) - balBefore;
    // fair share is (A + B) * lossRecoveryPrice; attacker receives B*price + A
    assertGt(got, (A + B) * vault.lossRecoveryPriceByEpoch(2) / 1e18);
}
```

Assert afterwards that a second honest user's `claimWithdrawRequest` reverts on `safeTransfer` (insufficient strategy balance), proving fund-level impact rather than a pure accounting artifact.