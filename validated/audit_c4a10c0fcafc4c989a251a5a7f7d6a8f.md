### Title
Stale `lastWithdrawRequest` lets a loss-adjusted withdraw receipt replay at par — (`File: contracts/strategies/idle/IdleCreditVault.sol`)

### Summary
The external advisory is a capture-replay bug: a previously seen message is replayed to obtain an action's effect again. The analog here is a stale-epoch replay of a withdraw receipt: `IdleCreditVault._claimLossAdjustedWithdrawRequest` looks up the haircut price only at `lastWithdrawRequest[_user]`, but a new `requestWithdraw` overwrites that marker. A loss-adjusted receipt from an earlier epoch is then silently re-priced at par by `_claimFundedWithdrawRequest`, replaying a receipt that was only partially funded as if it had been fully funded.

### Finding Description
When `stopEpoch` funds pending withdraws for less than the pending basis, `collectWithdrawFunds` records the haircut under the *current* epoch: `lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice` and zeroes `pendingWithdraws` (IdleCreditVault.sol:411-429). Critically, the per-user aggregates `withdrawsRequests[_user]` and `withdrawsRequestsByEpoch[_user][E1]` are left intact — the haircut is only enforced lazily at claim time via `_claimLossAdjustedWithdrawRequest`, which reads `lossEpoch = lastWithdrawRequest[_user]` and then `lossRecoveryPriceByEpoch[lossEpoch]` (IdleCreditVault.sol:789-801).

`requestWithdraw` unconditionally overwrites `lastWithdrawRequest[_user] = currentEpoch` (IdleCreditVault.sol:282) with no check that an earlier loss-adjusted receipt is still unclaimed. After the attacker requests again in epoch E2, `lastWithdrawRequest = E2`, so `lossRecoveryPriceByEpoch[E2] == 0` and the loss-adjusted path returns 0. Once epoch E2 ends, `_claimFundedWithdrawRequest` passes its gate (`epochNumber > lastWithdrawRequest`) and pays the full `withdrawsRequests[_user]` — which still includes the haircutted E1 basis — at par through `_transferFundedClaim` (IdleCreditVault.sol:338-349). The E1 shortfall was never collected by `collectWithdrawFunds`, so the difference is paid out of strategy underlyings belonging to other claimants.

Broken invariant: one receipt, one correctly-priced payout; the loss waterfall's pending-receipt haircut is escaped.

### Impact Explanation
Direct theft / insolvency. The attacker recovers `claimBasis_E1 * (1 - lossRecoveryPrice)` in excess of what was actually funded for their receipt. The strategy pays this from its underlying balance, draining the reserve that backs other users' funded receipts and instant-withdraw claims, so the last claimants are left unpaid. Loss scales with the epoch-E1 receipt size and the haircut depth; with a 50% loss-adjusted price on a 100k USDC receipt, 50k USDC is stolen.

### Likelihood Explanation
Requires a `stopEpochWithDuration`/`collectWithdrawFunds` shortfall epoch (honest owner/manager action, triggered by a realized borrower loss — a designed-for scenario), plus the attacker making any second withdraw request in a later epoch, which any tranche holder can do permissionlessly with a dust amount. No privileged role, timing race, or external dependency is needed. The only existing guard — the loss-adjusted lookup — is bypassed precisely because the marker is a single mutable slot rather than per-epoch.

### Recommendation
Track loss-adjusted epochs per user instead of relying on the single `lastWithdrawRequest` slot: e.g., iterate `lossRecoveryPriceByEpoch` over the user's request epochs, or store the user's loss-epoch explicitly (e.g., `lossEpochByUser[_user]`) when `collectWithdrawFunds` records a haircut, and have `requestWithdraw` refuse (or first settle) when the user has an unclaimed loss-adjusted receipt. Alternatively, subtract the underfunded portion from `withdrawsRequestsByEpoch`/`withdrawsRequests` at `collectWithdrawFunds` time so the haircut is crystallized rather than deferred to a look-up that can be invalidated.

### Proof of Concept
```solidity
// Foundry fork test sketch (per test/foundry/IdleCreditVault.t.sol harness)
// Setup: KYC'd attacker deposits and holds AA tranches.

// Epoch E1: attacker requests withdraw of X tranche tokens.
cdoEpoch.requestWithdraw(attackerTranches, address(AAtranche));
// Honest stop: borrower realizes a loss; collectWithdrawFunds funds only part.
// -> lossRecoveryPriceByEpoch[E1] = e.g. 0.5e18, pendingWithdraws = 0
// -> withdrawsRequests[attacker] still == X (full basis)

// Epoch E2 (running): attacker requests a second, dust withdraw.
cdoEpoch.requestWithdraw(1, address(AAtranche));
// lastWithdrawRequest[attacker] = E2, overwriting E1.

// After E2 ends (epochNumber > E2), attacker claims.
cdoEpoch.claimWithdrawRequest();
// _claimLossAdjustedWithdrawRequest: lossRecoveryPriceByEpoch[E2] == 0 -> returns 0
// _claimFundedWithdrawRequest pays withdrawsRequests[attacker] (= X + dust) AT PAR.

uint256 stolen = X * (RECOVERY_FULL - lossRecoveryPriceByEpoch[E1]) / RECOVERY_FULL;
assertGt(underlying.balanceOf(attacker) - balPre, fundedAmount, "receipt replayed at par");
// stolen comes from strategy underlying reserved for other funded claims.
```