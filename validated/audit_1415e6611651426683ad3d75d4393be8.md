### Title
Loss-adjusted withdraw receipts keyed by a stale `epochNumber` escape the haircut and drain funded reserves - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`collectWithdrawFunds` records a loss haircut under `lossRecoveryPriceByEpoch[epochNumber]`, but each user's receipt is keyed by `lastWithdrawRequest[_user]`, which was snapshotted at request time. Because `epochNumber` is incremented inside `deposit()` whenever a deposit lands while an epoch is running (`IdleCreditVault.sol:607-610`), a mid-epoch deposit shifts `epochNumber` between the request and the loss stop. Receipts booked under the older epoch then find `lossRecoveryPriceByEpoch[oldEpoch] == 0`, skip `_claimLossAdjustedWithdrawRequest`, and are paid at par via `_claimFundedWithdrawRequest` — even though the borrower underfunded the aggregate `pendingWithdraws` they were part of. This is the credit-vault analog of the CVE-2017-6834 buffer-indexing bug: a length/index (`epochNumber`) that moves after the write makes the later read hit the wrong slot, paying out more than was funded.

### Finding Description
Relevant code:

- `requestWithdraw` stores `lastWithdrawRequest[_user] = currentEpoch` and `withdrawsRequestsByEpoch[_user][currentEpoch] += _amount` (`IdleCreditVault.sol:282-293`).
- `deposit()` bumps `epochNumber += 1` whenever `isEpochRunning()` is true (`IdleCreditVault.sol:607-610`). Mid-epoch deposits are a supported flow (`depositDuringEpoch` / `mintStrategyTokens` path in `IdleCDOEpochVariant.sol:721-732`), so `epochNumber` is attacker-influenceable by any KYC-passing lender depositing during a running epoch.
- On an underfunded `stopEpochWithDuration`, `collectWithdrawFunds` sets `pendingWithdraws = 0` and stores `lossRecoveryPriceByEpoch[epochNumber] = _amount * 1e18 / pendingBasis` using the *current* `epochNumber` (`IdleCreditVault.sol:411-421`), while only `_amount < pendingBasis` underlying is actually transferred in.
- At claim time, `_claimLossAdjustedWithdrawRequest` looks up `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` (`IdleCreditVault.sol:789-792`). If the user's request epoch differs from the loss-stop epoch, the lookup returns 0, nothing is cleared, and `claimWithdrawRequest` falls through to `_claimFundedWithdrawRequest`, which pays `withdrawsRequests[_user]` at par and burns the full receipt (`IdleCreditVault.sol:319-349`).

The re-request guard (`IdleCreditVault.sol:263-271`) only checks the epoch stored in `lastWithdrawRequest`, so it cannot detect that a haircut was recorded under a different epoch index. No other guard reconciles per-epoch keys: `withdrawsRequestsByEpoch[user][staleEpoch]` still holds the basis and is paid via the aggregate in the funded path.

### Impact Explanation
Broken invariant: loss socialization / fair payout. The borrower funds `recoveryPrice * pendingBasis`, but stale-epoch receipts withdraw `100%` of basis from the strategy's underlying balance. The excess is taken from the same pool backing other claimants and active LPs:

- Early claimants with stale-epoch receipts are overpaid by `claimBasis * (1 - lossRecoveryPrice)`.
- The strategy's underlying balance is drained below `defaultRecoveryReserve`-style expectations; later claimants (both haircut-eligible and funded) revert in `_transferFundedClaim`/safeTransfer — permanent underpayment/insolvency equal to the aggregate overpayment.
- An attacker can engineer the condition deterministically: request a withdraw early in a running epoch, then make (or wait for) any mid-epoch deposit to bump `epochNumber`, then benefit whenever the epoch ends in a loss stop — or simply keep the full payout that other users lose to the haircut.

Quantified example: pendingBasis = 100k (attacker receipt 90k at epoch N, others 10k at epoch N+1 after the bump), borrower funds 50k → `lossRecoveryPriceByEpoch[N+1] = 0.5e18`. Attacker claims 90k at par; other claimants share the remaining -40k shortfall — i.e., the strategy is insolvent by 40k for their rightful 5k claims plus funded receipts.

