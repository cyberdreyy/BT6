### Title
Loss-adjusted withdraw receipt escapes haircut when a second request overwrites `lastWithdrawRequest` - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault._claimLossAdjustedWithdrawRequest` looks up the loss-epoch price using only `lastWithdrawRequest[_user]`, i.e. the *latest* request epoch. The haircut-clearing path therefore only ever clears the newest epoch's receipt. If a user holds a receipt from an earlier epoch that ended with `stopEpochWithDuration(_lossAmount > 0)` and then submits a second `requestWithdraw` in a later epoch, `lastWithdrawRequest[_user]` is overwritten. When the user finally claims, the loss-adjusted path reads the newer epoch (no `lossRecoveryPriceByEpoch` entry, early return) and `_claimFundedWithdrawRequest` pays the *aggregate* `withdrawsRequests[_user]` — including the old loss-epoch receipt — at par.

### Finding Description
In `requestWithdraw` the vault records receipts per epoch via `withdrawsRequestsByEpoch[_user][currentEpoch]` and aggregates them into `withdrawsRequests[_user]`, then stores `lastWithdrawRequest[_user] = currentEpoch` (contracts/strategies/idle/IdleCreditVault.sol:282-293).

On claim, `_claimLossAdjustedWithdrawRequest` uses `lossEpoch = lastWithdrawRequest[_user]` as the *only* epoch key into `lossRecoveryPriceByEpoch` (IdleCreditVault.sol:789-801). If that epoch has no loss price it returns 0 and never clears `withdrawsRequestsByEpoch[_user][earlierLossEpoch]`. Then `_claimFundedWithdrawRequest` pays `withdrawsRequests[_user]` — the aggregate of *all* epochs — at par via `_transferFundedClaim` (IdleCreditVault.sol:338-349).

Meanwhile `stopEpoch` only pulls the *post-loss* `_pendingWithdraws` amount from the borrower (`previewLossAdjustedWithdrawFunds` reduces it, IdleCDOEpochVariant.sol:391-393, 408). So the funded cash held by the strategy covers only the haircut value of the epoch-N receipt; paying it at par spends other LPs'/claimants' money.

Attack sequence (running epoch, loss mode, honest manager):
1. Attacker (KYC'd lender) deposits AA, calls `requestWithdraw` during epoch N buffer → receipt R1 recorded under epoch N.
2. Manager stops epoch N via `stopEpochWithDuration(..., _lossAmount > 0)` → `lossRecoveryPriceByEpoch[N]` set; borrower funds only `R1 * price`.
3. Epoch N+1 starts; attacker calls `requestWithdraw` again → `lastWithdrawRequest = N+1`, receipt R2.
4. Epoch N+1 ends normally (`lossRecoveryPriceByEpoch[N+1] == 0`).
5. Attacker calls `claimWithdrawRequest`: loss path returns 0, funded path pays `R1 + R2` at par. R1's haircut is stolen from funded strategy underlyings backing other pending claims/recovery reserve.

### Impact Explanation
Direct theft / insolvency: the attacker extracts the haircut portion `R1 * (1 - lossRecoveryPriceByEpoch[N]) / RECOVERY_FULL` that the waterfall assigned to their receipt. Funds come from the pool of underlyings collected for other pending withdraws (or, post-default, the recovery reserve via `_transferFundedClaim`'s reserve check), so other claimants are left underfunded — a quantified loss equal to the escaped haircut, potentially the entire pending-withdraw bucket if the attacker is the dominant requester.

### Likelihood Explanation
Requirements: a `stopEpochWithDuration` with non-zero `_lossAmount` (an existing, documented operating mode for realizing losses without full default) while the attacker holds a pending receipt, plus one later epoch. No privileged collusion, no oracle, no donation. The only guard — the `lastWithdrawRequest`/`epochNumber` check in `_claimFundedWithdrawRequest` — does not help because it compares against the newest epoch, not the stale loss epoch. The bug is deterministic once the sequence occurs.

### Recommendation
Track loss-adjusted claims per epoch rather than via a single `lastWithdrawRequest` key: iterate/clear `withdrawsRequestsByEpoch[_user]` for every epoch `< epochNumber` that has a `lossRecoveryPriceByEpoch` entry before paying the funded remainder, or store a per-user list of request epochs. Alternatively, refuse a new `requestWithdraw` while an earlier receipt sits in an epoch with a non-zero loss price until it is claimed/cleared.

### Proof of Concept
```solidity
// Foundry fork test sketch (against existing IdleCreditVault.t.sol harness)
// Setup: depositAA for attacker and another LP, KYC/mock as in _depositWithUser.

// Epoch N: attacker requests withdraw of half their position
vm.prank(attacker);
uint256 r1 = cdoEpoch.requestWithdraw(attackerTrancheBal / 2, address(AAtranche));
_startEpochAndCheckPrices(N);

// Stop epoch N with a realized loss (e.g. 50% of pending withdraws)
uint256 loss = r1 / 2;
vm.warp(cdoEpoch.epochEndDate() + 1);
vm.prank(manager);
cdoEpoch.stopEpochWithDuration(0, initialProvidedApr, epochDuration, loss);
assertGt(strategy.lossRecoveryPriceByEpoch(N + 1 /* epoch at request */), 0);

// Epoch N+1: attacker files a second request; lastWithdrawRequest overwritten
vm.prank(manager); cdoEpoch.startEpoch();
vm.prank(attacker);
uint256 r2 = cdoEpoch.requestWithdraw(0, address(AAtranche));
assertEq(strategy.lastWithdrawRequest(attacker), strategy.epochNumber());

// Stop epoch N+1 with NO loss
vm.warp(cdoEpoch.epochEndDate() + 1);
vm.prank(manager);
cdoEpoch.stopEpoch(0, initialProvidedApr);
assertEq(strategy.lossRecoveryPriceByEpoch(strategy.lastWithdrawRequest(attacker)), 0);

// Claim: loss-adjusted path returns 0; funded path pays r1 + r2 at par
uint256 balPre = underlying.balanceOf(attacker);
vm.prank(attacker);
cdoEpoch.claimWithdrawRequest();
uint256 paid = underlying.balanceOf(attacker) - balPre;

// Expected honest payout: r1 * lossPrice / 1e18 + r2. Actual: r1 + r2 at par.
assertGt(paid, r1 * strategy.lossRecoveryPriceByEpoch(N + 1) / 1e18 + r2);
// => attacker captured the haircut; strategy's funded-withdraw bucket is short
//    by r1 * (1 - lossPrice/1e18), borne by remaining claimants.
```