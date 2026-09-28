### Title
Loss-recovery price stored under wrong epoch key causes pending withdraw receipts to be paid at par - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.collectWithdrawFunds` records a loss-adjusted recovery price under `lossRecoveryPriceByEpoch[epochNumber]` at `stopEpoch`/`stopEpochWithDuration` time, but `epochNumber` has already been incremented by `deposit()` during that same `stopEpoch` flow. Pending receipts are indexed by `lastWithdrawRequest[_user]`, which still points at the request epoch. As a result `_claimLossAdjustedWithdrawRequest` looks up the recovery price under the stale epoch key, finds zero, and pays receipts at par through `_claimFundedWithdrawRequest` — while the strategy only collected the haircutted amount. This is the idle-tranches analog of a checkpoint/TTL that does not live under the key (epoch) for which the data is actually needed.

### Finding Description
- `requestWithdraw` stamps each receipt with the *request* epoch: `lastWithdrawRequest[_user] = currentEpoch` and `withdrawsRequestsByEpoch[_user][currentEpoch] += _amount`. [1](#0-0) 
- `deposit()` increments `epochNumber` whenever it is invoked while the epoch is running — which is exactly what happens inside `stopEpoch`. [2](#0-1) 
- `collectWithdrawFunds` is called by the CDO later in the same `stopEpochWithDuration` sequence, after the epoch bump, and stores the haircut under the *new* `epochNumber`. [3](#0-2) 
- The claim path resolves the loss epoch exclusively via `lastWithdrawRequest[_user]`, which still holds the old request epoch. `lossRecoveryPriceByEpoch[oldEpoch]` is `0`, so the loss-adjusted path is skipped and the full `withdrawsRequests` amount is paid 1:1 by `_claimFundedWithdrawRequest`. [4](#0-3) 
- The re-request guard in `requestWithdraw` uses the same stale key (`lossRecoveryPriceByEpoch[lastWithdrawRequest]`), so it also fails to detect the pending haircut and lets users stack new requests on top. [5](#0-4) 

Uncertainty: the exact ordering inside `IdleCDOEpochVariant.stopEpochWithDuration` (whether `collectWithdrawFunds` is invoked strictly after the strategy `deposit`/`mintStrategyTokens` call that bumps `epochNumber`) could not be fully confirmed within the retrieved context. The finding stands if — as the code structure suggests — any epoch-advancing strategy call precedes `collectWithdrawFunds`; a Foundry PoC should verify this ordering first.

### Impact Explanation
The borrower funds only `pendingBasis - pendingLoss`, but every pending receipt claims its full un-haircutted basis. The strategy is insolvent by exactly `pendingLoss`: early claimers are made whole (loss socialization is bypassed — a broken waterfall invariant) and later claimers' `_transferFundedClaim` transfers revert on insufficient balance, permanently freezing the residual receipts until the vault is recapitalized. For a 10% pro-rata loss on a `pendingWithdraws` bucket of, e.g., 1M tokens, ~100k tokens of claims are unfundable.

### Likelihood Explanation
Triggers deterministically on any `stopEpochWithDuration(_lossAmount > 0)` while `pendingWithdraws != 0` — i.e., a realized borrower shortfall in an epoch with queued withdrawals. No privileged misbehavior is required: any KYC'd lender who requested a withdrawal in the loss epoch either escapes the haircut (unfair gain at other receipt-holders' expense) or, if they claim last, has funds frozen. The APR0 path is unaffected since APR0 interest for the default/loss epoch is only added to `pendingWithdraws` by `prepareStopEpochWithApr0` before the price write.

### Recommendation
Store the recovery price under the epoch that receipts reference. Either:
- snapshot `uint256 pendingEpoch = epochNumber - 1` (or record the request epoch explicitly) and write `lossRecoveryPriceByEpoch[pendingEpoch]`, or
- increment `epochNumber` only after `collectWithdrawFunds` completes, or
- track a dedicated `pendingReceiptsEpoch` updated in `requestWithdraw`/`collectWithdrawFunds` and use it for both the guard and `_claimLossAdjustedWithdrawRequest` lookups.

Also make the `requestWithdraw` guard iterate over all epochs with non-zero `withdrawsRequestsByEpoch` rather than relying on a single `lastWithdrawRequest` key.

### Proof of Concept
Foundry fork test sketch (mode: fixed-APR, non-prefunded `IdleCDOEpochVariant`):

```solidity
// test/foundry/LossRecoveryEpochMismatch.t.sol
function test_LossReceiptsEscapeHaircut() public {
    // epoch N: KYC'd lender deposits via CDO, then requestWithdraw
    cdo.depositAA(1_000_000e6);            // during buffer/epoch N
    cdo.requestWithdraw(user, amt);        // lastWithdrawRequest[user] == N
    vm.prank(manager);
    cdo.startEpoch();                      // epoch running

    // stopEpochWithDuration realizes a loss; inside it:
    //   strategy.deposit/mintStrategyTokens runs while isEpochRunning() => epochNumber N -> N+1
    //   collectWithdrawFunds(fundedAmt < pendingBasis) writes
    //   lossRecoveryPriceByEpoch[N+1] = fundedAmt * 1e18 / pendingBasis
    cdo.stopEpochWithDuration(lossAmount, duration);

    // user claims: _claimLossAdjustedWithdrawRequest reads
    // lossRecoveryPriceByEpoch[lastWithdrawRequest = N] == 0 -> skipped,
    // _claimFundedWithdrawRequest pays full amt.
    uint256 balBefore = underlying.balanceOf(user);
    cdo.claimWithdrawRequest(user);
    assertEq(underlying.balanceOf(user) - balBefore, amt); // par payout, no haircut

    // second receipt holder's claim now reverts: vault holds only
    // pendingBasis - pendingLoss, insolvent by pendingLoss.
    vm.expectRevert();
    cdo.claimWithdrawRequest(user2);
}
```

Assertions: (1) `vault.lossRecoveryPriceByEpoch(N) == 0` while `lossRecoveryPriceByEpoch(N+1) != 0` confirms the key mismatch; (2) first claim pays at par proving haircut evasion; (3) the final claim reverts proving insolvency equal to the un-applied loss share.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L262-270)
```text
    uint256 lossRecoveryPrice = lossRecoveryPriceByEpoch[lossEpoch];
    if (
      lossRecoveryPrice != 0 &&
      (withdrawsRequestsByEpoch[_user][lossEpoch] != 0 ||
      (apr0Users[_user].principal != 0 && apr0Users[_user].principalEpoch == lossEpoch))
    ) {
      // A loss-adjusted receipt must be claimed before opening a later request, otherwise
      // `lastWithdrawRequest` would stop pointing to the epoch that stores its haircut.
      revert NotAllowed();
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L281-294)
```text
    // save the epoch of the last withdraw request (buffer + epochDuration is 1 epoch)
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
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L411-421)
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
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L607-611)
```text
    if (IIdleCDOEpochVariant(idleCDO).isEpochRunning()) {
      // deposit done on stopEpoch (before setting the var to false) so we reset the counter
      totEpochDeposits = 0;
      epochNumber += 1;
    } else {
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