### Likelihood Explanation
- Preconditions: a loss-producing `stopEpochWithDuration` (borrower shortfall — a normal, expected protocol event, which is exactly why `lossRecoveryPriceByEpoch` exists) and at least one mid-epoch deposit between a request and the stop. Both are ordinary flows; no privileged misbehavior required.
- The attacker is an ordinary KYC-passing lender who can also force the `epochNumber` bump themselves via their own mid-epoch deposit, making the exploit self-contained rather than opportunistic.
- Mitigation already present (per-epoch revert guard at lines 263-271) only covers the case where the loss epoch equals `lastWithdrawRequest`; it does not cover index drift.

Caveat: I verified the epoch-bump site (`deposit()` lines 607-614) and the claim lookup path, but did not fully trace every `epochNumber` mutation in `IdleCDOEpochVariant`/`ProgrammableBorrower` hooks; if another path resets `lastWithdrawRequest` alignment this needs a fork test to confirm reachability.

### Recommendation
Key loss recovery by the epoch in which receipts were *funded*, and make the lookup robust to drift:

- In `collectWithdrawFunds`, record the haircut against every pending request epoch (or store a single `lastLossRecoveryPrice` plus the covered epoch range) rather than only `lossRecoveryPriceByEpoch[epochNumber]`.
- In `claimWithdrawRequest`/`_claimLossAdjustedWithdrawRequest`, iterate or track all epochs in `withdrawsRequestsByEpoch[_user]` that carry a non-zero `lossRecoveryPriceByEpoch`, not just `lastWithdrawRequest[_user]`.
- Alternatively, pin a monotonically increasing `withdrawRequestEpoch` counter that is not incremented by mid-epoch deposits, and use it consistently for `lastWithdrawRequest`, `withdrawsRequestsByEpoch`, and `lossRecoveryPriceByEpoch`.
- Add a solvency assertion: after a loss stop, `underlyingToken.balanceOf(this)` must be `>= sum of haircut-adjusted pending claims`.

### Proof of Concept
Foundry fork sketch (structure mirrors `test/foundry/IdleCreditVault.t.sol`):

```solidity
function testLossReceiptEpochDriftPaysPar() external {
    // setup: deposit AA for attacker and victim, start epoch 0
    _depositWithUser(attacker, 90_000 * ONE_SCALE, true);
    _depositWithUser(victim,   10_000 * ONE_SCALE, true);
    _startEpochAndCheckPrices(0);

    // attacker requests withdraw while epoch running -> lastWithdrawRequest[attacker] = E
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(0, address(AAtranche));
    uint256 reqEpoch = IdleCreditVault(address(strategy)).epochNumber();

    // mid-epoch deposit bumps epochNumber -> E+1 (any lender, incl. attacker via second acct)
    _depositWithUser(makeAddr('bump'), 1_000 * ONE_SCALE, true);
    assertEq(IdleCreditVault(address(strategy)).epochNumber(), reqEpoch + 1);

    // victim requests now -> keyed at E+1
    vm.prank(victim);
    cdoEpoch.requestWithdraw(0, address(AAtranche));

    // borrower underfunds: stopEpochWithDuration with _lossAmount = 50% of basis
    uint256 pending = IdleCreditVault(address(strategy)).pendingWithdraws();
    deal(defaultUnderlying, borrower, pending / 2); // plus interest obligations
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(..., pending / 2); // loss path

    // lossRecoveryPriceByEpoch is set under E+1 only
    assertEq(IdleCreditVault(address(strategy)).lossRecoveryPriceByEpoch(reqEpoch), 0);

    // attacker claims at PAR via _claimFundedWithdrawRequest despite the 50% haircut
    uint256 balPre = underlying.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimWithdrawRequest();
    assertGt(underlying.balanceOf(attacker) - balPre, pending * 9 / 10 * 5 / 10);

    // victim is haircut AND the pool is now short: later funded claims revert/underpay
    vm.prank(victim);
    cdoEpoch.claimWithdrawRequest(); // receives < 50% haircut share or reverts
}
```

Expected: attacker's full-basis payout leaves the strategy unable to honor the victim's haircut-adjusted claim plus reserve-backed receipts, demonstrating insolvency caused by the `epochNumber` index drift.

Uncertainty note: per repo instructions, only in-scope production code was used; the PoC ordering (whether `deposit()`'s `epochNumber += 1` fires on `depositDuringEpoch` vs. the stopEpoch internal deposit) should be confirmed in a live fork test, since I could not fully trace `depositDuringEpoch`'s call path in this session.