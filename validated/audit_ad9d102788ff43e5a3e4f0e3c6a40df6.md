### Title
Post-default withdraw requests pay 1:1 from the fixed `defaultRecoveryReserve`, draining funds reserved for defaulted claimants (double-spend of the recovery reserve) - (File: `contracts/strategies/idle/IdleCreditVault.sol`)

### Summary
The NVDEC double-free maps to idle-tranches as a **double-spend of a fixed pool**: the same accounting unit is released twice — once implicitly allocated to defaulted claimants at `defaultRecoveryPrice`, and again paid out at par to post-default requesters. After `finalizeDefaultRecovery` sizes `defaultRecoveryReserve` as exactly `totalBasis * recoveryPrice / RECOVERY_FULL`, any tranche holder can call `requestWithdraw` (post-default branch, `IdleCreditVault.sol:247-257`), receive a `postDefaultRequests` receipt minted with **zero new underlying funding**, and then claim it 1:1 through `_claimPostDefaultWithdrawRequest` → `_transferDefaultRecovery`, which decrements the already-fully-allocated reserve (`IdleCreditVault.sol:912-917`).

### Finding Description
`finalizeDefaultRecovery` computes the reserve to exactly cover all defaulted claims at the recovery haircut (`IdleCreditVault.sol:686-692`): every defaulted receipt is owed `claimBasis * defaultRecoveryPrice / RECOVERY_FULL`, and the sum of all such payouts equals the reserve. Post-default `requestWithdraw` (line 254-256) burns/mints strategy tokens but adds **no** underlying and does not increase `defaultRecoveryReserve`. Yet `_claimPostDefaultWithdrawRequest` (lines 760-767) pays `amount` at par via `_transferDefaultRecovery`, which unconditionally subtracts from `defaultRecoveryReserve` (line 915). Every post-default payout therefore consumes reserve belonging to defaulted-epoch claimants: an attacker who deposits/requests after finalization withdraws `X` underlyings at 100% while honest defaulted users are only entitled to `X * recoveryPrice` — a reserve shortfall of `X * (1 - recoveryPrice)` per claim, until `defaultRecoveryReserve -= _amount` underflows and all remaining defaulted claims revert permanently.

### Impact Explanation
Direct theft plus permanent freezing: a post-default requester extracts underlyings at par from a reserve priced at the recovery haircut, and once cumulative post-default claims exceed the dust slack, honest defaulted claimants' `_transferDefaultRecovery` calls revert on underflow, permanently freezing their unclaimed recovery. Quantified: with `recoveryPrice = 50%`, a post-default claim of `X` steals `X/2` of reserve backing defaulted claims; an attacker cycling requests drains up to the full reserve.

### Likelihood Explanation
Reachable by any unprivileged tranche-token holder: after default finalization they call `cdoEpoch.requestWithdraw` (the strategy path explicitly "preserve[s] request/claim UX after default", line 252-256) and then `claimWithdrawRequest`, which routes to `_claimPostDefaultWithdrawRequest` before defaulted claims (lines 303-311). No guard caps post-default payouts against reserve solvency — the `_transferFundedClaim` solvency guard (lines 899-905) is bypassed because post-default claims use `_transferDefaultRecovery` instead.

### Recommendation
Fund post-default requests with real underlying (e.g., require the CDO/borrower to supply the payout, or mint them against fresh deposits), or pay post-default claims from a separate prefunded bucket rather than `defaultRecoveryReserve`. Alternatively, revert `requestWithdraw` once `defaultRecoveryFinalized` until the recovery reserve is fully claimed, or scale post-default payouts by `defaultRecoveryPrice` so reserve accounting stays solvent.

### Proof of Concept
Foundry fork PoC (concept, extending `test/foundry/IdleCreditVault.t.sol` default flows):

```solidity
// 1. Deposit AA/BB, run epoch, force borrower default (no deal to borrower),
//    stopEpoch -> default path; manager calls finalizeDefault with partial recovery
//    such that defaultRecoveryPrice = 0.5e18 (50%).
// 2. Attacker (any EOA holding tranches) post-finalization:
vm.prank(attacker);
cdoEpoch.requestWithdraw(attackerTranches, address(AAtranche)); // mints postDefaultRequests
vm.prank(attacker);
cdoEpoch.claimWithdrawRequest(); // pays attacker 1:1 from defaultRecoveryReserve
// 3. Honest defaulted user:
vm.prank(victim);
vm.expectRevert(); // underflow in _transferDefaultRecovery once reserve drained
cdoEpoch.claimWithdrawRequest();
assertLt(underlying.balanceOf(victim), victimBasis * 0.5e18 / 1e18);
```

Uncertainty: I confirmed the strategy-side paths in `IdleCreditVault.sol` but could not fully verify the CDO-side entry (`IdleCDOEpochVariant.requestWithdraw`/`claimWithdrawRequest`) routing post-default; the strategy code's explicit post-default branch and comments indicate it is reachable via the CDO.