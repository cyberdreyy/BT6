Found a strong candidate — need to verify epoch numbering between `requestWithdraw` and `collectWithdrawFunds`/`lossRecoveryPriceByEpoch`. Checking the epoch variant's stopEpoch ordering.### Title
Loss-adjusted withdraw receipts keyed to a stale epoch pay out at par (or freeze), breaking the loss waterfall - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`IdleCreditVault` records a user's withdraw-request epoch in `lastWithdrawRequest[_user]` and `withdrawsRequestsByEpoch[_user][currentEpoch]` using `epochNumber` at request time (`requestWithdraw`, lines 260–294), but `collectWithdrawFunds` stores the haircut under `lossRecoveryPriceByEpoch[epochNumber]` using the *post-increment* `epochNumber` — `deposit()` increments `epochNumber` inside `stopEpoch` (lines 607–610) before the CDO pulls the funded amount. The claimant-side lookup in `_claimLossAdjustedWithdrawRequest` uses `lastWithdrawRequest[_user]` (the request-time epoch), so it reads `lossRecoveryPriceByEpoch` at the wrong key, finds 0, and the haircut receipt falls through to `_claimFundedWithdrawRequest`, which pays the full `withdrawsRequests[_user]` at par. This is the vault analog of the CVE's use-after-free class: a stale epoch pointer lets an already-"freed" (haircutted) claim be re-used at full value.

### Finding Description
- `requestWithdraw` sets `lastWithdrawRequest[_user] = currentEpoch` where `currentEpoch = epochNumber` read during the buffer phase (lines 260, 282, 293). [1](#0-0) 
- `deposit()` increments `epochNumber` when `isEpochRunning()` is true, i.e. during the CDO's `stopEpoch` funding deposit (lines 607–610). [2](#0-1) 
- On a lossy `stopEpochWithDuration`, `collectWithdrawFunds` writes `lossRecoveryPriceByEpoch[epochNumber]` — now the incremented value — while receipts still point at the request-time epoch (lines 414–422). [3](#0-2) 
- `_claimLossAdjustedWithdrawRequest` computes `lossEpoch = lastWithdrawRequest[_user]` and reads `lossRecoveryPriceByEpoch[lossEpoch]`; with the off-by-one key it returns 0 and early-returns without clearing anything (lines 789–795). [4](#0-3) 
- `_claimFundedWithdrawRequest` then only requires `epochNumber > lastWithdrawRequest[_user]`, which is true after the stop, and pays `withdrawsRequests[_user]` at par, burning the receipt and zeroing the accounting (lines 326–349). [5](#0-4) 

The queue test implicitly confirms the "+1" numbering convention (`claimEpoch = strategy.epochNumber() + 1`, `test/foundry/IdleCDOEpochQueue.t.sol:817-862`), i.e. prices processed at stopEpoch are indexed by the incremented epoch while requests were booked under the pre-increment one.

### Impact Explanation
The borrower only funds `pendingToFund = pendingBasis - pendingLoss` (per `previewLossAdjustedWithdrawFunds`, lines 440–459), so the strategy holds less underlying than the aggregate receipt basis. Each claimant who reaches `_claimFundedWithdrawRequest` first withdraws at par, draining the haircut subsidy from later claimants; the last claimants either get nothing (transfer reverts on insufficient balance → permanent freeze of unclaimed funds) or the deficit is silently socialized onto the default/recovery reserve. Broken invariant: loss waterfall / one-receipt-one-payout. Direct quantified theft of `(1 - lossRecoveryPrice) * claimBasis` per exploiting claim, plus insolvency for remaining receipt holders.

### Likelihood Explanation
Requires only a `stopEpochWithDuration(_lossAmount > 0)` executed by the honest manager — a designed code path for partial borrower repayment — and any unprivileged KYC'd lender with a pending withdraw request in that epoch. The attacker merely calls `cdoEpoch.claimWithdrawRequest()` before others; no privileged action and no guard (`requestWithdraw`'s loss-epoch check at lines 263–270 only gates *new* requests, not the claim path itself). Uncertainty: the exact call ordering inside `IdleCDOEpochVariant.stopEpochWithDuration`/`stopEpoch` (whether `collectWithdrawFunds` runs strictly after the `epochNumber`-incrementing `deposit`) could not be fully verified in this session; the finding hinges on that ordering, which the strategy's own comments ("deposit done on stopEpoch … epochNumber += 1") and the queue's `epochNumber() + 1` test convention strongly support.

### Recommendation
Key `lossRecoveryPriceByEpoch` by the *request* epoch, not the post-stop `epochNumber`: either snapshot `epochNumber - 1` (or the pre-increment value) when writing in `collectWithdrawFunds`, or store the pending-loss epoch explicitly at `prepareStopEpoch`/`stopEpochWithDuration` time. Symmetrically, `_claimLossAdjustedWithdrawRequest` should iterate or be given the correct claim epoch rather than relying on `lastWithdrawRequest` matching the storage key. Add a regression test: request in epoch E, `stopEpochWithDuration` with `_lossAmount > 0`, then claim and assert payout equals `claimBasis * lossRecoveryPrice / 1e18`.

### Proof of Concept
Foundry fork PoC sketch (mirroring `testClaimLossAdjustedWithdraw` setup in `test/foundry/IdleCreditVault.t.sol` / `IdleCDOEpochQueue.t.sol:811-858`):

```solidity
// buffer phase of epoch E
uint256 req = cdoEpoch.requestWithdraw(trancheBal, address(AAtranche)); // lastWithdrawRequest[user] = E

// borrower partially repays; manager stops with a loss
deal(underlying, borrower, expectedInterest + pendingToFund);
vm.prank(manager);
cdoEpoch.stopEpochWithDuration(apr, 0, duration, lossAmount);
// deposit() inside stopEpoch bumps epochNumber to E+1;
// collectWithdrawFunds writes lossRecoveryPriceByEpoch[E+1]
assertEq(strategy.lastWithdrawRequest(user), E);
assertEq(strategy.lossRecoveryPriceByEpoch(E), 0);            // stale key -> lookup misses
assertGt(strategy.lossRecoveryPriceByEpoch(E + 1), 0);        // haircut stored one epoch later

uint256 balPre = underlying.balanceOf(user);
cdoEpoch.claimWithdrawRequest();                               // pays at par via _claimFundedWithdrawRequest
assertEq(underlying.balanceOf(user) - balPre, req);            // full payout, haircut escaped
```

Expected broken assertion per intended design: payout should be `req * lossRecoveryPrice / 1e18`; actual payout is `req`, and subsequent claimants' claims revert or are under-funded.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L260-293)
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L411-422)
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L789-795)
```text
  function _claimLossAdjustedWithdrawRequest(address _user) internal returns (uint256 amount) {
    uint256 lossEpoch = lastWithdrawRequest[_user];
    uint256 lossRecoveryPrice = lossRecoveryPriceByEpoch[lossEpoch];
    if (lossRecoveryPrice == 0) return amount;

    (uint256 claimBasis, uint256 burnAmount) = _clearWithdrawClaimForEpoch(_user, lossEpoch, false);
    if (claimBasis == 0) return amount;
```
