### Title
`lastWithdrawRequest` single-slot overwrite orphans a loss-adjusted receipt, letting the same claimBasis be paid twice — once haircut-funded, once at par (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault` keeps only one epoch pointer per user, `lastWithdrawRequest[_user]`, and uses it for two different purposes: (a) gating the funded-claim path (`epochNumber <= lastWithdrawRequest → revert`) and (b) as the lookup key into `lossRecoveryPriceByEpoch` in `_claimLossAdjustedWithdrawRequest`. When a `stopEpochWithDuration`-style loss haircut is applied to a pending receipt via `collectWithdrawFunds` (borrower funds only `pendingToFund < pendingBasis`, remainder recorded as `lossRecoveryPriceByEpoch[epoch]`), the user's receipt amount is **not** cleared from `withdrawsRequests`/`withdrawsRequestsByEpoch` — it is only cleared later when the user claims through `_claimLossAdjustedWithdrawRequest`. If the user makes a second `requestWithdraw` before claiming, `lastWithdrawRequest` is overwritten to the new epoch, the loss-adjusted claim for the old epoch becomes permanently unreachable (the per-epoch accounting under the old epoch is never visited again), and the old basis remains in the aggregate `withdrawsRequests[_user]`. Once one more epoch passes, `_claimFundedWithdrawRequest` pays the aggregate **at par**, even though the old receipt was only ever funded at `lossRecoveryPrice` — an exact use-after-free analog: a dangling reference to an epoch record that was logically "freed" (haircut-settled at `collectWithdrawFunds`) but whose state is reused.

### Finding Description
Bug class mapping: CVE-2024-0225 is a use-after-free — code keeps a pointer to an object already freed. The vault analog is `lastWithdrawRequest` aliasing two roles and pointing at an already-settled epoch record.

Trace:
1. Buffer phase, APR ≠ 0, normal mode. Alice deposits AA and calls `cdoEpoch.requestWithdraw(...)`. `IdleCreditVault.requestWithdraw` sets `withdrawsRequests[alice] += A`, `withdrawsRequestsByEpoch[alice][E] += A`, `pendingWithdraws += A`, `lastWithdrawRequest[alice] = E` (lines 279–293).
2. `stopEpoch` realizes a loss: `previewLossAdjustedWithdrawFunds` splits the loss; `collectWithdrawFunds` receives only `A * price` from the CDO, sets `pendingWithdraws = 0` and `lossRecoveryPriceByEpoch[E'] = price` (lines 411–429). Alice's `withdrawsRequestsByEpoch[alice][E] = A` is left in place — intended to be cleared on claim.
3. Alice calls `requestWithdraw` again for a second tranche `B` in epoch `F` (`F` after the loss epoch). `lastWithdrawRequest[alice]` is overwritten to `F` (line 282). `withdrawsRequests[alice] = A + B`.
4. Epoch `F` ends successfully (`epochNumber = F + 1`). Alice calls `cdoEpoch.claimWithdrawRequest()` → `claimWithdrawRequest`:
   - `_claimLossAdjustedWithdrawRequest` reads `lossEpoch = lastWithdrawRequest = F`; `lossRecoveryPriceByEpoch[F] == 0` → early return (lines 790–792). The epoch-`E` loss-adjusted receipt can never be reached again.
   - `_claimFundedWithdrawRequest`: `epochNumber (F+1) > lastWithdrawRequest (F)` → gate passes; pays `withdrawsRequests[alice] = A + B` **at par** (lines 326–349).

The strategy only ever received `A * price + B` (assuming `B` fully funded) but pays out `A + B`. The deficit `A * (1 − price)` is taken from whatever underlyings sit in the strategy — other users' funded receipts or reserve — or, if the strategy holds less, the transfer fails and Alice's claim (and others') is bricked.

This survives all guards: `_transferFundedClaim`'s reserve check only protects `defaultRecoveryReserve`, not other users' funded receipts; `NotAllowed` gates are all satisfied; no `Default` state is required.

### Impact Explanation
Direct theft / insolvency: after a realized epoch loss, a user who re-requests before claiming converts a haircut-funded receipt into a par claim. Loss = `unclaimed loss-adjusted basis × (1 − lossRecoveryPrice)`, drawn from other claimants' funded underlyings or from the default recovery reserve boundary. In the degenerate case (strategy underfunded) it permanently freezes the funded-claim path for everyone behind it.

### Likelihood Explanation
Requires a loss epoch (`stopEpochWithDuration` partial funding path, i.e. borrower repays less than `pendingWithdraws` without full default — a normal credit-risk event for this vault), and a user who re-requests a withdrawal before claiming. Re-requesting is explicitly supported ("if a user does not claim a withdraw request and instead requests another withdraw, he will have to wait for another epoch to claim both requests" — comment at lines 323–324), and rational users routinely roll requests. Attacker is an ordinary KYC'd tranche holder — fully within the unprivileged model.

### Recommendation
Decouple the epoch pointer from the funded-claim gate:
- Store a per-user queue/set of pending claim epochs (or iterate `withdrawsRequestsByEpoch`/`lossRecoveryPriceByEpoch` by a stored oldest epoch), so a second `requestWithdraw` cannot orphan an unsettled loss-adjusted receipt.
- At minimum, in `_claimLossAdjustedWithdrawRequest`, scan/clear the loss-adjusted epoch independently of `lastWithdrawRequest` (e.g. keep `lastLossEpoch[user]` separate), and in `requestWithdraw` either force-claim/settle the prior receipt or reject re-requests while `lossRecoveryPriceByEpoch[lastWithdrawRequest[user]] != 0`.

### Proof of Concept
Foundry fork test sketch (extend `test/foundry/IdleCreditVault.t.sol`):

```solidity
function testLossReceiptOrphanedByReRequest() external {
    uint256 amount = 10_000 * ONE_SCALE;
    address alice = makeAddr("alice");
    _depositWithUser(alice, 2 * amount, true);

    // Epoch E: request A, then stop with partial loss (borrower funds < pendingWithdraws)
    vm.prank(alice);
    uint256 A = cdoEpoch.requestWithdraw(amount / 2, address(AAtranche));
    _startEpochAndCheckPrices(0);
    // borrower repays pending basis minus a loss share, e.g. 50%
    uint256 funded = A * 5e17 / ONE_TRANCHE;
    deal(defaultUnderlying, borrower, _expectedFundsEndEpoch() - (A - funded));
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(initialProvidedApr, A - funded); // loss path -> collectWithdrawFunds shortfall
    assertGt(IdleCreditVault(address(strategy)).lossRecoveryPriceByEpoch(strategy.epochNumber()), 0);

    // Epoch F: alice requests again before claiming -> lastWithdrawRequest overwritten
    _startEpochAndCheckPrices(1);
    vm.prank(alice);
    uint256 B = cdoEpoch.requestWithdraw(amount / 4, address(AAtranche));
    deal(defaultUnderlying, borrower, _expectedFundsEndEpoch());
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(initialProvidedApr, 0); // fully funded epoch

    uint256 strategyBal = underlying.balanceOf(address(strategy));
    uint256 balPre = underlying.balanceOf(alice);
    vm.prank(alice);
    cdoEpoch.claimWithdrawRequest();
    // BUG: pays A + B at par although A was only funded at lossRecoveryPrice
    assertGt(underlying.balanceOf(alice) - balPre, funded + B);
}
```

Caveat: I could not fully read `IdleCDOEpochVariant.stopEpoch` ordering (whether `epochNumber` is incremented before `collectWithdrawFunds` stores the price). If the price is keyed under the post-increment epoch while `lastWithdrawRequest` holds the request epoch, the loss-adjusted path is unreachable even *without* a re-request, which makes the same bug strictly worse. Either ordering produces a valid broken one-receipt-one-payout invariant.