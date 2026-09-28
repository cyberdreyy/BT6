I need to check how `stopEpoch` orders the `epochNumber` bump vs `collectWithdrawFunds` to see whether the loss-recovery epoch keying aligns with `lastWithdrawRequest`.### Title
Loss-adjusted withdraw receipts haircut only the last-request epoch; earlier-epoch pending receipts escape the loss and redeem at par - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.collectWithdrawFunds` socializes a `stopEpochWithDuration` loss across the *aggregate* `pendingWithdraws` and stores a single `lossRecoveryPriceByEpoch` haircut. But `_claimLossAdjustedWithdrawRequest` applies that haircut only to the receipt stored under `lastWithdrawRequest[user]` (the user's most recent request epoch). Pending receipts recorded under earlier epochs in `withdrawsRequestsByEpoch` are never haircutted; they remain in `withdrawsRequests[user]` and are paid at par by `_claimFundedWithdrawRequest`. This mirrors the GMX bug where a partially filled order is left untouched at a stale (pre-loss) price: part of the "order" is settled at the old full price even though the counterparty (borrower/strategy) only funded the reduced amount.

### Finding Description
- `requestWithdraw` can accumulate receipts in more than one epoch before any claim: `lastWithdrawRequest[user]` is overwritten with `epochNumber` on each request, while each amount is also stored per-epoch in `withdrawsRequestsByEpoch[user][epoch]` and aggregated in `withdrawsRequests[user]` and `pendingWithdraws` (IdleCreditVault.sol, `requestWithdraw` ~L279-294).
- On a lossy stop, `collectWithdrawFunds` computes `lossRecoveryPrice = funded / pendingBasis` over the *whole* `pendingWithdraws` and zeroes it (L411-421). The borrower therefore only repays `pendingBasis * price` for all outstanding receipts.
- On claim, `_claimLossAdjustedWithdrawRequest` looks up `lossRecoveryPriceByEpoch[lastWithdrawRequest[user]]` and calls `_clearWithdrawClaimForEpoch(user, lastWithdrawRequest)` — clearing *only that epoch's* `withdrawsRequestsByEpoch` entry and subtracting only that epoch's amount from `withdrawsRequests[user]` (L789-801, L811-836).
- `_claimFundedWithdrawRequest` then pays the remaining `withdrawsRequests[user]` (i.e., all earlier-epoch receipt basis) at par via `_transferFundedClaim` (L319-349).
- Net effect: a user with receipts R1 (epoch N) and R2 (epoch N+1) is funded `(R1+R2)*price` but paid `R1 + R2*price`. The excess `R1*(1-price)` is taken from strategy-held underlyings that back other claimants, breaking solvency/one-receipt-one-payout.
- The existing guard in `requestWithdraw` (L263-271) only blocks a *new* request when a loss-adjusted receipt exists; it does not prevent accumulating receipts across epochs *before* the loss occurs, and `_clearWithdrawClaimForEpoch` never iterates other epoch keys.

### Impact Explanation
Direct theft / insolvency: the claimant redeems more underlyings than the borrower funded for pending receipts. With a large loss ratio (e.g., `lossRecoveryPrice` = 50%), a user splitting requests across two epochs recovers `R1 + R2/2` against funding of `(R1+R2)/2`, extracting `R1/2` of underlyings belonging to other receipt holders or the recovery reserve; last claimers are left unpayable. Loss scales with the earlier-epoch receipt size and the haircut depth.

### Likelihood Explanation
Requires only unprivileged actions: two `requestWithdraw` calls in consecutive epochs (skipping the claim of the first), then a `stopEpochWithDuration` loss — a normal protocol event, not attacker-controlled. No privileged cooperation, no timing race; deterministic whenever a user holds unclaimed receipts across ≥2 epochs at a lossy stop.

### Recommendation
Apply `lossRecoveryPriceByEpoch` to *all* pending receipt epochs of the user, not just `lastWithdrawRequest`: either iterate `withdrawsRequestsByEpoch` for the user, or store the haircut on a per-request basis at request time / on a global "pending receipts cohort" rather than a single epoch key. Alternatively, store `lossRecoveryPriceByEpoch` under each epoch that contributed to `pendingWithdraws` and have `_claimFundedWithdrawRequest` revert while any unclaimed loss-adjusted epoch exists.

### Proof of Concept
```solidity
// Foundry fork-style PoC on the IdleCreditVault / IdleCDOEpochVariant suite
// Mode: normal (non-APR0) withdraws, running epochs.
function testLossAdjustedClaimEscapeEarlierEpoch() external {
    uint256 amount = 10_000 * ONE_SCALE;
    uint256 minted = idleCDO.depositAA(amount);
    _transferBurnedTrancheTokens(address(this), true);

    // Epoch 0: request R1 (half position)
    uint256 R1 = cdoEpoch.requestWithdraw(minted / 2, address(AAtranche));
    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr, _expectedFundsEndEpoch());
    // epochNumber now 1; R1 is claimable but user deliberately does NOT claim

    // Epoch 1: request R2 (rest)
    uint256 R2 = cdoEpoch.requestWithdraw(0, address(AAtranche));
    _startEpochAndCheckPrices(1);

    // Lossy stopEpoch: borrower only funds 50% of pendingWithdraws (R1+R2)
    IdleCreditVault cv = IdleCreditVault(address(strategy));
    uint256 pending = cv.pendingWithdraws();          // == R1 + R2
    uint256 funded  = pending / 2;
    deal(defaultUnderlying, borrower, cdoEpoch.expectedEpochInterest() + funded);
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(... /* realize loss so collectWithdrawFunds(funded) */);
    // => cv.lossRecoveryPriceByEpoch(epochNumber) == 0.5e18, pendingWithdraws == 0

    // Claim: R2 haircut to R2*0.5, but R1 pays at PAR
    uint256 balPre = underlying.balanceOf(address(this));
    cdoEpoch.claimWithdrawRequest();
    uint256 paid = underlying.balanceOf(address(this)) - balPre;

    // Funded was (R1+R2)/2, user received R1 + R2/2 => excess R1/2 stolen
    assertEq(paid, R1 + R2 / 2);
    assertGt(paid, funded); // strategy underlyings drained by R1*(1 - price)
}
```

Caveat: I could not fully verify the exact ordering of `epochNumber` increments vs `collectWithdrawFunds` inside `stopEpochWithDuration` within the iteration budget, so the precise epoch key under which `lossRecoveryPriceByEpoch` is stored (and whether the haircut additionally misses via a key mismatch) should be confirmed; the cross-epoch haircut escape described above holds regardless of that detail because `_claimLossAdjustedWithdrawRequest` only ever clears the `lastWithdrawRequest` epoch.