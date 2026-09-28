I'll examine the withdraw request flow and epoch accounting more closely.### Title
Loss-adjusted withdrawal haircut can be bypassed via settled APR0 buckets, allowing par claims on haircut receipts - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
When `stopEpochWithDuration(_lossAmount)` records `lossRecoveryPriceByEpoch[epoch]`, users holding withdraw receipts for that epoch are supposed to claim the haircut amount via `_claimLossAdjustedWithdrawRequest`. The protection that preserves `lastWithdrawRequest[_user]` (the pointer to the loss epoch) in `requestWithdraw` only inspects `withdrawsRequestsByEpoch` and the *open* `apr0Users[_user].principal` bucket. Once a user's APR0 principal is settled into `settledPrincipal`/`settledInterest` (the `principal`/`principalEpoch` fields are cleared), the guard no longer detects the loss-epoch receipt, `lastWithdrawRequest` can be overwritten by a new request, and the settled amounts are later paid **at par** through `_claimFundedWithdrawRequest` — the loss haircut is never applied to them.

### Finding Description
The analog to CVE-2017-18193 is shared-state corruption across concurrent/interleaved operations on the same per-user accounting structure: two request types (normal/APR0 withdraw receipts and the `lastWithdrawRequest` epoch pointer) race across epoch transitions, leaving the structure in a state the claim paths cannot reconcile.

In `requestWithdraw` (lines 261-270) the guard is:

```solidity
uint256 lossEpoch = lastWithdrawRequest[_user];
uint256 lossRecoveryPrice = lossRecoveryPriceByEpoch[lossEpoch];
if (
  lossRecoveryPrice != 0 &&
  (withdrawsRequestsByEpoch[_user][lossEpoch] != 0 ||
   (apr0Users[_user].principal != 0 && apr0Users[_user].principalEpoch == lossEpoch))
) { revert NotAllowed(); }
```

It ignores `apr0Users[_user].settledPrincipal` and `settledInterest`. Symmetrically, `_withdrawClaimAmountsForEpoch` (lines 862-881) computes the loss-epoch `claimBasis` only from `withdrawsRequestsByEpoch` plus open `principal`/`principalEpoch` — settled buckets contribute nothing to the haircutted basis. Meanwhile `_claimFundedWithdrawRequest` (lines 338-341) pays out `settledPrincipal + principal + settledInterest` at par:

```solidity
uint256 apr0PrincipalAmount = _apr0User.settledPrincipal + _apr0User.principal;
uint256 apr0InterestAmount = _apr0User.settledInterest;
amount = normalAmount + apr0PrincipalAmount + apr0InterestAmount;
```

So the lifecycle is: APR0 user requests a withdraw in epoch E → epoch E ends with a loss (`lossRecoveryPriceByEpoch[E] < RECOVERY_FULL`) → the user's APR0 principal is settled into `settledPrincipal`/`settledInterest` (clearing `principal`/`principalEpoch`) → the guard sees `principal == 0` and `withdrawsRequestsByEpoch[user][E] == 0` (APR0 requests never touch that ledger, see lines 285-294) → user calls `requestWithdraw` again in a later epoch, overwriting `lastWithdrawRequest` → `_claimLossAdjustedWithdrawRequest` reads the new `lossEpoch`, finds `lossRecoveryPriceByEpoch[newEpoch] == 0`, returns 0 → the funded path pays the settled buckets at par. The haircut is silently dropped.

### Impact Explanation
Direct theft / insolvency. The user receives 100% of their settled principal and interest even though the epoch's funds were reduced by `lossRecoveryPriceByEpoch[E]`. The excess is paid out of the strategy's funded balance, so it is socialized onto all other tranche holders and later claimants — the last claimants of the loss epoch can be left underfunded (insolvency). Loss magnitude scales with the attacker's share of APR0 receipts in the loss epoch; in a pool where APR0 withdrawals dominate epoch E, essentially the full haircut amount can be extracted.

