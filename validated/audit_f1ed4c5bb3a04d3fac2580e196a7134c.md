### Title
Loss-adjusted withdraw receipts escape their haircut when `lastWithdrawRequest` is overwritten by a later request - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`IdleCreditVault` records a haircut for partially funded withdraw receipts in `lossRecoveryPriceByEpoch[epochNumber]`, but `_claimLossAdjustedWithdrawRequest` only ever looks up `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]`. `requestWithdraw` unconditionally overwrites `lastWithdrawRequest[_user]` with the current epoch. A tranche holder who holds a receipt in a loss epoch can make a second `requestWithdraw` in a later, fully funded epoch; the later epoch has no entry in `lossRecoveryPriceByEpoch`, so the loss-adjusted claim path returns 0 and the stale loss-epoch basis falls through to `_claimFundedWithdrawRequest`, which pays the full `withdrawsRequests[_user]` aggregate at par. This is the vault analog of the Zip Slip report: an attacker-controlled "entry" (the request epoch key) is joined to the payout path without validating that the epoch's receipts stayed inside their haircut bucket, letting a receipt written for a loss directory land in the sibling full-payout directory.

### Finding Description
Relevant code:

- `requestWithdraw` writes the receipt into `withdrawsRequests[_user]`, `withdrawsRequestsByEpoch[_user][currentEpoch]`, and always sets `lastWithdrawRequest[_user] = currentEpoch` (lines 282-293). The NatSpec even states re-requesting before claiming is supported: "if a user does not claim a withdraw request and instead requests another withdraw, he will have to wait for another epoch to claim both requests."
- `collectWithdrawFunds` on a `stopEpochWithDuration` loss stores `lossRecoveryPriceByEpoch[epochNumber] = _amount * RECOVERY_FULL / pendingBasis` and zeroes `pendingWithdraws`, but leaves each user's `withdrawsRequests` / `withdrawsRequestsByEpoch` basis untouched — the haircut is only applied lazily at claim time (lines 411-429).
- `_claimLossAdjustedWithdrawRequest` derives the claim epoch solely from `lastWithdrawRequest[_user]` (lines 789-801). There is no iteration over `withdrawsRequestsByEpoch` and no check that a previous epoch's receipt carried a nonzero `lossRecoveryPriceByEpoch`.
- `_claimFundedWithdrawRequest` then pays `withdrawsRequests[_user]` (which still contains the loss-epoch `normalAmount`) plus APR0 buckets at 1:1 via `_transferFundedClaim` (lines 319-350). `_transferFundedClaim` only ring-fences `defaultRecoveryReserve` (lines 897-907); it does not protect underlyings that belong to other users' funded receipts.

Broken invariant: loss waterfall / "one receipt one (correctly priced) payout". A receipt whose epoch was funded at `lossRecoveryPrice < RECOVERY_FULL` is paid at `RECOVERY_FULL`.

### Impact Explanation
Direct theft / insolvency. The attacker claims `lossBasis * (RECOVERY_FULL - lossRecoveryPrice) / RECOVERY_FULL` more than entitled. Since `collectWithdrawFunds` only pulled `pendingBasis * lossRecoveryPrice / RECOVERY_FULL` for that epoch, the excess is paid out of underlyings held for other users' funded receipts (or, post-default, out of everything not covered by `defaultRecoveryReserve`). Quantified example: user has a 100k receipt in an epoch funded at 50%, requests again next epoch, then claims — receives 150k + second receipt instead of 50k + second receipt, i.e. steals the 50k haircut. The same hole applies whenever a user holds receipts in more than one loss epoch, since only the epoch equal to `lastWithdrawRequest` is ever haircutted.

### Likelihood Explanation
No privileged misbehavior needed: the loss epoch arises from an honest `stopEpochWithDuration`/`collectWithdrawFunds` shortfall (borrower underfunding is a normal protocol path), and the attacker is an ordinary KYC-passed tranche holder who simply calls `requestWithdraw` twice across epochs and `claimWithdrawRequest` once. The only precondition is holding tranche tokens in an epoch that ends with `_amount < pendingBasis`, which the protocol explicitly supports.

### Recommendation
In `_claimLossAdjustedWithdrawRequest` (or `claimWithdrawRequest`), iterate or track all epochs with nonzero `withdrawsRequestsByEpoch[_user][*]` that have a `lossRecoveryPriceByEpoch` entry, rather than keying solely off `lastWithdrawRequest[_user]`; alternatively, at loss-collection time migrate loss-epoch basis out of `withdrawsRequests` into a dedicated loss-claim mapping, or block new `requestWithdraw` while the user's `lastWithdrawRequest` epoch still has an unclaimed nonzero `lossRecoveryPriceByEpoch`.

### Proof of Concept
```solidity
// test/foundry/IdleCreditVault.t.sol style PoC (sketch)
function testLossReceiptEscapesHaircutViaLaterRequest() external {
    uint256 amount = 100_000 * ONE_SCALE;
    address attacker = makeAddr('attacker');
    address victim   = makeAddr('victim');

    // attacker and victim deposit AA
    _depositWithUser(attacker, amount, true);
    _depositWithUser(victim,   amount, true);

    // attacker requests withdraw in epoch 0 -> receipt R1
    vm.prank(attacker);
    uint256 r1 = cdoEpoch.requestWithdraw(0, address(AAtranche));

    _startEpochAndCheckPrices(0);
    // borrower underfunds stopEpoch: only 50% of pendingWithdraws collected
    // -> collectWithdrawFunds stores lossRecoveryPriceByEpoch[0] = 0.5e18
    _stopEpochWithLoss(0, /* loss such that pending funded at 50% */);

    // epoch 1: attacker requests again -> lastWithdrawRequest[attacker] = 1
    vm.prank(attacker);
    uint256 r2 = cdoEpoch.requestWithdraw(0, address(AAtranche));
    _startEpochAndCheckPrices(1);
    // epoch 1 fully funded -> lossRecoveryPriceByEpoch[1] == 0
    _stopEpochAndCheckPrices(1, initialProvidedApr, _expectedFundsEndEpoch());

    // claim: _claimLossAdjustedWithdrawRequest looks up epoch 1 -> price 0 -> skipped
    // _claimFundedWithdrawRequest pays withdrawsRequests (r1 + r2) at PAR
    uint256 balPre = underlying.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimWithdrawRequest();

    // BUG: attacker receives r1 + r2 instead of r1/2 + r2
    assertEq(underlying.balanceOf(attacker) - balPre, r1 + r2,
        'loss-epoch receipt paid at par instead of haircut');
}
```
Key lines: `requestWithdraw` overwrites `lastWithdrawRequest` (IdleCreditVault.sol:282), `_claimLossAdjustedWithdrawRequest` reads only `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` (lines 790-792), and `_claimFundedWithdrawRequest` pays the entire `withdrawsRequests` aggregate (lines 338-349).