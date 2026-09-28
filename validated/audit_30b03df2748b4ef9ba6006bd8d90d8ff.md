### Title
Loss-adjusted withdraw receipt is paid at par when a second request moves `lastWithdrawRequest` to a non-loss epoch - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The external report is an identifier-confusion bug: two distinct resources share an ambiguous name, so an action meant for one is applied to the other. The analog in `IdleCreditVault` is the single per-user epoch marker `lastWithdrawRequest[_user]`, which is used both as the claim-delay gate and as the lookup key that decides whether a user's pending receipts belong to a loss-haircutted epoch (`lossRecoveryPriceByEpoch`). Because one marker is overloaded to identify "which epoch's receipts are loss-adjusted," a user who holds a haircutted receipt and then files any new withdraw request causes the haircutted receipt to be routed to the par-funded claim path, paying out funds the borrower never supplied.

### Finding Description
`requestWithdraw` records every request under the current epoch: `lastWithdrawRequest[_user] = currentEpoch`, `withdrawsRequests[_user] += _amount`, and `withdrawsRequestsByEpoch[_user][currentEpoch] += _amount` (`IdleCreditVault.sol:282-294`). When the borrower underfunds pending receipts, `collectWithdrawFunds` sets `lossRecoveryPriceByEpoch[epochNumber]` to the funded ratio and zeroes `pendingWithdraws` (`IdleCreditVault.sol:411-421`).

At claim time, `claimWithdrawRequest` routes the claim: `_claimLossAdjustedWithdrawRequest` reads `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` — i.e., it assumes the user's *latest* request epoch is the only epoch that can carry a haircut (`IdleCreditVault.sol:789-800`). If that lookup returns 0, execution falls through to `_claimFundedWithdrawRequest`, which pays the *aggregate* `withdrawsRequests[_user]` at par via `_transferFundedClaim` (`IdleCreditVault.sol:319-349`).

The confusion: `withdrawsRequestsByEpoch` keeps per-epoch basis, but the router that selects haircut vs. par only consults the single `lastWithdrawRequest` marker. The epoch-N haircutted basis remains inside the aggregate `withdrawsRequests[_user]` and is paid 1:1.

### Impact Explanation
Attacker (unprivileged KYC-passed lender):

1. Epoch N (running): calls `requestWithdraw` for a large amount.
2. Manager stops the epoch with a realized loss (`stopEpochWithDuration`/`collectWithdrawFunds` underfunding), so `lossRecoveryPriceByEpoch[N] = e.g. 0.5e18` and the strategy receives only 50% of the pending basis.
3. Attacker does not claim. In epoch N+1 they file a new (even dust-sized) `requestWithdraw`, which sets `lastWithdrawRequest[attacker] = N+1`.
4. After that request is funded at par at the next stop, attacker calls `claimWithdrawRequest` via the CDO. `lossRecoveryPriceByEpoch[N+1] == 0`, so the loss-adjusted path is skipped and `_claimFundedWithdrawRequest` pays the full aggregate — including the epoch-N basis that was only funded at 50%.

The claim drains the strategy's funded-claim balance by `(1 - lossRecoveryPrice) * epoch-N-basis` more than was collected for it. That shortfall is socialized onto other users' funded receipts: the attacker is paid with funds earmarked for later claimants (direct theft), or the final claimants' withdrawals revert on insufficient balance (insolvency/permanent freezing of unclaimed payouts). Loss scales with the attacker's epoch-N receipt size and the haircut depth — bounded only by their deposited principal and the epoch loss.

### Likelihood Explanation
Requirements are all ordinary protocol flows: a partial-loss `stopEpochWithDuration` (borrower underfunds pending receipts, a designed feature per `previewLossAdjustedWithdrawFunds`/`collectWithdrawFunds`), plus the attacker making two withdraw requests in different epochs. No privileged collusion, no oracle manipulation, no timing window tighter than an epoch boundary. Any lender can do it; the second request can be arbitrarily small, so cost is negligible. No existing guard catches it: `lossRecoveryPriceByEpoch` is never consulted for any epoch other than `lastWithdrawRequest[_user]`, and `_clearWithdrawClaimForEpoch` only clears the keyed epoch, leaving the stale haircutted basis inside the funded aggregate.

One caveat I could not fully verify within the tool budget: whether `_transferFundedClaim` enforces a per-claim funded reserve rather than transferring from the strategy's raw underlying balance. If it strictly bounded payouts to per-epoch collected amounts the overpayment would revert instead of draining other claimants' funds — but the code comments (`"pendingWithdraws was already cleared when the borrower funded the loss-adjusted amount"`, `IdleCreditVault.sol:797`) and the aggregate-basis payout in `_claimFundedWithdrawRequest` indicate the funded path has no such per-epoch accounting, so the drain proceeds against the shared balance.

### Recommendation
Do not route claims through `lastWithdrawRequest`. In `_claimLossAdjustedWithdrawRequest` (or `claimWithdrawRequest`), iterate or explicitly check every epoch with a nonzero `lossRecoveryPriceByEpoch` for which `withdrawsRequestsByEpoch[_user][epoch] != 0` (or `apr0Users[_user].principalEpoch == epoch`), clearing those receipts at the haircut price before the funded path runs. Equivalently, subtract each epoch's haircutted basis from `withdrawsRequests[_user]` when the loss is recorded, or store a per-user set/bitmask of pending receipt epochs so no single "latest epoch" marker can mislabel which receipts are loss-adjusted.

### Proof of Concept
Foundry fork sketch (extend `test/foundry/IdleCreditVault.t.sol` helpers):

```solidity
function testLossReceiptPaidAtParAfterNewRequest() external {
    // attacker deposits AA during buffer, epoch N starts
    uint256 amount = 100_000 * ONE_SCALE;
    idleCDO.depositAA(amount);
    _transferBurnedTrancheTokens(address(this), true);
    _startEpochAndCheckPrices(0);

    // attacker requests withdraw in epoch N
    cdoEpoch.requestWithdraw(IERC20(AAtranche).balanceOf(address(this)) / 2, address(AAtranche));

    // stop epoch N with a 50% loss on pending receipts:
    // borrower funds only half of pendingWithdraws
    vm.warp(cdoEpoch.epochEndDate() + 1);
    uint256 pending = IdleCreditVault(address(strategy)).pendingWithdraws();
    deal(defaultUnderlying, borrower, pending / 2 + amount);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0); // or stopEpochWithDuration path that underfunds
    assertEq(strategy.lossRecoveryPriceByEpoch(strategy.epochNumber() - 1) > 0, true);

    // epoch N+1: attacker files a dust-sized second request
    _startEpochAndCheckPrices(1);
    cdoEpoch.requestWithdraw(1, address(AAtranche));

    // fund it fully at the next stop
    _startEpochAndCheckPrices(2);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    deal(defaultUnderlying, borrower, strategy.pendingWithdraws());
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    // claim: the epoch-N haircutted basis is paid at par
    uint256 balPre = IERC20Detailed(defaultUnderlying).balanceOf(address(this));
    cdoEpoch.claimWithdrawRequest(); // routed via IdleCDOEpochVariant
    uint256 paid = IERC20Detailed(defaultUnderlying).balanceOf(address(this)) - balPre;
    // paid == full basis, but strategy only collected ~basis/2 for epoch N
    // => drains other users' funded receipts or leaves last claimer reverting
}
```

Key assertion: after `collectWithdrawFunds` underfunding, `lossRecoveryPriceByEpoch[N]` is set, yet the attacker's claim succeeds for the full `withdrawsRequests` aggregate once `lastWithdrawRequest` points at epoch N+1.