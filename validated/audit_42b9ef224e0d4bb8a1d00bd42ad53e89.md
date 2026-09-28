### Title
Unfunded instant-withdraw receipts can drain the funded claim pool, leaving legitimately funded claimants with only the default-recovery haircut - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault` keeps one shared underlying balance for all instant-withdraw receipts. `collectInstantWithdrawFunds` pulls cash from the CDO per funding event and only decrements the aggregate `pendingInstantWithdraws`, while `claimInstantWithdrawRequest` pays `instantWithdrawsRequests[_user]` at par from whatever balance the strategy holds, without checking that this user's receipt was actually funded. A user whose request is still unfunded can therefore claim first and spend underlying that was collected for another user's receipt — the same "early redeemer takes full amount, last user absorbs the loss" pattern as the CoreRouter liquidation bug.

### Finding Description
In `requestInstantWithdraw`, the CDO burns `_amount` strategy tokens and mints a 1:1 receipt to the user, bumping `instantWithdrawsRequests[_user]` and the global `pendingInstantWithdraws` (contracts/strategies/idle/IdleCreditVault.sol:356-375). Funding is delivered later via `collectInstantWithdrawFunds`, which only does `pendingInstantWithdraws -= _amount` and `safeTransferFrom(idleCDO, ...)` — there is no per-user or per-receipt funding marker (contracts/strategies/idle/IdleCreditVault.sol:398-403). When funds arrive, the CDO moves its own available cash to the strategy, but that cash may cover only part of the instant queue, exactly the scenario documented at `defaultPendingClaimBasis` (lines 636-649).

`claimInstantWithdrawRequest` then burns the caller's full receipt and calls `_transferFundedClaim(_user, amount)` unconditionally (lines 380-393). `_transferFundedClaim` only guards the `defaultRecoveryReserve`; it does not verify the receipt was funded (lines 897-907). So any holder of an unfunded instant receipt can claim at par while the pool still contains underlying collected for someone else.

Concrete sequence (running epoch, prefunded/instant mode):
1. Lender A requests instant withdraw of 100k; borrower/CDO funds it via `collectInstantWithdrawFunds(100k)` → strategy holds 100k, `pendingInstantWithdraws` back to 0.
2. Attacker B (any KYC'd lender / tranche holder) requests instant withdraw of 100k. `pendingInstantWithdraws = 100k`, strategy balance still 100k (A hasn't claimed yet).
3. B calls the CDO's claim path → `claimInstantWithdrawRequest(B)` pays B 100k at par, consuming A's funding.
4. A's claim now reverts on `safeTransfer` (or, if the borrower defaults before refunding, A's receipt is folded into `defaultPendingClaimBasis` and paid at `defaultRecoveryPrice` instead of par — see `_claimDefaultedInstantWithdrawRequest`, lines 842-856, and the prefunded-reserve accounting at lines 683-688).

A's receipt was fully funded, yet A ends up either temporarily unable to claim or permanently haircutted to the recovery ratio, while B — whose receipt was never funded — exits at par.

### Impact Explanation
Direct theft / loss realization on an honest user: a funded withdraw receipt is converted into an unfunded defaulted-epoch claim. If default recovery is, e.g., 40%, A loses 60% of a receipt that had already been paid for by the borrower, and B gains 100k it was never entitled to. Even absent default, A's withdrawal is frozen until new funding arrives, which is temporary freezing of user funds.

### Likelihood Explanation
Requires only: (a) an instant-withdraw funding event, (b) a second instant request landing before the funded user claims, and (c) the second user claiming first. All actors are unprivileged tranche holders; the CDO, manager and borrower behave honestly. Partial funding of the instant queue is an explicitly supported state (the code documents that `startEpoch` cash may cover "only part of the instant queue"). No privileged misbehavior, oracle manipulation, or default is required for the theft; default only worsens A's outcome from freezing to permanent loss.

### Recommendation
Track funded vs unfunded instant receipt basis per user/epoch (e.g., a `fundedInstantBasis` counter increased in `collectInstantWithdrawFunds` and per-epoch funded ownership), and have `claimInstantWithdrawRequest` pay only against the funded portion — mirroring how `lossRecoveryPriceByEpoch` haircuts underfunded normal receipts in `collectWithdrawFunds` (lines 411-430). Alternatively, gate claims on `instantWithdrawClaimsByEpoch` funding coverage so an unfunded receipt can never spend the funded pool.

### Proof of Concept
```solidity
// SPDX-License-Identifier: MIT
// Foundry fork test against a deployed IdleCDOEpochVariantPrefunded + IdleCreditVault pair.
function test_UnfundedInstantReceiptDrainsFundedPool() public {
    // -- setup: vault running epoch, A and B are whitelisted AA lenders --
    uint256 amt = 100_000e6; // USDC-scaled

    // 1. A deposits, requests instant withdraw; manager funds it.
    vm.prank(A); cdo.depositAA(amt);
    vm.prank(A); cdo.requestInstantWithdraw(amt); // A receipt minted
    // borrower repays / CDO pushes cash to strategy for A's receipt
    vm.prank(address(cdo));
    vault.collectInstantWithdrawFunds(amt);
    assertEq(underlying.balanceOf(address(vault)), amt);
    assertEq(vault.pendingInstantWithdraws(), 0);

    // 2. B requests instant withdraw; NOT funded.
    vm.prank(B); cdo.depositAA(amt);
    vm.prank(B); cdo.requestInstantWithdraw(amt);
    assertEq(vault.pendingInstantWithdraws(), amt); // unfunded remainder
    assertEq(underlying.balanceOf(address(vault)), amt); // only A's money

    // 3. B claims first -> paid at par from A's funded balance.
    vm.prank(address(cdo));
    vault.claimInstantWithdrawRequest(B);
    assertEq(underlying.balanceOf(B), amt);      // B stole A's funding
    assertEq(underlying.balanceOf(address(vault)), 0);

    // 4. A's funded claim now reverts / stays frozen.
    vm.prank(address(cdo));
    vm.expectRevert(); // safeTransfer on empty balance
    vault.claimInstantWithdrawRequest(A);

    // 5. If borrower defaults, A is haircutted to defaultRecoveryPrice
    //    via _claimDefaultedInstantWithdrawRequest instead of par.
}
```

Note: I could not fully trace `IdleCDOEpochVariantPrefunded`'s claim path and the exact conditions under which `collectInstantWithdrawFunds` is invoked (the grep did not return the function bodies within the iteration limit), so step 1's caller/approval details may differ slightly. The core gap — no per-receipt funding check in `claimInstantWithdrawRequest`/`_transferFundedClaim` — is directly visible in the cited code.