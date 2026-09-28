### Title
Stale-epoch APR0 withdraw receipts escape the default haircut and are paid at par, draining non-reserve strategy funds - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The external report's bug class is a parser misclassifying a malformed object (type confusion over an index/record, leading to out-of-bounds access). The analog in `IdleCreditVault` is the same shape: a per-epoch withdraw receipt is "parsed" against `defaultRecoveryEpoch`, and an APR0 receipt whose `principalEpoch` predates the default epoch is misclassified — it is silently excluded from the defaulted-claim clearing while still being counted in the recovery basis, then paid out at par through the funded-claim path.

### Finding Description
`defaultPendingClaimBasis()` includes all of `pendingWithdraws`, which contains every open APR0 principal (added in `requestWithdraw` at line 279 regardless of the APR0 branch). So `finalizeDefaultRecovery` prices `defaultRecoveryPrice` and sizes `defaultRecoveryReserve` assuming those receipts take the haircut.

However `_claimDefaultedWithdrawRequest` → `_clearWithdrawClaimForEpoch` → `_withdrawClaimAmountsForEpoch` only includes APR0 principal when `apr0Users[_user].principalEpoch == defaultRecoveryEpoch` (lines 871-877). Any earlier-epoch, never-claimed APR0 receipt (`principalEpoch < defaultRecoveryEpoch`, which is normal — settlement only happens lazily on claim/re-request) contributes `0` to the defaulted claimBasis, is not cleared, and `lastWithdrawRequest[_user]` is reset to 0 (line 835).

The claim then falls through to `_claimFundedWithdrawRequest`, which calls `_settleApr0(_user)`; since `reqEpoch < epochNumber` after the default-epoch stop, the stale principal moves to `settledPrincipal` and is paid **at par** via `_transferFundedClaim` (lines 330-349). `_transferFundedClaim` only guards `balance - reserve >= amount`, so the payout is drawn from strategy underlyings that are not part of the recovery reserve — e.g. fresh deposits awaiting `sendInterestAndDeposits`, or any borrower-returned funds. Net effect: the attacker both diluted the recovery price (their basis was counted) and withdrew 100% instead of `defaultRecoveryPrice`.

### Impact Explanation
Direct theft/insolvency: the attacker recovers `principal` instead of `principal * defaultRecoveryPrice / 1e18`, extracting `(1 - recoveryPrice) * principal` of value belonging to honest depositors/borrower-directed funds held by the strategy. If the strategy holds only the reserve, the same misclassification still inflates `totalBasis` at finalization (lowering everyone's recovery price) while `pendingWithdraws` retains a basis that is never claimable through the defaulted path — value is permanently stranded in the reserve.

### Likelihood Explanation
Attacker is an ordinary KYC'd lender. Preconditions: APR set to 0 (a manager-configurable, legitimate mode with dedicated tests), attacker requests a withdraw in epoch N-1, does not claim, epoch N stops, borrower defaults during epoch N, `finalizeDefault` runs. No privileged misbehavior required; the lazy `_settleApr0` design makes un-claimed earlier-epoch APR0 receipts the common case.

### Recommendation
In `_claimDefaultedWithdrawRequest`/`_withdrawClaimAmountsForEpoch`, include and clear stale open APR0 principal (`apr0User.principal` with `principalEpoch < defaultRecoveryEpoch`) plus any `settledPrincipal`/`settledInterest` in the defaulted claimBasis at `defaultRecoveryPrice`, or explicitly route them to the reserve-priced path; alternatively settle all APR0 receipts inside `finalizeDefaultRecovery` so basis and claims stay consistent.

### Proof of Concept
Foundry fork sketch (extend `test/foundry/IdleCreditVault.t.sol` helpers):

```solidity
function testPocStaleApr0EscapesDefaultHaircut() external {
  _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, cdoEpoch.managementFee());
  vm.prank(owner); cdoEpoch.setIsAYSActive(false);
  vm.prank(manager); IdleCreditVault(address(strategy)).setAprs(0, 0);

  address victim = makeAddr('victim');
  idleCDO.depositAA(10000 * ONE_SCALE);                 // attacker (this contract)
  _depositWithUser(victim, 10000 * ONE_SCALE, true);    // honest LP

  // epoch 0 runs and stops; attacker requests APR0 withdraw during buffer
  _startEpochAndCheckPrices(0);
  _stopEpochAndCheckPrices(0, 0, _expectedFundsEndEpoch());
  uint256 req = cdoEpoch.requestWithdraw(
      IERC20(AAtranche).balanceOf(address(this)) / 2, address(AAtranche));

  // epoch 1 runs (receipt becomes claimable), attacker simply does not claim
  _startEpochAndCheckPrices(1);
  // borrower defaults mid-epoch; partial recovery
  _checkDefault();
  uint256 recovered = /* partial recovery amount */;
  deal(defaultUnderlying, manager, recovered);
  vm.startPrank(manager);
  IERC20Detailed(defaultUnderlying).approve(address(strategy), recovered);
  cdoEpoch.finalizeDefault(recovered, manager);
  vm.stopPrank();

  uint256 balPre = IERC20Detailed(defaultUnderlying).balanceOf(address(this));
  cdoEpoch.claimWithdrawRequest();
  // attacker's stale APR0 receipt is paid at PAR, not at defaultRecoveryPrice
  assertEq(IERC20Detailed(defaultUnderlying).balanceOf(address(this)) - balPre, req);
}
```

The key assertion is that the claim returns `req` (par) rather than `req * defaultRecoveryPrice / 1e18`, with the delta pulled from non-reserve strategy balance while other claimants are priced on a basis that already included this receipt.