### Title
Loss-adjusted withdraw receipts are repaid at par after a second `requestWithdraw` overwrites `lastWithdrawRequest` - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.requestWithdraw` unconditionally overwrites `lastWithdrawRequest[_user]` with the current epoch. The loss-adjusted claim path (`_claimLossAdjustedWithdrawRequest`) uses `lastWithdrawRequest[_user]` as the sole lookup key into `lossRecoveryPriceByEpoch`. A user whose receipt was haircut in a `stopEpochWithDuration` partial-funding epoch can submit a new withdraw request, which erases the reference to the loss epoch while leaving the old amount inside the aggregate `withdrawsRequests[_user]`. The haircutted principal is then paid at par through `_claimFundedWithdrawRequest`, letting the attacker escape the loss and drain other users' funded withdrawals.

### Finding Description
This is the use-after-free analog of CVE-2021-30544: a stale/overwritten state reference lets a "freed" (loss-adjusted) receipt be reused through a different claim path at full value.

In `requestWithdraw`, the marker is overwritten each call (`lastWithdrawRequest[_user] = currentEpoch`) and the new amount is added to the aggregate `withdrawsRequests[_user]` [1](#0-0) .

When the borrower under-funds pending withdrawals, `collectWithdrawFunds` sets `pendingWithdraws = 0` and records `lossRecoveryPriceByEpoch[epoch]` — but the per-user aggregate `withdrawsRequests[_user]` is never reduced [2](#0-1) .

`_claimLossAdjustedWithdrawRequest` can only clear the epoch stored in `lastWithdrawRequest[_user]`; per-epoch data for any earlier loss epoch is unreachable once the marker moves [3](#0-2) .

`claimWithdrawRequest` then falls through to `_claimFundedWithdrawRequest`, which pays `withdrawsRequests[_user]` — still containing the haircutted epoch's amount — at par once `epochNumber > lastWithdrawRequest[_user]` [4](#0-3) [5](#0-4) .

### Impact Explanation
An unprivileged lender who requested a withdrawal in an epoch that suffered a `stopEpochWithDuration` loss can recover 100% of principal instead of `lossRecoveryPrice` by making a dust-sized second `requestWithdraw`, waiting one epoch, then calling `claimWithdrawRequest`. The excess is paid from funded underlying reserved for other pending receipts, so honest withdrawers are left with unbacked receipts — direct theft equal to `(1 - lossRecoveryPrice/RECOVERY_FULL) * claimBasis`, and potential insolvency of the withdraw reserve.

### Likelihood Explanation
Requirements: a partial-funding epoch occurs (borrower/manager-driven, honest actors), then the attacker performs only permissionless `requestWithdraw` + `claimWithdrawRequest` calls. No privileged cooperation is needed and no existing guard stops it: `_clearWithdrawClaimForEpoch` only resets the marker when `lastWithdrawRequest == _claimEpoch` [6](#0-5) , and the funded-claim revert only gates on the *latest* epoch number. The same logic applies to APR0 receipts since `apr0Users` entries for the orphaned epoch are never cleared.

### Recommendation
Track the user's loss-adjusted claim per epoch rather than via a single `lastWithdrawRequest` slot. Options: iterate/clear all epochs with `withdrawsRequestsByEpoch[_user][e] != 0` that have a non-zero `lossRecoveryPriceByEpoch`, or store a per-user list/set of loss epochs; alternatively remove the loss-adjusted basis from `withdrawsRequests[_user]` inside `collectWithdrawFunds`/`stopEpochWithDuration` so the funded path cannot re-pay it. Also handle the symmetric case where the loss epoch is skipped and `_claimLossAdjustedWithdrawRequest` can never clear it (permanent freeze of that receipt).

### Proof of Concept
Foundry fork PoC sketch (pattern follows `test/foundry/IdleCreditVault.t.sol`):

```solidity
function testLossAdjustedReceiptPaidAtParAfterSecondRequest() external {
    // setup: attacker deposits, epoch 0 starts
    idleCDO.depositAA(1000 * ONE_SCALE);
    _startEpochAndCheckPrices(0);

    // epoch 0 ends; attacker requests withdraw of all tranches in buffer of epoch 1
    vm.warp(cdoEpoch.epochEndDate() + 1);
    cdoEpoch.stopEpoch(0, 0);
    uint256 principal = cdoEpoch.requestWithdraw(attackerTrancheBal, address(AAtranche));

    _startEpochAndCheckPrices(1);

    // borrower under-funds: stopEpochWithDuration creates lossRecoveryPriceByEpoch[1] = 50%
    uint256 pendingBasis = strategy.pendingWithdraws();
    vm.warp(cdoEpoch.epochEndDate() + 1);
    deal(underlying, borrower, pendingBasis / 2);
    cdoEpoch.stopEpochWithDuration(0, pendingBasis / 2); // loss recorded for epoch 1

    // attacker submits a dust requestWithdraw in epoch 2 buffer -> lastWithdrawRequest overwritten
    uint256 dust = cdoEpoch.requestWithdraw(1, address(AAtranche));

    _startEpochAndCheckPrices(2);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    // fund epoch-2 pendingWithdraws fully so funded path succeeds
    deal(underlying, borrower, strategy.pendingWithdraws());
    cdoEpoch.stopEpoch(0, 0);

    // attacker claims: loss-adjusted path skipped (lastWithdrawRequest == 2, no price),
    // funded path pays full withdrawsRequests[attacker] at par
    uint256 balBefore = underlying.balanceOf(address(this));
    cdoEpoch.claimWithdrawRequest();
    assertGt(underlying.balanceOf(address(this)) - balBefore, principal * 50 / 100 + dust);
}
```

Expected: attacker receives `principal + dust` (par) instead of `principal * lossRecoveryPrice / RECOVERY_FULL + dust`.

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L301-313)
```text
  function claimWithdrawRequest(address _user) external returns (uint256 amount) {
    _onlyIdleCDO();
    if (defaultRecoveryFinalized) {
      // Post-default requests are already priced after the haircut and backed by the reserve,
      // so they must not fall through to the defaulted-epoch receipt logic.
      amount = _claimPostDefaultWithdrawRequest(_user);
      if (amount != 0) return amount;
      // Only receipts created in the defaulted epoch are haircutted here; old fulfilled
      // receipts are handled below at par if they were already funded before default.
      amount = _claimDefaultedWithdrawRequest(_user);
    }
    amount += _claimLossAdjustedWithdrawRequest(_user);
    return amount + _claimFundedWithdrawRequest(_user);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L338-349)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L411-420)
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L832-836)
```text
    if (lastWithdrawRequest[_user] == _claimEpoch) {
      // The cleared epoch was the latest request marker. Any remaining normal/APR0 receipt
      // is older and already funded, so it can continue to the funded-claim path.
      lastWithdrawRequest[_user] = 0;
    }
```
