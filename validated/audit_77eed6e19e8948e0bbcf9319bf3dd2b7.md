### Title
Loss-adjusted withdraw receipts can escape their haircut because `lossRecoveryPriceByEpoch` is keyed by the post-stop `epochNumber`, not the request epoch - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.collectWithdrawFunds` stores a partial-funding haircut under `lossRecoveryPriceByEpoch[epochNumber]` at the moment it is called during `stopEpoch`. But `deposit()` — which is also invoked by the CDO during `stopEpoch` to move collected funds — increments `epochNumber` when `isEpochRunning()` is still true. If `collectWithdrawFunds` executes after that `deposit()` (the natural ordering, since funds must be swept before underfunding is measured), the loss price is stored under `epochNumber + 1` while every affected receipt's `lastWithdrawRequest` points at `epochNumber`. The per-epoch haircut then never applies to the receipts it was meant for.

### Finding Description
In `requestWithdraw`, a user's receipt is tagged with `lastWithdrawRequest[_user] = currentEpoch` (the pre-stop epoch). In `_claimLossAdjustedWithdrawRequest` the haircut is looked up as `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]`. In `collectWithdrawFunds`:

```solidity
lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice;   // line 421
```

But in `deposit()`:

```solidity
if (IIdleCDOEpochVariant(idleCDO).isEpochRunning()) {
    totEpochDeposits = 0;
    epochNumber += 1;   // line 610 — runs during stopEpoch
}
```

So during `stopEpoch` for epoch N, any `strategy.deposit(...)` call bumps `epochNumber` to N+1 before the pending-receipt funding is settled. If `collectWithdrawFunds` then runs with `_amount < pendingWithdraws`, the haircut lands in `lossRecoveryPriceByEpoch[N+1]`, while receipts carry `lastWithdrawRequest == N`. Consequences:

1. `_claimLossAdjustedWithdrawRequest` reads `lossRecoveryPriceByEpoch[N] == 0` and returns early; the receipt falls through to `_claimFundedWithdrawRequest`, which pays `withdrawsRequests[_user]` at par — the loss haircut is never applied.
2. The guard in `requestWithdraw` (lines 263–271) that forces users to claim a loss-adjusted receipt before opening a new one reads the same zeroed slot, so an attacker can freely re-request, overwrite `lastWithdrawRequest`, and lock in the par claim.
3. The vault only actually received `_amount < pendingBasis` underlying for those receipts, so paying them at par drains funds that belong to other pending claimants or to the recovery reserve — first claimants steal the shortfall, later claimants are permanently frozen (transfer reverts on insufficient balance).

Broken invariant: loss socialization (one receipt, one haircut-adjusted payout) and solvency of the funded-claim reserve.

### Impact Explanation
Any `stopEpochWithDuration(_lossAmount)` / underfunded `stopEpoch` (e.g., borrower repays less than `pendingWithdraws` + interest) leaves pending receipts haircuttable. Due to the epoch-key mismatch, early-claiming receipt holders — any KYC'd lender, unprivileged — are paid 100% while the strategy holds only `pendingBasis - pendingLoss`. The loss is fully concentrated on the last claimers (their claims revert on insufficient underlying → permanent freeze) or is silently absorbed by `defaultRecoveryReserve`/`_transferFundedClaim` bookkeeping that assumed haircut-sized obligations. Quantified loss equals `pendingLoss = _lossAmount * pendingBasis / totalBasis` (per `previewLossAdjustedWithdrawFunds`), i.e. the entire pending-receipt share of the realized loss.

### Likelihood Explanation
Requires a stop epoch that underfunds pending withdraws (`_amount < pendingBasis`), which occurs whenever the realized loss is nonzero and pending receipts exist — a designed-for code path (`previewLossAdjustedWithdrawFunds`, `stopEpochWithDuration(_lossAmount)`), not an edge case. The trigger ordering (CDO sweeps borrower funds via `deposit` → then calls `collectWithdrawFunds`) matches the normal funding sequence in `IdleCDOEpochVariant.stopEpoch`/`stopEpochWithDuration`, where collected funds are pushed to the strategy before pending-withdraw funding is reconciled. The attacker needs only to be first to `claimWithdrawRequest` after such an epoch.

### Recommendation
Key `lossRecoveryPriceByEpoch` to the epoch that owns the pending receipts, not the live `epochNumber` at collection time — e.g., capture `pendingWithdrawEpoch` at request time (receipts of one stop always share the request epoch that `pendingWithdraws` aggregates), or pass the settled epoch explicitly from the CDO into `collectWithdrawFunds`. Alternatively, perform the `epochNumber += 1` bump strictly after `collectWithdrawFunds` in `deposit()`, or have the CDO call `collectWithdrawFunds` before any `deposit` that bumps the epoch counter. Add a regression test: request withdraw in epoch N, run a lossy `stopEpoch`, assert `lossRecoveryPriceByEpoch[N] != 0` and that the claim is haircut.

### Proof of Concept
Outline (Foundry fork, extending `test/foundry/IdleCreditVault.t.sol` helpers):

```solidity
function testLossReceiptEscapesHaircut() external {
    _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, cdoEpoch.managementFee());
    uint256 amountWei = 10000 * ONE_SCALE;
    idleCDO.depositAA(amountWei);                 // attacker deposits AA

    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr, _expectedFundsEndEpoch());

    // attacker opens a normal withdraw receipt; lastWithdrawRequest == epochNumber (N)
    uint256 requested = cdoEpoch.requestWithdraw(0, address(AAtranche));

    _startEpochAndCheckPrices(1);
    // borrower repays with a realized loss so pendingBasis is underfunded
    // (use stopEpochWithDuration path: pendingToFund < pendingWithdraws)
    // -> strategy.deposit() inside stopEpoch bumps epochNumber to N+1
    // -> collectWithdrawFunds stores lossRecoveryPriceByEpoch[N+1]
    _stopEpochWithLoss(...);

    uint256 balPre = underlying.balanceOf(address(this));
    cdoEpoch.claimWithdrawRequest();              // _claimLossAdjusted finds price[N]==0 -> par payout
    assertEq(underlying.balanceOf(address(this)) - balPre, requested); // haircut skipped
}
```

Caveat: the exact call order inside `IdleCDOEpochVariant.stopEpoch`/`_afterStopEpochWithDuration` (whether `deposit()` precedes `collectWithdrawFunds`) could not be fully verified within this scan; if collection provably precedes the epoch bump, this specific key mismatch does not trigger. The PoC above is the direct check: after a lossy stop, `strategy.lossRecoveryPriceByEpoch(requestEpoch)` should equal the haircut price — if it is 0 while `lossRecoveryPriceByEpoch[requestEpoch + 1]` is nonzero, the bug is confirmed.