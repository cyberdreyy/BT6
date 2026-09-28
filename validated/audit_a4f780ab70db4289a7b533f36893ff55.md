### Title
Loss-adjusted withdraw haircut bypassed by combining an old unclaimed receipt with a new request in the loss epoch — (`contracts/strategies/idle/IdleCreditVault.sol`)

### Summary
`IdleCreditVault` spreads a realized `stopEpoch` loss pro rata over **all** pending receipts (`previewLossAdjustedWithdrawFunds` uses the aggregate `pendingWithdraws`), but at claim time `_claimLossAdjustedWithdrawRequest` applies the resulting `lossRecoveryPriceByEpoch` only to receipts recorded under `lastWithdrawRequest[_user]` — the user's *latest* request epoch. Older, still-unclaimed receipts in `withdrawsRequests[_user]` are then paid at par by `_claimFundedWithdrawRequest`. A user who holds an unclaimed funded receipt and requests again in the epoch that later takes a loss keeps the old receipt at full value while the loss price was diluted by that same receipt, paying out more underlying than was funded and leaving other claimants unbacked.

### Finding Description
In `collectWithdrawFunds` (`contracts/strategies/idle/IdleCreditVault.sol:411-430`), when the borrower funds less than `pendingBasis`, the contract zeroes `pendingWithdraws` and stores `lossRecoveryPriceByEpoch[epochNumber] = _amount * RECOVERY_FULL / pendingBasis`. `previewLossAdjustedWithdrawFunds` (`:440-460`) computes the loss over `activeBasis + pendingBasis`, so the haircut price is intentionally spread across *every* pending receipt, regardless of which epoch it was created in.

At claim time, however:

- `_claimLossAdjustedWithdrawRequest` (`:789-801`) looks up `lossEpoch = lastWithdrawRequest[_user]` and calls `_clearWithdrawClaimForEpoch`, which only subtracts `withdrawsRequestsByEpoch[_user][lossEpoch]` from the aggregate `withdrawsRequests[_user]` (`:815-820`). Older-epoch receipts remain in `withdrawsRequests[_user]`.
- `_clearWithdrawClaimForEpoch` then resets `lastWithdrawRequest[_user] = 0` (`:832-836`), which removes the only epoch gate in `_claimFundedWithdrawRequest` (`epochNumber <= lastWithdrawRequest[_user]`, `:326-328`).
- `_claimFundedWithdrawRequest` (`:319-350`) then pays the remaining `withdrawsRequests[_user]` (the old receipt) at par via `_transferFundedClaim`.

The `requestWithdraw` guard (`:263-271`) only blocks a *new* request when a loss-adjusted receipt already exists (`lossRecoveryPriceByEpoch[lossEpoch] != 0`); a normally funded old receipt does not block re-requesting, and the code comments at `:323-324` explicitly acknowledge stacking requests.

Numerically: old receipt `F`, new receipt `N`, funded `A < F+N`, price `p = A/(F+N)`. Attacker receives `F + N·p`, while the fair pro-rata payout for their whole position is `(F+N)·p = A`. Excess extracted: `F·(1 − p)`. This is drawn from the same pool of underlying that backs other users' loss-adjusted claims, so the last claimers' receipts are undercollateralized.

### Impact Explanation
Broken invariant: fair loss socialization / one receipt one (pro-rata) payout. An unprivileged tranche-token holder can, with only two `requestWithdraw` calls across two epochs and no privileged cooperation, withdraw strictly more than their loss-adjusted entitlement. The excess is a direct theft of funded underlying that was proportionally earmarked for all pending claimants, producing insolvency (or permanently stranded receipts) for later claimers of magnitude `F·(1 − p)` per attacker, scalable with `F`.

### Likelihood Explanation
Requires a `stopEpochWithDuration`/`stopEpoch` epoch ending in partial funding (a real loss event, which is precisely when the recovery machinery runs) and an attacker who timed a re-request before that epoch. Any honest user can also accidentally end up in this state, but an attacker can deliberately hold an unclaimed receipt and re-request every epoch to always be positioned. No privileged misbehavior is needed; manager/owner calls (start/stop epoch, loss reporting) happen in their normal honest flow.

### Recommendation
Either apply `lossRecoveryPriceByEpoch[epochNumber]` to the user's **entire** `withdrawsRequests`/`apr0` basis at claim time (not just the last-request-epoch slice), or only compute the haircut over same-epoch receipts in `collectWithdrawFunds`/`previewLossAdjustedWithdrawFunds` and keep old funded receipts fully segregated. The simplest consistent fix: in `_claimLossAdjustedWithdrawRequest`, treat the whole `withdrawsRequests[_user]` (plus open APR0 principal) as the claim basis when a loss price exists for `lastWithdrawRequest[_user]`, since `collectWithdrawFunds` already zeroed the aggregate `pendingWithdraws`.

### Proof of Concept
Foundry fork test sketch (setup mirrors `test/foundry/IdleCreditVault.t.sol`):

```solidity
function testLossHaircutBypassViaStackedReceipts() external {
    // user deposits and requests withdraw in epoch N-1
    uint256 amt = 100_000 * ONE_SCALE;
    _depositWithUser(attacker, amt, true);
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(0, address(AAtranche)); // receipt F

    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr, _expectedFundsEndEpoch()); // fully funded, no loss price

    // attacker does NOT claim; requests again in epoch N
    _depositWithUser(victim, amt, true);           // victim has same-size active position
    vm.prank(victim);
    cdoEpoch.requestWithdraw(0, address(AAtranche));
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(0, address(AAtranche)); // receipt N, same epoch as victim

    _startEpochAndCheckPrices(1);
    // borrower repays only partially -> loss, lossRecoveryPriceByEpoch[N] < RECOVERY_FULL
    uint256 pendingBasis = strategy.pendingWithdraws();
    uint256 activeBasis = cdoEpoch.getContractValue() + cdoEpoch.expectedEpochInterest();
    uint256 lossAmount = (activeBasis + pendingBasis) / 2; // 50% loss
    (uint256 pendingToFund, ) = strategy.previewLossAdjustedWithdrawFunds(lossAmount);
    uint256 repay = cdoEpoch.expectedEpochInterest() + pendingToFund;
    deal(defaultUnderlying, strategy.borrower(), repay);
    vm.prank(strategy.borrower());
    IERC20Detailed(defaultUnderlying).approve(address(cdoEpoch), repay);
    cdoEpoch.stopEpochWithDuration(initialProvidedApr, 0, cdoEpoch.epochDuration(), lossAmount);

    uint256 p = strategy.lossRecoveryPriceByEpoch(strategy.epochNumber());
    uint256 attackerBalPre = IERC20Detailed(defaultUnderlying).balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimWithdrawRequest();

    uint256 fairShare = (/*F+N*/ pendingBasisShareOfAttacker * p) / 1e18;
    assertGt(
        IERC20Detailed(defaultUnderlying).balanceOf(attacker) - attackerBalPre,
        fairShare,
        'attacker paid more than loss-adjusted entitlement'
    );
    // victim's later claim is now underbacked / reverts on insufficient strategy balance
}
```

Note: I verified the claim-path mechanics (`_claimLossAdjustedWithdrawRequest` → per-epoch slice, `lastWithdrawRequest` reset → funded path at par) directly from `IdleCreditVault.sol`; the exact accounting of how much excess drains versus reverts on transfer should be confirmed by running the PoC, but the one-sided dilution (`F·(1−p)` overpayment against a fixed funded pool) follows directly from the stored price being computed over the aggregate `pendingBasis` while only epoch-N basis is haircut.