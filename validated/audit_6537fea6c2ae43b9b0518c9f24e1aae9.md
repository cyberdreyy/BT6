### Title
Loss-adjusted (haircutted) withdraw receipts from an earlier epoch can be claimed at par, stealing other claimants' funded recovery — ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`stopEpochWithDuration`-style losses store a single haircut under `lossRecoveryPriceByEpoch[epochNumber]`, but `claimWithdrawRequest` only applies that haircut to the receipt stored under `lastWithdrawRequest[_user]`. A user with pending receipts in two different epochs gets the older epoch's receipt paid at par via `_claimFundedWithdrawRequest`, even though the borrower only funded the haircutted aggregate. The deficit `(1 - lossRecoveryPrice) × olderEpochBasis` is drained from funds reserved for other users' receipts — a traversal-style escape: the claim path "walks out" of the epoch boundary that the loss was recorded under.

### Finding Description
The directory-traversal analog here is escaping the epoch "root" that bounds a haircut: the loss price is recorded per epoch, but the claim path aggregates across epochs and only haircuts the latest one.

- `requestWithdraw` records receipts both in the aggregate `withdrawsRequests[_user]` and per-epoch `withdrawsRequestsByEpoch[_user][currentEpoch]`, and adds the full `_amount` to `pendingWithdraws` (`IdleCreditVault.sol:279,292-293`). Per the NOTE at lines 323-324, a user may hold unclaimed receipts spanning multiple epochs; each new request just overwrites `lastWithdrawRequest[_user]`.
- On a lossy stop, `collectWithdrawFunds` computes `lossRecoveryPrice = _amount * 1e18 / pendingBasis` over the **entire aggregate** `pendingWithdraws` (all epochs' receipts), clears `pendingWithdraws` to 0, and stores the price only under `lossRecoveryPriceByEpoch[epochNumber]` (`IdleCreditVault.sol:413-421`).
- `claimWithdrawRequest` first calls `_claimLossAdjustedWithdrawRequest`, which uses `lossEpoch = lastWithdrawRequest[_user]` and clears **only** `withdrawsRequestsByEpoch[_user][lossEpoch]` inside `_clearWithdrawClaimForEpoch` (`IdleCreditVault.sol:789-800, 815-819`). The older epoch's `withdrawsRequestsByEpoch` entry and its share of `withdrawsRequests[_user]` remain.
- `_claimFundedWithdrawRequest` then pays `withdrawsRequests[_user]` (the leftover older-epoch basis) **at par** (`IdleCreditVault.sol:338-349`), with no check that any of it was inside a haircutted pending bucket.
- The guard in `requestWithdraw` (`IdleCreditVault.sol:263-271`) only blocks *new* requests when `lossRecoveryPriceByEpoch[lastWithdrawRequest]` is set; it does not prevent the pre-existing two-epoch state, and the funded-claim path never re-checks other epochs for a nonzero loss price.

Broken invariant: one receipt one payout at the correct recovery price. Total payouts become `lossRecoveryPrice × latestBasis + olderBasis` while only `lossRecoveryPrice × (latestBasis + olderBasis)` was funded.

### Impact Explanation
Direct insolvency/theft: the attacker extracts `(1 - lossRecoveryPrice) × olderEpochBasis` more underlying than was funded, paid out of the strategy's `underlyingToken` balance that is reserved for other users' funded withdraw/instant-withdraw receipts (and post-default `defaultRecoveryReserve` accounting assumes every pending receipt was haircutted). With, e.g., a 50% loss price and a 100k older receipt, the attacker over-withdraws 50k underlying, leaving later claimants' transactions to revert or pay less — permanent freezing/theft of funded claims. Quantified loss: `(RECOVERY_FULL - lossRecoveryPriceByEpoch[stopEpoch]) * withdrawsRequestsByEpoch[user][earlierEpoch] / 1e18`.

### Likelihood Explanation
Likelihood is moderate. The attacker is an ordinary KYC'd lender who must (1) request a withdraw in epoch N's buffer, (2) refrain from claiming and request again in epoch N+1's buffer (explicitly supported behavior), and (3) have a lossy `stopEpochWithDuration`/`collectWithdrawFunds(_amount < pendingBasis)` occur while both receipts sit in `pendingWithdraws`. Lossy stops are manager-driven but are a designed mode (`previewLossAdjustedWithdrawFunds`, `stopEpochWithDuration` exist precisely for partial borrower repayment); the attacker needs no privileged role and profits proportionally to their earlier-epoch receipt size whenever such a stop occurs. No existing guard (skim, default gating, epoch gating, `onlyIdleCDO`) covers the cross-epoch aggregate claim.

### Recommendation
Apply the stored loss price to **all** of a user's receipt epochs covered by the haircutted `pendingBasis`, not only `lastWithdrawRequest`. Concretely: in `_claimLossAdjustedWithdrawRequest`/`_clearWithdrawClaimForEpoch`, when `lossRecoveryPriceByEpoch[lossEpoch] != 0`, zero the user's entire `withdrawsRequests[_user]` (and every contributing `withdrawsRequestsByEpoch` entry / APR0 principal) and pay `totalBasis * lossRecoveryPrice / 1e18`; or iterate the user's per-epoch entries. Symmetrically, `requestWithdraw` should revert if the user holds any receipt whose epoch maps to a nonzero `lossRecoveryPriceByEpoch`, not just the latest one. Add a regression test: two-epoch receipts + lossy stop → claim pays `price × totalBasis`, not par on the older piece.

### Proof of Concept
Foundry fork PoC (sketch, modeled on `test/foundry/IdleCreditVault.t.sol` helpers `_depositWithUser`, `_startEpochAndCheckPrices`, `_stopEpochAndCheckPrices`):

```solidity
function testCrossEpochLossEscape() external {
    _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, cdoEpoch.managementFee());
    uint256 amount = 100_000 * ONE_SCALE;
    address alice = makeAddr('alice'); // attacker
    address bob   = makeAddr('bob');   // honest claimant, same pending bucket
    _depositWithUser(alice, amount, true);
    _depositWithUser(bob,   amount, true);

    // Epoch 0 buffer: both request withdraws (receipts recorded under epoch 0)
    vm.prank(alice); uint256 aReq1 = cdoEpoch.requestWithdraw(0, address(AAtranche));
    vm.prank(bob);   cdoEpoch.requestWithdraw(0, address(AAtranche));

    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr, _expectedFundsEndEpoch());
    // epochNumber = 1; both receipts funded but unclaimed

    // Epoch 1 buffer: alice requests again instead of claiming (supported per NOTE)
    vm.prank(alice); uint256 aReq2 = cdoEpoch.requestWithdraw(0, address(AAtranche));
    // pendingWithdraws now = aReq1 + aReq2 + bobReq across TWO epochs of alice

    _startEpochAndCheckPrices(1);

    // Lossy stop: borrower repays only 50% of pendingBasis
    IdleCreditVault strat = IdleCreditVault(address(strategy));
    uint256 pending = strat.pendingWithdraws();
    uint256 funded = pending / 2;
    deal(defaultUnderlying, borrower, _expectedFundsEndEpoch());
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(... /* path that calls collectWithdrawFunds(funded) */);
    assertEq(strat.lossRecoveryPriceByEpoch(strat.epochNumber()), 5e17);

    // Alice claims: haircut applied ONLY to aReq2 (lastWithdrawRequest epoch);
    // aReq1 is paid at par via _claimFundedWithdrawRequest.
    uint256 balPre = underlying.balanceOf(alice);
    vm.prank(alice);
    cdoEpoch.claimWithdrawRequest();
    uint256 got = underlying.balanceOf(alice) - balPre;

    assertEq(got, aReq1 + aReq2 / 2);           // over-payout: aReq1 should also be /2
    // Strategy only received funded = (aReq1 + aReq2 + bobReq)/2;
    // bob's later claim reverts or is short by aReq1/2 -> insolvency.
    vm.prank(bob);
    vm.expectRevert(); // underfunded: stolen by alice
    cdoEpoch.claimWithdrawRequest();
}
```

Fix validation: after the change, `got == (aReq1 + aReq2) / 2` and Bob's claim of `bobReq / 2` succeeds.