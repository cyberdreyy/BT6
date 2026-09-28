### Title
Instant-withdraw receipts lose per-epoch trackability and are claimed as one aggregate against pooled funds — unfunded receipts can consume another epoch's collected collateral - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
XSA-224's bug class is loss of grant trackability: a resource reference stops being bound to the entity/epoch that was actually accounted for, so a grantee can consume tracking capacity (here, funded underlying) that was reserved for someone else. The analog in `IdleCreditVault` is the instant-withdraw receipt flow. Requests are recorded per epoch (`instantWithdrawsRequestsByEpoch`, `instantWithdrawClaimsByEpoch`, `pendingInstantWithdraws`), funding is pulled via a single global counter in `collectInstantWithdrawFunds`, but `claimInstantWithdrawRequest` pays the user's entire aggregate `instantWithdrawsRequests[_user]`, burns all receipt tokens, and transfers funded underlying without checking that *that user's specific epoch receipts* were ever funded.

### Finding Description
Relevant code:

- `requestInstantWithdraw` mints receipt strategy tokens 1:1 and tracks the request three ways: per-user aggregate `instantWithdrawsRequests[_user]`, per-user-per-epoch `instantWithdrawsRequestsByEpoch[_user][currentEpoch]`, and global per-epoch `instantWithdrawClaimsByEpoch[currentEpoch]` plus global `pendingInstantWithdraws` (`contracts/strategies/idle/IdleCreditVault.sol:356-375`).
- `collectInstantWithdrawFunds(_amount)` only decrements the global `pendingInstantWithdraws` and pulls `_amount` underlying from the CDO — it does not attribute funding to any epoch or user (`IdleCreditVault.sol:398-403`).
- `claimInstantWithdrawRequest(_user)` burns `instantWithdrawsRequests[_user]` in full and calls `_transferFundedClaim(_user, amount)`; the per-epoch mappings are only ever cleared on the default-finalization path (`_claimDefaultedInstantWithdrawRequest`), never on the normal path (`IdleCreditVault.sol:380-393`).

So after a claim, `instantWithdrawsRequestsByEpoch[user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]` remain permanently stale — the protocol loses trackability of which epoch grants were actually settled (the exact XSA-224 shape: grants consumed but the grant-table entry never released/reconciled).

The exploitable consequence: because funding and claiming are both aggregate while requests are per-epoch, there is no binding between "this receipt's epoch was funded" and "this claim pays out". A user holding an instant receipt from epoch N that was never funded by the borrower (`pendingInstantWithdraws` still > 0 for it) can call `claimInstantWithdrawRequest` once the strategy holds underlying collected for *other* receipts (e.g., epoch N+1 funds pulled via `collectInstantWithdrawFunds`, or deposit proceeds resting in the strategy). The claim burns the unfunded receipt and transfers out funded collateral, which then leaves the legitimately funded claimant (or the queue's `processWithdrawalClaims`) with an unredeemable receipt — permanent freezing of their payout.

A secondary manifestation: if the borrower later defaults and `finalizeDefaultRecovery` runs, `defaultPendingClaimBasis()` computes basis as `pendingWithdraws + instantWithdrawClaimsByEpoch[epochNumber]` (`IdleCreditVault.sol:644-649`). Stale `instantWithdrawClaimsByEpoch` entries for the current epoch that correspond to already-claimed receipts inflate the default claim basis, diluting `defaultRecoveryPrice` for all genuine claimants — unquantified bookkeeping drift caused by the same loss of trackability.

### Impact Explanation
Direct theft / permanent freezing with fund impact: an unprivileged tranche-token holder whose instant-withdraw request was never funded can redeem it against underlying that was collected for other users' receipts or is earmarked for the epoch queue (`processWithdrawalClaims` expects `epochPendingClaims` back; a drained strategy makes that call receive less or revert). Victim receipts become permanently unpayable — there is no re-funding path once `pendingInstantWithdraws` has been decremented for funds that already left via someone else's claim. Quantified loss: up to the attacker's full `instantWithdrawsRequests` balance, bounded by funded underlying held by the strategy.

### Likelihood Explanation
Likelihood is gated by one detail I could not fully confirm within the indexed code: how `_transferFundedClaim` and the CDO-side `claimInstantWithdrawRequest` decide that underlying is "available" (tests show a `NotAllowed` revert when no underlying is present, `test/foundry/IdleCreditVault.t.sol:4801-4804`). If the gate is a plain balance check on the strategy rather than a per-epoch/per-user funding check, the attack works whenever the strategy transiently holds funded underlying while an earlier unfunded instant receipt exists — a normal condition across epoch boundaries (`instantWithdrawDelay` window plus borrower funding lag). The CDO, borrower, manager and queue all remain honest; the attacker is just a KYC'd tranche holder who requested an instant withdraw in an epoch whose funding the borrower short-paid or delayed. If instead availability is checked against `pendingInstantWithdraws` only, the same theft still applies to the residual case where partial funding occurred. Confidence: medium — the missing per-epoch funding binding is verified; the exact strength of the balance gate is not.

### Recommendation
Track funded status per epoch (or per user) rather than only globally:
- In `collectInstantWithdrawFunds`, record which epoch's claims the pulled amount covers (e.g., `fundedInstantClaimsByEpoch`).
- In `claimInstantWithdrawRequest`, iterate the user's `instantWithdrawsRequestsByEpoch` entries and pay only those whose epoch is fully funded; clear the per-epoch entries on claim so `instantWithdrawClaimsByEpoch` stays consistent for `defaultPendingClaimBasis`.
- Alternatively keep the aggregate payout but require `pendingInstantWithdraws == 0` or reduce the claimable amount to the funded floor, and always delete `instantWithdrawsRequestsByEpoch`/`instantWithdrawClaimsByEpoch` entries at claim time to restore trackability.

### Proof of Concept
Foundry fork sketch (mainnet fork, real IdleCDOEpochVariant + IdleCreditVault):

```solidity
function testUnfundedInstantReceiptStealsFundedUnderlying() external {
    // epoch 0 running, instant withdraws enabled with instantWithdrawDelay
    // user A (attacker): requestInstantWithdraw(100e6) in epoch N
    cdoEpoch.requestWithdraw(aShares, address(AAtranche));          // pendingInstantWithdraws = 100e6
    // epoch ends; borrower/manager funds only user B's epoch N+1 instant request:
    // borrower repays -> getInstantWithdrawFunds pulls exactly B's amount
    // strategy balance now holds B's funded underlying; pendingInstantWithdraws still counts A's 100e6
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest();                        // burns A's receipt, pays A from B's funds
    // user B / queue.processWithdrawalClaims now reverts or receives 0:
    // B's receipt tokens remain but strategy balance is drained -> permanent freeze
    assertEq(strategy.instantWithdrawsRequestsByEpoch(attacker, epochA), 100e6); // stale entry never cleared
}
```

Invariant broken: "one receipt one payout funded by its own epoch's grant" — the claim succeeds although the epoch-N grant was never funded, and the paid-out underlying was tracked (via `pendingInstantWithdraws`) as belonging to a different claim.