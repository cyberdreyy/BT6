### Title
Loss-adjusted withdraw receipts are keyed to the wrong epoch, letting pending withdrawals claim at par after `stopEpochWithDuration` loss - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
The external bug is an unvalidated index: `tty_driver_lookup_tty()` returns an object for an attacker-supplied `console=` index without checking bounds, so an invalid identifier resolves to a bogus entry that is then dereferenced. The analog in this codebase is epoch-indexed lookup in `IdleCreditVault`: `collectWithdrawFunds` stores the stop-loss recovery price under `lossRecoveryPriceByEpoch[epochNumber]`, while `_claimLossAdjustedWithdrawRequest` and the re-request guard in `requestWithdraw` look it up under `lastWithdrawRequest[_user]` — the epoch in which the receipt was created. Because `epochNumber` is incremented inside `deposit()` during `stopEpoch` before `collectWithdrawFunds` runs, the loss is recorded under epoch `N+1` while every receipt it haircuts is keyed under epoch `N`. The lookup uses an index that is never validated against the epoch that actually carries the loss, so haircut receipts silently resolve to `lossRecoveryPrice == 0` ("no loss-adjusted epoch") and are paid at par by `_claimFundedWithdrawRequest`.

### Finding Description
The relevant flow:

1. During epoch `N`, a lender calls `IdleCDOEpochVariant.requestWithdraw`, which calls `IdleCreditVault.requestWithdraw`. The receipt is stored as `withdrawsRequestsByEpoch[_user][epochNumber] += _amount` with `epochNumber == N`, `lastWithdrawRequest[_user] = N`, and `pendingWithdraws += _amount`. [1](#0-0) 

2. At `stopEpoch`/`stopEpochWithDuration`, the strategy's `deposit()` is invoked while `isEpochRunning()` is still true, executing `epochNumber += 1` (now `N+1`) before withdraw funding is collected. [2](#0-1) 

3. `collectWithdrawFunds` then records the haircut under the *already-incremented* epoch: `lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice` stores the price under `N+1`, while zeroing `pendingWithdraws` and pulling only `_amount = pendingBasis * lossRecoveryPrice / 1e18` of underlying. [3](#0-2) 

4. On claim, `_claimLossAdjustedWithdrawRequest` reads `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` — i.e. `lossRecoveryPriceByEpoch[N]`, which is `0` — and returns early. [4](#0-3) 

5. `_claimFundedWithdrawRequest` then checks only `epochNumber <= lastWithdrawRequest[_user]` → `N+1 <= N` is false, so it pays `withdrawsRequests[_user]` **at par**, burning the receipt 1:1 and calling `_transferFundedClaim`. [5](#0-4) 

The same wrong index also defeats the re-request guard in `requestWithdraw` (`lossRecoveryPriceByEpoch[lastWithdrawRequest]` is checked at `N`, not `N+1`), so users can stack additional requests on top of an unclaimed haircut receipt, which the comment explicitly says must be blocked. [6](#0-5) 

Note on verification: the ordering assumption is that `epochNumber += 1` in `deposit()` runs before `collectWithdrawFunds` inside the same `stopEpoch` transaction — this is the natural flow (the CDO deposits the borrower's repayment into the strategy to mint its tokens, then collects the funded withdrawal amount), and `deposit()` itself comments "deposit done on stopEpoch (before setting the var to false) so we reset the counter". I was not able to trace the exact call order inside `IdleCDOEpochVariant.stopEpoch` within the available iterations; if `collectWithdrawFunds` were invoked before the deposit/increment, the keys would align and the bug would not manifest — this should be confirmed first.

### Impact Explanation
Every loss-adjusted pending withdrawal is funded at `lossRecoveryPrice` but pays out at `RECOVERY_FULL`. An attacker (any KYC-passing tranche holder) requests a withdraw in epoch `N`, waits for the manager to call `stopEpochWithDuration` with a loss, then calls `claimWithdrawRequest` and receives their full basis instead of `basis * lossRecoveryPrice`. The first claimers drain the strategy's funded underlyings — which include the recovery-funded pool and the `defaultRecoveryReserve` — causing either direct theft of other pending claimers' funds (last claimers' transactions revert on insufficient balance = permanent freezing of their claims) or insolvency of the withdraw bucket. Quantified loss: `(1 - lossRecoveryPrice/1e18) * pendingBasis` per loss epoch, claimable by whichever pending withdrawer calls first.

### Likelihood Explanation
Requires: a normal withdraw request outstanding plus a `stopEpochWithDuration(_lossAmount > 0)` — a sanctioned flow where the borrower repays short (partial default/loss socialization) handled by honest manager calls. The attacker needs no privilege beyond being a lender and timing a `claimWithdrawRequest` before other pending claimers. No guard (`NotAllowed` reverts, `Default` check, epoch gating) catches it because the lookup itself returns the "no loss" sentinel `0`.

### Recommendation
Record and read the loss price under a single, consistent epoch key: either increment `epochNumber` *after* `collectWithdrawFunds` in the stop-epoch sequence, or key `lossRecoveryPriceByEpoch` to the request epoch (`epochNumber - 1` post-increment) and validate in `_claimLossAdjustedWithdrawRequest`/`requestWithdraw` that the looked-up epoch actually matches the epoch that carried the loss rather than silently treating `0` as "no loss". Add a regression test asserting that a pending receipt spanning a `stopEpochWithDuration` loss claims at the haircut price.

### Proof of Concept
```solidity
// test/foundry/LossEpochIndex.t.sol — fork of existing IdleCreditVault harness
function testLossReceiptClaimsAtParWrongEpoch() external {
  // setup: deposit, epoch N running
  _depositWithUser(USER, 100_000 * ONE_SCALE);
  _startEpochAndCheckPrices(0);

  // attacker requests withdraw during epoch N (request epoch = N)
  vm.prank(USER);
  cdoEpoch.requestWithdraw(0, address(AAtranche));
  assertEq(strategy.lastWithdrawRequest(USER), strategy.epochNumber());

  // borrower repays short: stopEpochWithDuration with _lossAmount > 0
  // -> deposit() bumps epochNumber to N+1, then collectWithdrawFunds stores
  //    lossRecoveryPriceByEpoch[N+1] = price < 1e18
  _stopEpochWithLoss(...); // partial funding: collectWithdrawFunds(_amount < pendingBasis)

  // claim: _claimLossAdjustedWithdrawRequest reads lossRecoveryPriceByEpoch[N] == 0
  // and _claimFundedWithdrawRequest pays full basis at par
  uint256 balPre = underlying.balanceOf(USER);
  vm.prank(USER);
  cdoEpoch.claimWithdrawRequest();
  uint256 paid = underlying.balanceOf(USER) - balPre;

  uint256 funded = /* _amount collected */;
  assertGt(paid, funded, "attacker paid above funded haircut amount");
}
```

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L260-294)
```text
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
    // burn strategy tokens from cdo (we don't burn future interest here, only the principal)
    _burn(msg.sender, _principal);
    // mint equal amount of strategy tokens to the user as receipt (interest included), useful in case of default
    _mint(_user, _amount);
    // A successfully closed pool already recalled all funds and has no later stopEpoch.
    if (!isClosed) {
      // Global amount that stopEpoch must source from borrower/strategy for all pending receipts.
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L607-610)
```text
    if (IIdleCDOEpochVariant(idleCDO).isEpochRunning()) {
      // deposit done on stopEpoch (before setting the var to false) so we reset the counter
      totEpochDeposits = 0;
      epochNumber += 1;
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