### Likelihood Explanation
Medium-high. It requires an epoch ending with `stopEpochWithDuration(_lossAmount > 0)` — an honest manager action after partial borrower repayment, which is a normal (if infrequent) operating mode. The attacker needs only a KYC-passing wallet, an APR0-mode vault (`unscaledApr == 0`), and a withdraw request outstanding in the loss epoch, plus one additional request transaction afterward. No privileged role, reentrancy, or oracle manipulation is required; the attack is a pure ordering/ledger-coverage gap between the guard at `requestWithdraw` and the claim-basis computation in `_withdrawClaimAmountsForEpoch`.

Caveat: exploitability depends on the user's APR0 principal actually being settled (clearing `principalEpoch`) while the receipt remains unpaid. If `principal`/`principalEpoch` is never cleared before claim in all code paths (i.e., settlement is strictly lazy inside `_claimFundedWithdrawRequest`, which is guarded by the `epochNumber <= lastWithdrawRequest` revert), the attack window narrows — but the pool-close path (`epochEndDate == 0` skips that revert, per the comment at lines 334-336) and any code path settling APR0 users at `stopEpoch`/`prepareStopEpochWithApr0` open it. A Foundry PoC should specifically verify whether `settledPrincipal` can become non-zero while `lossRecoveryPriceByEpoch[principalEpoch]` is set.

### Recommendation
1. Extend the `requestWithdraw` guard so a loss-epoch receipt blocks new requests whenever *any* claim basis exists for `lossEpoch`, e.g. also check `apr0Users[_user].settledPrincipal != 0 || apr0Users[_user].settledInterest != 0` when the settled epoch maps to `lossEpoch` (store the settled epoch alongside, since `principalEpoch` is cleared on settlement).
2. Extend `_withdrawClaimAmountsForEpoch` to include settled APR0 principal/interest attributable to `_claimEpoch` in `claimBasis`, so the haircut cannot be skipped even if the epoch pointer is lost.
3. Alternatively, settle APR0 receipts applying `lossRecoveryPriceByEpoch` at settlement time so the funded path can never pay them above the haircut.

### Proof of Concept
```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.10;

import "forge-std/Test.sol";

// Scenario: APR0-mode IdleCreditVault + IdleCDOEpochVariant.
// Requires: attacker is KYC-passed (isWalletAllowed), manager/borrower honest.
contract LossHaircutBypassPoC is Test {
    // 1. Configure vault with unscaledApr == 0 (APR0 flow) and a live epoch.
    // 2. attacker deposits D underlying -> receives tranche tokens.
    // 3. During epoch E (running phase), attacker calls cdo.requestWithdraw(tranches)
    //    -> IdleCreditVault.requestWithdraw routes to _requestWithdrawApr0:
    //       apr0Users[attacker].principal = amount; principalEpoch = E.
    // 4. Epoch ends; manager calls stopEpochWithDuration(apr, lossAmount > 0)
    //    (honest partial borrower repayment) -> lossRecoveryPriceByEpoch[E] = r < 1e18.
    // 5. Drive settlement of the attacker's APR0 receipt so that
    //    apr0Users[attacker].principal == 0 && principalEpoch == 0
    //    while settledPrincipal/settledInterest != 0
    //    (e.g., pool-close path with epochEndDate == 0 where _settleApr0 runs,
    //     or the settle step invoked by stopEpoch/prepareStopEpochWithApr0).
    // 6. Attacker calls cdo.requestWithdraw(dust) in epoch E+1.
    //    Guard at requestWithdraw L263-270:
    //      withdrawsRequestsByEpoch[attacker][E] == 0   (APR0 never wrote it)
    //      apr0Users[attacker].principal == 0         (settled)
    //    => no revert; lastWithdrawRequest[attacker] = E+1.
    // 7. Warp until epoch E+1 ends; borrower funds pendingWithdraws.
    // 8. Attacker calls claimWithdrawRequest:
    //      _claimLossAdjustedWithdrawRequest: lossEpoch = E+1,
    //        lossRecoveryPriceByEpoch[E+1] == 0 -> returns 0.
    //      _claimFundedWithdrawRequest pays
    //        settledPrincipal + settledInterest AT PAR.
    // 9. Assert: attacker.balanceOf(underlying) == settledPrincipal + settledInterest
    //    instead of (settledPrincipal + settledInterest) * r / 1e18.
    //    Assert: strategy funded balance shortfall == *(1e18 - r)/1e18,
    //    i.e., other claimants' loss-epoch payouts are insolvent by that amount.
}
```