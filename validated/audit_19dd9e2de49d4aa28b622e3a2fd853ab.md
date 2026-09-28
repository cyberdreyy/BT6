### Title
Loss-adjusted withdraw receipts bypass the haircut via stale epoch keying in `lossRecoveryPriceByEpoch` - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault` stores the haircut for loss-adjusted (underfunded) withdraw receipts under `lossRecoveryPriceByEpoch[epochNumber]` inside `collectWithdrawFunds` (line 421), but looks the haircut up under `lastWithdrawRequest[_user]`, the epoch in which the receipt was *recorded* (lines 261, 790). `epochNumber` is incremented inside `deposit()` while the epoch is still running (lines 607-611), i.e. during `stopEpoch` when the CDO pushes borrower repayment *before* it calls `collectWithdrawFunds`. The result is a "shallow copy" of the epoch reference: the receipt's haircut lives under `epoch N+1` while every user pointer (`lastWithdrawRequest`, `withdrawsRequestsByEpoch`) still references epoch `N`. The loss-adjusted claim path therefore never fires, and the haircutted receipt is paid at par through `_claimFundedWithdrawRequest`, draining other claimants' funded underlyings.

### Finding Description
- `requestWithdraw` records the receipt under `currentEpoch = epochNumber` and sets `lastWithdrawRequest[_user] = currentEpoch` (lines 260-294).
- At `stopEpoch`, `IdleCDOEpochVariant` first deposits the borrower's repayment into the strategy. `deposit()` sees `isEpochRunning() == true` and executes `epochNumber += 1` (lines 607-611).
- The CDO then calls `collectWithdrawFunds(pendingToFund)` where `pendingToFund < pendingBasis` after `previewLossAdjustedWithdrawFunds`. The loss price is stored as `lossRecoveryPriceByEpoch[epochNumber]` — i.e. under the *new*, incremented epoch (line 421).
- The request-time guard (lines 261-271) and `_claimLossAdjustedWithdrawRequest` (lines 789-801) both read `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]`, which is the *old* epoch. That slot is always `0`, so:
  - the anti-re-request guard never fires — a user can open new requests while holding an unclaimed haircutted receipt;
  - on claim, `_claimLossAdjustedWithdrawRequest` returns 0 and `_claimFundedWithdrawRequest` executes. Its only epoch gate is `epochNumber <= lastWithdrawRequest[_user]` (line 326), which passes because `epochNumber` was already incremented past the request epoch. The user is paid `withdrawsRequests[_user]` **at par** via `_transferFundedClaim`, even though the strategy only collected `pendingBasis * lossRecoveryPrice` for those receipts.

Broken invariant: loss waterfall / one receipt one payout. Haircutted receipts redeem at 100%, so the funded pool is over-drawn; the last claimants (or the reserve guard at line 904) absorb the shortfall.

### Impact Explanation
Direct theft/insolvency: every withdraw receipt in a loss epoch escapes its `stopEpochWithDuration` haircut. If the borrower funded `pendingBasis * price`, claimants collectively withdraw `pendingBasis`, leaving later claimants with reverts or zero payout. Attacker is an ordinary KYC'd tranche holder requesting a withdraw before a loss stop-epoch; no privileged role needed. The loss itself is an honest manager/borrower action (`stopEpochWithDuration(_lossAmount)`), so the sequence is entirely sequenceable by an unprivileged user.

### Likelihood Explanation
Requires a realized epoch loss on a vault using the loss-adjusted withdraw flow (`stopEpochWithDuration`/`collectWithdrawFunds` partial funding) — a normal, designed code path, not an edge case. Once such a loss occurs, *every* pending receipt in that epoch claims at par automatically; no attacker sophistication needed beyond holding a pending withdraw request. Medium likelihood, high per-event impact.

### Recommendation
Key `lossRecoveryPriceByEpoch` by the epoch the receipts belong to. Either:
- store the haircut under `epochNumber - 1` when `collectWithdrawFunds` is called after the epoch increment, or
- have the CDO call `collectWithdrawFunds` before the deposit that increments `epochNumber`, or
- track the pending-receipt epoch explicitly (e.g. `pendingWithdrawsEpoch`) instead of reusing the live counter.

Also make the claim path iterate/verify all epochs with receipts rather than relying solely on the single `lastWithdrawRequest` pointer, mirroring the shallow-copy fix in the original advisory (own the referenced data instead of aliasing a mutable counter).

### Proof of Concept
Foundry fork test sketch (modeled on `test/foundry/IdleCreditVault.t.sol` helpers):

```solidity
function testLossEpochReceiptClaimsAtPar() external {
    // epoch E: user deposits AA and requests withdraw (records epochNumber = E)
    idleCDO.depositAA(amount);
    _startEpochAndCheckPrices(0);
    uint256 principal = cdoEpoch.requestWithdraw(amount, address(AAtranche));
    // withdrawsRequestsByEpoch[user][E] = amount; lastWithdrawRequest[user] = E

    // borrower realizes a loss at stopEpoch: stopEpochWithDuration(_lossAmount)
    // inside stopEpoch: deposit() -> epochNumber becomes E+1, then
    // collectWithdrawFunds(pendingToFund < pendingBasis) ->
    // lossRecoveryPriceByEpoch[E+1] = price < 1e18
    _startEpochAndCheckPrices(1); // epoch advances; claim gate satisfied

    // claimWithdrawRequest: lossRecoveryPriceByEpoch[E] == 0 -> skip haircut
    // _claimFundedWithdrawRequest pays full `amount` at par
    uint256 balPre = underlying.balanceOf(user);
    vm.prank(user);
    cdoEpoch.claimWithdrawRequest();
    assertEq(underlying.balanceOf(user) - balPre, amount); // expected: amount*price
}
```

Assert additionally that `lossRecoveryPriceByEpoch[E] == 0` while `lossRecoveryPriceByEpoch[E+1] != 0`, and that a second claimant's claim reverts or underpays because the funded underlying was over-drawn by the first par claim.

Caveat: the finding depends on the call order inside `IdleCDOEpochVariant.stopEpoch`/`stopEpochWithDuration` (repayment deposit, which increments `epochNumber`, occurring before `collectWithdrawFunds`). If the CDO collects withdraw funds before the incrementing deposit, the keys align and this specific escape is not reachable; the PoC's `lossRecoveryPriceByEpoch` assertions verify which ordering holds.