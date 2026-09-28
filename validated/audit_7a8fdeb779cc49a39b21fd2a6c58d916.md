### Title
Loss-adjusted withdraw receipts escape the haircut and are paid at par after a second `requestWithdraw` overwrites `lastWithdrawRequest` - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault._claimLossAdjustedWithdrawRequest` decides whether a user's pending receipt is loss-adjusted by looking up `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` — i.e., it only checks the *most recent* request epoch. Like the kernel bug (freeing pages without checking the `decrypted` field), the vault "frees" a haircutted receipt back into the funded pool at par because the per-epoch loss marker is keyed off a mutable "latest request" field that a new `requestWithdraw` silently overwrites. An unprivileged lender can convert a loss-adjusted claim into a par claim, draining underlying that was never funded.

### Finding Description
When `stopEpochWithDuration(_lossAmount)` realizes a loss, the CDO calls `collectWithdrawFunds` with less than `pendingWithdraws`; the vault then stores `lossRecoveryPriceByEpoch[epochNumber] = _amount * 1e18 / pendingBasis` and zeroes `pendingWithdraws`, having pulled only the haircutted amount from the CDO (IdleCreditVault.sol:411-430). The haircutted basis remains claimable per user via `withdrawsRequestsByEpoch[user][lossEpoch]` and the aggregate `withdrawsRequests[user]`.

The claim path (`claimWithdrawRequest`, lines 301-314) runs:

1. `_claimLossAdjustedWithdrawRequest` uses `lossEpoch = lastWithdrawRequest[_user]` (line 790). If the user makes a *second* `requestWithdraw` in a later epoch, `lastWithdrawRequest[user]` is overwritten to the new epoch (line 282), and `lossRecoveryPriceByEpoch[newEpoch] == 0`, so the loss path returns 0 and the old haircutted basis is never cleared or haircutted.
2. `_claimFundedWithdrawRequest` then only checks `epochNumber > lastWithdrawRequest[_user]` (line 326) and pays `withdrawsRequests[_user]` — the *aggregate*, which still includes the old loss-adjusted basis — at par (lines 338-349), burning receipt tokens 1:1.

Re-requesting while a receipt is unclaimed is explicitly supported ("if a user does not claim a withdraw request and instead requests another withdraw, he will have to wait for another epoch to claim both requests", lines 323-324). Nothing else removes the loss-epoch basis from `withdrawsRequests[_user]` — `_clearWithdrawClaimForEpoch` (lines 811-837) is only reached through the defaulted or loss-adjusted paths, and the loss path is unreachable once the marker moves. Meanwhile `pendingWithdraws` was zeroed at loss-collection time, so the borrower never funds that basis again; the strategy only holds `pendingBasis * lossRecoveryPrice`.

### Impact Explanation
Direct theft / insolvency. After one more epoch, the attacker claims `(oldLossBasis + newRequestBasis)` at par while the strategy was only funded `oldLossBasis * price + newRequestBasis`. The shortfall `oldLossBasis * (1 - price)` is paid from other users' funded claims/reserves, or later claims revert for lack of balance — the "one receipt, one funded payout" invariant is broken. Loss is quantified as the haircut amount the attacker escapes, which can be nearly the entire receipt if the realized loss is large (up to the limit where `lossRecoveryPrice` rounds to zero, blocked by the line 419 revert).

### Likelihood Explanation
Requires only: (a) a KYC-passing lender with a pending withdraw receipt in an epoch where `stopEpochWithDuration` realizes a loss (a designed mode), and (b) a second `requestWithdraw` of arbitrary small size in the next epoch — an ordinary, permitted user action. Honest privileged actors (manager stopping epochs, borrower funding) supply all other steps. No race, no privileged collusion, no exotic mode needed (works in fixed-APR; also in APR0 since the normal-receipt bucket is affected).

### Recommendation
Track the loss-adjusted basis independently of `lastWithdrawRequest`. Options: iterate `withdrawsRequestsByEpoch` against a per-user set of request epochs, or store a `lossEpoch`/`lossBasis` per user at the moment `collectWithdrawFunds` applies a haircut and clear it in `_claimLossAdjustedWithdrawRequest` before consulting `lastWithdrawRequest`. At minimum, in `_claimFundedWithdrawRequest` subtract any outstanding loss-adjusted epochs for the user from the par payout instead of paying the raw aggregate.

### Proof of Concept
```solidity
// Foundry fork-style PoC sketch against IdleCDOEpochVariant + IdleCreditVault
// Setup: pool running with AA lender `attacker`, borrower honest, apr > 0.

// 1. Epoch N running: attacker requests withdraw of W underlyings.
vm.prank(attacker);
cdoEpoch.requestWithdraw(attackerTrancheBal, address(AAtranche));
// strategy.withdrawsRequests(attacker) == W; lastWithdrawRequest == N

// 2. stopEpochWithDuration realizes a loss L. Manager (honest) funds only
//    pendingToFund < W from previewLossAdjustedWithdrawFunds.
vm.prank(manager);
cdoEpoch.stopEpochWithDuration(lossArgs); // -> strategy.collectWithdrawFunds(funded < W)
// strategy.lossRecoveryPriceByEpoch[N] == funded*1e18/W < 1e18
// strategy.pendingWithdraws() == 0; strategy holds only `funded`

// 3. Epoch N+1 starts; attacker files a second, tiny withdraw request.
vm.prank(manager); cdoEpoch.startEpoch();
vm.prank(attacker);
cdoEpoch.requestWithdraw(dust, address(AAtranche));
// lastWithdrawRequest[attacker] == N+1  (loss marker for epoch N lost)

// 4. Epoch N+1 stops normally; borrower funds only `dust` (pendingWithdraws == dust).
vm.prank(manager); cdoEpoch.stopEpoch(apr, 0);

// 5. Attacker claims. Loss path sees lossRecoveryPriceByEpoch[N+1]==0 and skips;
//    funded path pays aggregate (W + dust) AT PAR.
vm.prank(attacker);
cdoEpoch.claimWithdrawRequest();
// Received ~W + dust, but strategy was funded only funded + dust.
// assertEq(payout, W + dust)  -> drained (W - funded) from other users' claims.
```