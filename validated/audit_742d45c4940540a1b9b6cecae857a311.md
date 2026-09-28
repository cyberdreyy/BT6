### Title
Loss-adjusted withdraw receipts are paid at par when a later request overwrites `lastWithdrawRequest` - (contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault._claimLossAdjustedWithdrawRequest` looks up the loss haircut only for `lastWithdrawRequest[_user]`, the user's *latest* request epoch. If a user holds an unclaimed receipt from a `stopEpochWithDuration` loss epoch and then files a new `requestWithdraw` in a later, fully funded epoch, the loss-adjusted epoch is skipped entirely and `_claimFundedWithdrawRequest` pays the aggregate `withdrawsRequests[_user]` at par. The contract only collected the haircut amount from the borrower for the loss epoch, so the overpayment drains funds belonging to other receipt holders.

### Finding Description
In `requestWithdraw`, each request updates `lastWithdrawRequest[_user] = currentEpoch` and accumulates `withdrawsRequests[_user]` plus per-epoch `withdrawsRequestsByEpoch[_user][currentEpoch]` [1](#0-0) .

When `stopEpochWithDuration(_lossAmount)` realizes a loss, `collectWithdrawFunds` stores `lossRecoveryPriceByEpoch[epochNumber]` and clears `pendingWithdraws`, but pulls only the haircut `_amount` of underlying from the CDO [2](#0-1) .

On claim, `_claimLossAdjustedWithdrawRequest` reads `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` [3](#0-2) . If the user requested again in a later non-loss epoch, `lastWithdrawRequest` points at that epoch, `lossRecoveryPrice` is 0, and the function returns 0 without clearing the loss-epoch receipt. `_claimFundedWithdrawRequest` then pays `withdrawsRequests[_user]` — which still includes the loss-epoch basis — in full [4](#0-3) . `_clearWithdrawClaimForEpoch` only ever clears one epoch's bucket, so the stale per-epoch entry is never haircut.

### Impact Explanation
Direct theft / insolvency. A tranche holder who requested a withdraw in loss epoch N (funded at `lossRecoveryPrice < 1e18`) can wait for epoch N+1 to be funded at par, file a dust-size `requestWithdraw` in epoch N+1, then after epoch N+2 starts call `claimWithdrawRequest` and receive `claimBasis_N + claimBasis_{N+1}` at par instead of `claimBasis_N * lossRecoveryPrice + claimBasis_{N+1}`. The excess `(claimBasis_N * (1 - lossRecoveryPrice))` is paid out of underlying reserved for other users' funded receipts or the default recovery reserve, leaving later claimers underfunded.

### Likelihood Explanation
Requires only an unprivileged tranche-token holder: (1) an epoch closed via `stopEpochWithDuration` with `0 < lossRecoveryPrice < 1e18`, (2) the attacker had a pending receipt in that epoch and does not claim, (3) a subsequent epoch funds normally. No privileged actor misbehavior is needed; manager/owner calls stay honest. The guard in `_claimFundedWithdrawRequest` (wait one epoch after `lastWithdrawRequest`) does not prevent the stale loss-epoch basis from being aggregated.

### Recommendation
In `_claimLossAdjustedWithdrawRequest`, iterate or check all epochs with nonzero `lossRecoveryPriceByEpoch` where `withdrawsRequestsByEpoch[_user][epoch] != 0` (or track a per-user set of loss epochs), rather than relying solely on `lastWithdrawRequest`. Alternatively, in `requestWithdraw`, force-settle any pending loss-adjusted receipt for the user before creating the new request, so the haircut is always applied at request time.

### Proof of Concept
Foundry fork PoC sketch (pattern follows `test/foundry/IdleCreditVault.t.sol` loss-adjusted tests):

```solidity
function testLossEpochReceiptPaidAtPar() external {
    address attacker = makeAddr('attacker');
    _depositWithUser(attacker, 10_000 * ONE_SCALE, true);

    // Epoch N: attacker requests full withdraw
    vm.prank(attacker);
    uint256 claimBasis = cdoEpoch.requestWithdraw(0, address(AAtranche));

    _startEpochAndCheckPrices(0);
    // stop with a loss: borrower funds only haircut amount
    uint256 pendingToFund = /* previewLossAdjustedWithdrawFunds result */;
    _stopEpochWithDurationLoss(/* lossAmount */); // sets lossRecoveryPriceByEpoch[N] < 1e18

    // Epoch N+1 runs and funds normally; attacker files a tiny new request
    _startEpochAndCheckPrices(1);
    _stopEpochAndCheckPrices(1, initialProvidedApr, _expectedFundsEndEpoch());
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(1, address(AAtranche)); // dust request bumps lastWithdrawRequest

    // Epoch N+2 starts so the funded-claim gate passes
    _startEpochAndCheckPrices(2);

    uint256 balPre = underlying.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimWithdrawRequest();
    // Expected (buggy): receives claimBasis + 1 at par.
    // Correct: claimBasis * lossRecoveryPriceByEpoch[N] / 1e18 + 1.
    assertGt(underlying.balanceOf(attacker) - balPre,
             claimBasis * creditVault.lossRecoveryPriceByEpoch(N) / 1e18 + 1);
}
```

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L282-293)
```text
    lastWithdrawRequest[_user] = currentEpoch;
    // APR=0 requests keep separate accounting and settle interest at stopEpoch.
    // `_amount` here is the post-management-fee principal bucket for that flow.
    if (unscaledApr == 0 && !isClosed) {
      _requestWithdrawApr0(_amount, _user);
    } else {
      // increase the withdraw requests for the user
      // we record both per-user (old, kept for compatibility) and per-epoch so
      // on finalization we can distinguish "default-epoch pending receipts"
      // from old funded receipts.
      withdrawsRequests[_user] += _amount;
      withdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L326-349)
```text
    if (IIdleCDOEpochVariant(idleCDO).epochEndDate() != 0 && (epochNumber <= lastWithdrawRequest[_user])) {
      revert NotAllowed();
    }
    // settle APR=0 requests once the related epoch has ended
    _settleApr0(_user);
    Apr0UserData storage _apr0User = apr0Users[_user];
    // Claim includes:
    // - settled APR0 principal from finalized epochs
    // - still-open APR0 principal: if pool-close mode was used (_interest == 1), IdleCDO sets
    //   epochEndDate = 0 and claims can be immediate, while _settleApr0 can still skip settlement
    //   for the current request epoch (reqEpoch >= epochNumber).
    // - settled APR0 interest
    uint256 normalAmount = withdrawsRequests[_user];
    uint256 apr0PrincipalAmount = _apr0User.settledPrincipal + _apr0User.principal;
    uint256 apr0InterestAmount = _apr0User.settledInterest;
    amount = normalAmount + apr0PrincipalAmount + apr0InterestAmount;
    // burn strategy tokens 1:1 with the principal only (normal amount already includes interest)
    _burn(_user, normalAmount + apr0PrincipalAmount);
    withdrawsRequests[_user] = 0;
    lastWithdrawRequest[_user] = 0;
    if (apr0PrincipalAmount != 0 || apr0InterestAmount != 0) {
      delete apr0Users[_user];
    }
    _transferFundedClaim(_user, amount);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L411-429)
```text
  function collectWithdrawFunds(uint256 _amount) external {
    _onlyIdleCDO();
    uint256 pendingBasis = pendingWithdraws;
    if (_amount < pendingBasis) {
      // Legacy receipts do not have per-epoch ownership data, so they can only be fully funded.
      if (!defaultRecoveryInitialized) revert NotAllowed();
      uint256 lossRecoveryPrice = _amount * RECOVERY_FULL / pendingBasis;
      // Avoid storing a zero price, which is indistinguishable from "no loss-adjusted epoch".
      if (lossRecoveryPrice == 0) revert NotAllowed();
      pendingWithdraws = 0;
      lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice;
    } else {
      // A plain implementation upgrade may leave legacy normal receipts pending. Their next
      // successful stop can fully fund the aggregate before lazy initialization occurs.
      pendingWithdraws = pendingBasis - _amount;
    }
    if (_amount != 0) {
      underlyingToken.safeTransferFrom(idleCDO, address(this), _amount);
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L789-800)
```text
  function _claimLossAdjustedWithdrawRequest(address _user) internal returns (uint256 amount) {
    uint256 lossEpoch = lastWithdrawRequest[_user];
    uint256 lossRecoveryPrice = lossRecoveryPriceByEpoch[lossEpoch];
    if (lossRecoveryPrice == 0) return amount;

    (uint256 claimBasis, uint256 burnAmount) = _clearWithdrawClaimForEpoch(_user, lossEpoch, false);
    if (claimBasis == 0) return amount;

    // pendingWithdraws was already cleared when the borrower funded the loss-adjusted amount.
    _burn(_user, burnAmount);
    amount = (claimBasis * lossRecoveryPrice) / RECOVERY_FULL;
    _transferFundedClaim(_user, amount);
```
