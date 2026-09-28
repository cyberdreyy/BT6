### Title
Loss haircut on pending withdrawals applies only to the user's latest request epoch, letting earlier receipts claim at par and over-drain the funded reserve - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`collectWithdrawFunds` computes `lossRecoveryPriceByEpoch[epochNumber]` over the *aggregate* `pendingWithdraws` basis (all users, all epochs), but `_claimLossAdjustedWithdrawRequest` haircuts only the claim basis recorded in `withdrawsRequestsByEpoch[_user][lastWithdrawRequest[_user]]` — the user's *most recent* request epoch. Any earlier pending receipt for the same user falls through to `_claimFundedWithdrawRequest`, which pays it at par. A user who holds receipts from two different epochs escapes the loss on the older receipt and is paid more underlying than the borrower actually funded, draining the strategy's claim reserve below solvency.

### Finding Description
The RFQ bug class — an irrevocable commitment executed later at stale terms — maps to IdleCreditVault withdraw receipts: once minted they cannot be repriced, and the loss-adjustment mechanism fails to reprice stale receipts from earlier epochs.

1. During the buffer before epoch N, the attacker calls `IdleCDOEpochVariant.requestWithdraw`, which hits `IdleCreditVault.requestWithdraw`: `_amount` is added to both `withdrawsRequests[_user]` and `withdrawsRequestsByEpoch[_user][N]`, `lastWithdrawRequest[_user] = N`, and `pendingWithdraws += _amount` [1](#0-0) .
2. The attacker lets epoch N be funded normally at stopEpoch but does **not** claim (claiming is optional; the receipt stays valid).
3. During the buffer before epoch N+1 the attacker requests again. The guard at lines 263-271 only blocks re-request when `lossRecoveryPriceByEpoch[lossEpoch] != 0`; with no prior loss it passes. `lastWithdrawRequest[_user]` is overwritten to N+1 while the epoch-N basis remains inside `withdrawsRequests`/`pendingWithdraws`.
4. Epoch N+1 ends with a loss: `stopEpochWithDuration(_lossAmount)` → `collectWithdrawFunds(_amount)` with `_amount < pendingWithdraws`, so `lossRecoveryPrice = _amount * RECOVERY_FULL / pendingWithdraws` is stored under the current `epochNumber` (N+1) and `pendingWithdraws = 0` [2](#0-1) . The intended semantics, per `previewLossAdjustedWithdrawFunds`, is that the loss is shared pro-rata across the *entire* pending bucket [3](#0-2) .
5. On `claimWithdrawRequest`, `_claimLossAdjustedWithdrawRequest` reads `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` = epoch N+1 and haircuts only `withdrawsRequestsByEpoch[_user][N+1]`; the epoch-N entry is untouched [4](#0-3) . Then `_claimFundedWithdrawRequest` pays the remaining `withdrawsRequests[_user]` (the epoch-N receipt) at par via `_transferFundedClaim` [5](#0-4) .

Broken invariant (loss waterfall / solvency): strategy received `received = (N + M) · p` underlying but pays out `N·1 + M·p`, i.e. `N·(1−p)` more than funded. The deficit is pulled from underlying held for instant-withdraw claims and other users' funded receipts.

### Impact Explanation
Direct insolvency of the withdraw-claim reserve. With attacker epoch-N receipt `N` and epoch-N+1 receipt `M`, and recovery price `p < 1`, the attacker extracts `N·(1−p)` underlying beyond what the borrower funded. Example: `N = M = 100k`, `p = 0.5` → funded `100k`, attacker claims `100k·1 + 100k·0.5 = 150k`, a 50k shortfall borne by other receipt holders whose later claims revert on insufficient balance (permanent freezing of unclaimed yield) or by the strategy's reserve.

### Likelihood Explanation
Requires only two `requestWithdraw` calls in different epochs plus one lossy `stopEpochWithDuration` — a normal, honest manager flow used whenever the borrower underpays. The attacker is an ordinary KYC-passed lender; no privileged action is needed. The trigger condition (partial funding of pending withdraws) is a designed code path, not an edge case.

### Recommendation
Apply the epoch-loss haircut to every pending receipt included in `pendingBasis`, not just `lastWithdrawRequest`. Options: (a) iterate `withdrawsRequestsByEpoch` over all epochs with nonzero basis and clear them at `lossRecoveryPrice`; or (b) store a per-epoch haircut and have `_claimFundedWithdrawRequest` check `lossRecoveryPriceByEpoch` for each outstanding epoch before paying par; or (c) reject a new `requestWithdraw` whenever the user has any still-pending (unfunded) receipt, not only a loss-adjusted one, so `pendingWithdraws` never mixes receipt generations with different loss treatment.

### Proof of Concept
```solidity
// Foundry fork test against the credit-vault deployment.
// Setup: deposit, epoch running/stopped cycles use existing helpers.
function testCrossEpochReceiptEscapesLoss() external {
    uint256 amount = 100_000 * ONE_SCALE;
    idleCDO.depositAA(amount);

    // buffer of epoch N: request withdraw #1 (do not claim)
    uint256 req1 = cdoEpoch.requestWithdraw(trancheBal / 2, address(AAtranche));
    _startEpochAndCheckPrices(N);            // epoch N funded normally at stopEpoch

    // buffer of epoch N+1: request withdraw #2 (re-request allowed, no prior loss)
    uint256 req2 = cdoEpoch.requestWithdraw(trancheBal / 4, address(AAtranche));

    // stopEpoch N+1 with partial funding => lossRecoveryPriceByEpoch[N+1] = p < 1
    uint256 pendingBasis = creditVault.pendingWithdraws(); // req1 + req2
    uint256 funded = pendingBasis / 2;
    vm.warp(cdoEpoch.epochEndDate() + 1);
    deal(underlying, borrower, funded + expectedInterest);
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(0, pendingBasis - funded);

    // claim: epoch-N+1 piece haircut, epoch-N piece paid AT PAR
    uint256 pre = underlying.balanceOf(address(this));
    cdoEpoch.claimWithdrawRequest();
    uint256 got = underlying.balanceOf(address(this)) - pre;

    // received = req1 + req2*p  >  funded = (req1+req2)*p
    assertGt(got, funded); // over-drain of req1*(1-p); later claimants' claims revert
}
```

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L279-293)
```text
      pendingWithdraws += _amount;
    }
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L411-426)
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
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L440-459)
```text
  function previewLossAdjustedWithdrawFunds(uint256 _lossAmount) external view returns (uint256 pendingToFund, uint256 activeLoss) {
    uint256 pendingBasis = pendingWithdraws;
    // Full zero-loss funding is safe for legacy aggregate receipts and needs no migration call.
    if (_lossAmount == 0) return (pendingBasis, _lossAmount);

    IIdleCDOEpochVariant cdo = IIdleCDOEpochVariant(idleCDO);
    uint256 activeBasis = _lossActiveBasis(cdo);
    if (pendingBasis == 0) {
      if (_lossAmount > activeBasis) revert NotAllowed();
      return (0, _lossAmount);
    }

    // Legacy pending receipts do not have the per-epoch ownership data needed to store a haircut.
    if (!defaultRecoveryInitialized) revert NotAllowed();
    uint256 totalBasis = activeBasis + pendingBasis;
    if (_lossAmount >= totalBasis) revert NotAllowed();

    uint256 pendingLoss = _lossAmount * pendingBasis / totalBasis;
    pendingToFund = pendingBasis - pendingLoss;
    activeLoss = _lossAmount - pendingLoss;
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
