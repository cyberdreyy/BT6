### Title
Stop-epoch loss price is keyed by the post-increment `epochNumber`, so loss-adjusted withdraw receipts claim at par and a later epoch's receipts eat the haircut - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The kernel bug (CVE-2024-26633) is a parser reading `frag_off` at a header offset that was never actually pulled into the skb — i.e., dereferencing state keyed by a position that does not correspond to the data actually present. The analog in `IdleCreditVault` is per-epoch accounting indexed by an `epochNumber` that is incremented mid-`stopEpoch`: `collectWithdrawFunds` stores the haircut under the *new* epoch while user receipts were recorded under the *previous* epoch. Reads of `lossRecoveryPriceByEpoch[lastWithdrawRequest[user]]` therefore look up a slot that does not hold the recorded haircut, exactly like reading `frag_off` past the pulled bytes.

### Finding Description
Pending withdraw receipts are keyed by request epoch:

- `requestWithdraw` stores `withdrawsRequestsByEpoch[_user][currentEpoch]` and `lastWithdrawRequest[_user] = currentEpoch` before the epoch turns over. [1](#0-0) 
- `deposit()` increments `epochNumber` during `stopEpoch` while `isEpochRunning` is still true. [2](#0-1) 
- `collectWithdrawFunds(_amount)`, invoked after the CDO has been repaid and re-deposited (it pulls funded underlying from `idleCDO`), records a partial funding as `lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice` — now keyed by the *incremented* epoch. [3](#0-2) 
- `_claimLossAdjustedWithdrawRequest` looks up `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]`, i.e., the *pre-increment* request epoch, which is zero. [4](#0-3) 

Two consequences follow:

1. Receipts that should have been haircut fall through to `_claimFundedWithdrawRequest`, which — since `epochNumber > lastWithdrawRequest` now holds — pays `withdrawsRequests[_user]` **at par** even though `pendingWithdraws` was cleared with only `pendingToFund` collected. [5](#0-4) 
2. A user whose next request lands in the following buffer has `lastWithdrawRequest = N+1`, the epoch key that now carries `lossRecoveryPrice`, so their fully funded new receipt is misrouted into `_claimLossAdjustedWithdrawRequest` and paid out at the stale haircut, or their new request is blocked by the guard at lines 263–271. [6](#0-5) 

The invariant violated is one-receipt-one-payout plus the intended `previewLossAdjustedWithdrawFunds` split (pending receipts bear `pendingLoss` pro rata): in reality early claimants take 100% of a bucket funded at `<100%`, and either later claimants' `_transferFundedClaim` reverts on insufficient balance (permanent freezing of unclaimed funds) or the payout drains underlying belonging to the instant-withdraw queue / `defaultRecoveryReserve`. [7](#0-6) 

### Impact Explanation
After any `stopEpochWithDuration(_lossAmount)` with `pendingWithdraws != 0`, loss-adjusted receipts are payable at par while only `pendingBasis - pendingLoss` was collected. First claimants extract excess underlying (theft from the strategy's shared underlying pool); the residual shortfall either bricks subsequent `claimWithdrawRequest`/`claimInstantWithdrawRequest` calls or is taken from `defaultRecoveryReserve`/instant-withdraw funding. Quantified loss: up to `pendingLoss = _lossAmount * pendingBasis / totalBasis` stolen or frozen. Additionally, honest users requesting in the next buffer get spuriously haircut or revert.

### Likelihood Explanation
Requires only: pending withdraw requests plus a `stopEpochWithDuration` loss — a manager-driven but routine loss-realization path. The attacker needs only to be a KYC'd tranche holder with a pending receipt who calls `claimWithdrawRequest` first; no privileged collusion. Caveat I could not fully confirm without reading `stopEpoch`/`stopEpochWithDuration` in `IdleCDOEpochVariant.sol`: the bug holds if the `deposit()` that bumps `epochNumber` executes before `collectWithdrawFunds`; the repayment-then-deposit-then-collect ordering implied by the code comments ("deposit done on stopEpoch … so we reset the counter") strongly suggests it does. If the calls were reversed, the keys would match and this is not exploitable — a PoC must verify ordering first.

### Recommendation
Key `lossRecoveryPriceByEpoch` by the epoch the pending receipts belong to (e.g., `epochNumber - 1` after the stop deposit, or capture `pendingEpoch` at request time), or increment `epochNumber` only after `collectWithdrawFunds`. Add a regression test where a stop loss partially funds pending receipts and assert claims pay `basis * lossRecoveryPrice / 1e18`, not par.

### Proof of Concept
Foundry fork sketch (verify deposit/collect ordering inside `stopEpochWithDuration` first):

```solidity
// setup: AA deposits, epoch 0 runs, requestWithdraw during buffer
cdoEpoch.requestWithdraw(mintedAA, address(AAtranche)); // lastWithdrawRequest[user] = epochNumber = N

// borrower partially repays; manager stops epoch with a loss
// inside stop: strategy.deposit() -> epochNumber becomes N+1
// then collectWithdrawFunds(pendingToFund) -> lossRecoveryPriceByEpoch[N+1] = price < 1e18
cdoEpoch.stopEpochWithDuration(lossAmount, duration, apr); // manager

// claim: lossRecoveryPriceByEpoch[N] == 0 -> funded path -> pays full basis
uint256 balBefore = underlying.balanceOf(user);
cdoEpoch.claimWithdrawRequest(); // user receives requestBasis, not basis*price
assertGt(underlying.balanceOf(user) - balBefore, basis * price / 1e18);

// insolvency: second claimant's _transferFundedClaim reverts or drains reserve
vm.expectRevert(); // or assert reserve drained
cdoEpoch.claimWithdrawRequest(); // user2
```

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L259-271)
```text
    bool isClosed = IIdleCDOEpochVariant(idleCDO).epochEndDate() == 0;
    uint256 currentEpoch = epochNumber;
    uint256 lossEpoch = lastWithdrawRequest[_user];
    uint256 lossRecoveryPrice = lossRecoveryPriceByEpoch[lossEpoch];
    if (
      lossRecoveryPrice != 0 &&
      (withdrawsRequestsByEpoch[_user][lossEpoch] != 0 ||
      (apr0Users[_user].principal != 0 && apr0Users[_user].principalEpoch == lossEpoch))
    ) {
      // A loss-adjusted receipt must be claimed before opening a later request, otherwise
      // `lastWithdrawRequest` would stop pointing to the epoch that stores its haircut.
      revert NotAllowed();
    }
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L319-350)
```text
  function _claimFundedWithdrawRequest(address _user) internal returns (uint256 amount) {
    // User should wait at least an epoch before claiming the withdraw. Once the epoch is over user can withdraw 
    // at any time even if a new epoch started. 
    // So if epochNumber is the same as the last withdraw request then we revert. Epoch number is increased at stopEpoch
    // NOTE: If a user does not claim a withdraw request and instead requests another withdraw, he will have to wait
    // for another epoch to claim both requests.
    // NOTE 2: if borrower defaults, old withdraw requests can still be claimed
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
  }
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L440-460)
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
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L607-614)
```text
    if (IIdleCDOEpochVariant(idleCDO).isEpochRunning()) {
      // deposit done on stopEpoch (before setting the var to false) so we reset the counter
      totEpochDeposits = 0;
      epochNumber += 1;
    } else {
      // deposit done between epochs so we increase the counter
      totEpochDeposits += _amount;
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L789-801)
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
  }
```
