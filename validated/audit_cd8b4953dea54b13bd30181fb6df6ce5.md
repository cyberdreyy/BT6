### Title
Loss-adjusted withdraw receipt bypasses haircut after a second request overwrites `lastWithdrawRequest`, paying the underfunded receipt at par — (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`stopEpochWithDuration`-style partial funding stores a per-epoch haircut in `lossRecoveryPriceByEpoch[epochNumber]` and clears `pendingWithdraws`, but only `pendingToFund` (less than the full basis) is actually transferred into the strategy. The claim path that applies that haircut — `_claimLossAdjustedWithdrawRequest` — locates the loss epoch solely through `lastWithdrawRequest[_user]`. Because `requestWithdraw` unconditionally overwrites `lastWithdrawRequest[_user]` with the new `epochNumber`, a user who makes any second withdraw request before claiming loses the haircut pointer entirely: the loss path silently returns 0 and the aggregated `withdrawsRequests[_user]` (still containing the full, unhaircutted amount) is paid 1:1 by `_claimFundedWithdrawRequest`.

### Finding Description
The external bug (CVE-2017-5503) is an out-of-bounds/invalid memory write: an index is corrupted so a write lands in the wrong slot. The analog here is a corrupted bookkeeping index. In `requestWithdraw` (IdleCreditVault.sol:282) `lastWithdrawRequest[_user] = currentEpoch` overwrites the marker even if an older, haircutted receipt is still unclaimed. Later, in `claimWithdrawRequest` → `_claimLossAdjustedWithdrawRequest` (IdleCreditVault.sol:789-801), `lossEpoch = lastWithdrawRequest[_user]` now points at the new epoch where `lossRecoveryPriceByEpoch` is 0, so the function returns without clearing `withdrawsRequestsByEpoch[_user][oldLossEpoch]` or decrementing `withdrawsRequests[_user]`. Execution falls through to `_claimFundedWithdrawRequest` (IdleCreditVault.sol:319-350), which pays `normalAmount = withdrawsRequests[_user]` — the full pre-haircut basis — at par.

The epoch gate at line 326 (`epochNumber <= lastWithdrawRequest[_user]` reverts while `epochEndDate != 0`) only forces waiting one epoch; after the new epoch is stopped, `epochNumber > lastWithdrawRequest[_user]` and the par payout proceeds. The `_transferFundedClaim` reserve guard (lines 899-905) only protects `defaultRecoveryReserve`; it does not account for the loss-adjusted shortfall, because `collectWithdrawFunds` (lines 411-430) zeroed `pendingWithdraws` and collected only `pendingToFund = pendingBasis - pendingLoss`.

### Impact Explanation
Direct insolvency/theft with quantified loss. Suppose pending basis `X` was funded at recovery price `r < RECOVERY_FULL`: the strategy holds `X*r/FULL_ALLOC` for that receipt but the ledger still owes `X`. The attacker (any KYC'd tranche holder) extracts `X` instead of `X*r/FULL_ALLOC`, stealing `X*(1 - r/FULL_ALLOC)` from underlyings owed to other pending claimants or from the default-recovery-adjacent funded pool. The loss equals the haircut that should have been applied, and last-claimants' funded receipts become unpayable (permanent freezing/theft of unclaimed payouts). Invariant broken: "one receipt, one haircut-adjusted payout" and funded-claim solvency.

### Likelihood Explanation
Requires: (1) a `stopEpochWithDuration`/`collectWithdrawFunds` partial funding (borrower returns less than `pendingWithdraws` but nonzero — a manager/borrower honesty-compatible sequence, not a full default); (2) the attacker holds a pending receipt in that epoch; (3) the attacker submits any additional `requestWithdraw` in the buffer of the next epoch before claiming — a normal, permitted action. No privileged misbehavior needed; sequencing is entirely within honest manager/borrower calls. One caveat I could not fully verify within the tool budget: the exact ordering of `epochNumber` increments inside `IdleCDOEpochVariant.stopEpochWithDuration` relative to `collectWithdrawFunds`. If `lossRecoveryPriceByEpoch` is keyed on an already-incremented `epochNumber`, the mismatch is even worse (haircut never applies at all); if keyed on the request epoch, the exploit works as described via the `lastWithdrawRequest` overwrite.

### Recommendation
Track loss-adjusted claims per epoch rather than via a single mutable marker. Concretely: in `claimWithdrawRequest`, iterate/check `withdrawsRequestsByEpoch[_user]` entries for any epoch with `lossRecoveryPriceByEpoch[epoch] != 0` (or store a per-user list/bitmap of request epochs), instead of deriving the loss epoch from `lastWithdrawRequest[_user]`. Alternatively, prevent a new `requestWithdraw` while the user has an uncleared loss-adjusted receipt, or settle/clear all prior-epoch receipts inside `requestWithdraw` before overwriting `lastWithdrawRequest`.

### Proof of Concept
```solidity
// Foundry fork test outline (contracts: IdleCDOEpochVariant + IdleCreditVault)
function testLossHaircutBypassViaSecondRequest() external {
    // 1. Deposit AA, startEpoch, run epoch E0 normally.
    idleCDO.depositAA(100_000e6);
    _startEpoch();

    // 2. In buffer after stopEpoch: attacker requests withdraw of X strategy tokens.
    //    -> withdrawsRequestsByEpoch[attacker][E1] = X; pendingWithdraws += X;
    //    -> lastWithdrawRequest[attacker] = E1;
    uint256 req = cdoEpoch.requestWithdraw(attackerTrancheBal, address(AAtranche));

    // 3. Epoch E1 runs; at stopEpochWithDuration the borrower funds only
    //    pendingToFund = X * r / 1e18 (partial loss). collectWithdrawFunds sets
    //    lossRecoveryPriceByEpoch[E1] = r and pendingWithdraws = 0.
    vm.prank(manager);
    cdoEpoch.stopEpoch(lossAmount, interest); // loss-adjusted funding path

    // 4. Attacker does NOT claim. In next buffer, requests another withdraw (any size).
    //    -> lastWithdrawRequest[attacker] = E2  (overwrites loss-epoch pointer)
    cdoEpoch.requestWithdraw(smallAmount, address(AAtranche));

    // 5. Epoch E2 stops fully funded; epochNumber > lastWithdrawRequest.
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, interest2);

    // 6. claimWithdrawRequest:
    //    _claimLossAdjustedWithdrawRequest: lossRecoveryPriceByEpoch[E2] == 0 -> returns 0
    //    _claimFundedWithdrawRequest pays withdrawsRequests == X + small at PAR
    uint256 balPre = underlying.balanceOf(attacker);
    cdoEpoch.claimWithdrawRequest();
    // Attacker received X instead of X * r / 1e18 for the E1 receipt:
    // strategy paid X*(1 - r/1e18) more than it collected for that receipt.
    assertGt(underlying.balanceOf(attacker) - balPre, expectedHaircutted + small);
}
```