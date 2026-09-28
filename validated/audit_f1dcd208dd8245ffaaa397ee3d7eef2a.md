### Title
Epoch boundary off-by-one in `collectWithdrawFunds` lets pending withdraw receipts claim at par and drains the loss-adjusted reserve - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The Firefox boundary-condition bug maps to an epoch-indexing boundary error in `IdleCreditVault`. When a borrower under-funds pending withdrawals at `stopEpoch`, `collectWithdrawFunds` records the haircut `lossRecoveryPriceByEpoch[epochNumber]` using the *already-incremented* epoch counter, while user receipts were recorded against the pre-increment epoch via `lastWithdrawRequest[_user]` and `withdrawsRequestsByEpoch[_user][epoch]`. The recovery price is therefore stored under a key that no receipt will ever match, the loss-adjusted claim path is unreachable, and all affected receipts pay out at full (unhaircutted) par from an intentionally under-funded reserve — first claimers steal later claimants' funds.

### Finding Description
Two writes use different sides of the same epoch boundary:

1. At request time, `requestWithdraw` snapshots `currentEpoch = epochNumber` (e.g. N) and stores `lastWithdrawRequest[_user] = N` and `withdrawsRequestsByEpoch[_user][N] += amount` [1](#0-0) .

2. During `stopEpochWithDuration`/`stopEpoch`, the CDO calls `_strategy.deposit(...)`, which — because `isEpochRunning()` is still true inside the stop flow (it is set to false only later) — increments `epochNumber` to N+1 [2](#0-1) . The CDO stop flow then calls `collectWithdrawFunds(_amount)`; in the under-funded branch it writes `lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice`, i.e. under key **N+1**, not the request epoch **N** [3](#0-2) .

3. On claim, `claimWithdrawRequest` → `_claimLossAdjustedWithdrawRequest` looks up `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` = `lossRecoveryPriceByEpoch[N]` = 0, so it returns 0 and falls through [4](#0-3) .

4. `_claimFundedWithdrawRequest` then applies only the "wait one epoch" gate, `epochNumber <= lastWithdrawRequest[_user]` → N+1 <= N is false, so it proceeds and pays `withdrawsRequests[_user]` **in full at par** via `_transferFundedClaim`, even though the strategy only received the haircutted `_amount` [5](#0-4) .

The loss branch also zeroes `pendingWithdraws`, so nothing else reconciles the shortfall. `_claimLossAdjustedWithdrawRequest` exists precisely to apply this haircut and is dead code for every affected epoch — strong evidence the epoch key used in `collectWithdrawFunds` should be the request epoch (pre-increment) rather than the post-increment `epochNumber`.

### Impact Explanation
Direct theft / insolvency with quantified loss. Example: two users each have a pending receipt of 100 underlying for epoch N (pendingBasis = 200). The borrower repays only 100 (`_lossAmount` split funds `_amount = 100 < pendingBasis`), so the intended recovery price is 0.5 — each user should get 50. Because the price is stored under epoch N+1, the loss-adjusted path never fires: the first user to call `claimWithdrawRequest` receives the full 100, and the second user's claim reverts on insufficient balance (or, if unrelated funded underlyings sit in the contract, it can even spend funds reserved for `defaultRecoveryReserve`-protected claims — though `_transferFundedClaim` guards that once a default reserve exists). One receipt-one-payout and fair loss socialization are both broken; the loss is the entire unfunded delta (50% of pending claims in the example, up to ~100% as `_amount` → 0, bounded above only by the `lossRecoveryPrice == 0` revert at `RECOVERY_FULL` precision).

### Likelihood Explanation
Requires only unprivileged tranche holders: any KYC'd lender can open a withdraw request during a running epoch, and the loss path triggers whenever the borrower partially repays at `stopEpochWithDuration(_lossAmount)` — an honest manager/guardian flow that the rules explicitly allow sequencing around. No privileged misbehavior is needed; the victim is simply a later claimant or the remaining receipt holders. The boundary error is deterministic on every under-funded stop that has pending receipts, so likelihood within that scenario is high. The main uncertainty is the exact call order inside `IdleCDOEpochVariant.stopEpochWithDuration` — specifically that `collectWithdrawFunds` is invoked after the `deposit()` call that bumps `epochNumber` (the deposit at `IdleCDOEpochVariant.sol:466` precedes `isEpochRunning = false` at line 474, and the grep of that file did not return line-level context for `collectWithdrawFunds`). If a variant ever called `collectWithdrawFunds` before the epoch bump, the same flaw would flip direction — storing the price under N for receipts keyed at an earlier epoch — and still misapply the haircut.

### Recommendation
Store the loss recovery price under the epoch in which the receipts were recorded, not the post-increment counter. Concretely, in `collectWithdrawFunds` the loss branch should write `lossRecoveryPriceByEpoch[epochNumber - 1]` (i.e., the epoch that just ended / the epoch `requestWithdraw` snapshotted), or better, pass the request epoch explicitly from the CDO/`prepareStopEpoch` path so no implicit counter ordering is assumed. Add a regression test: user requests withdrawal in epoch N → `stopEpochWithDuration` with partial funding → both users claim and receive `basis * lossRecoveryPrice`, and `_claimLossAdjustedWithdrawRequest` is exercised rather than skipped.

### Proof of Concept
Foundry fork PoC sketch (modeled on `test/foundry/IdleCreditVault.t.sol` helpers such as `_startEpochAndCheckPrices`/`_toggleEpoch`):

```solidity
function testLossRecoveryEpochMismatch() external {
    // 1) Two users deposit AA during buffer, epoch starts (epochNumber = 0).
    idleCDO.depositAA(1000 * ONE_SCALE);            // test contract / user A
    _depositAs(userB, 1000 * ONE_SCALE, AAtranche);
    _startEpochAndCheckPrices(0);

    // 2) Both request withdraw of 100 underlying during epoch 0.
    //    -> IdleCreditVault: lastWithdrawRequest[A] = 0,
    //       withdrawsRequestsByEpoch[A][0] = 100e6, same for B.
    vm.prank(userA); cdoEpoch.requestWithdraw(amountA, address(AAtranche));
    vm.prank(userB); cdoEpoch.requestWithdraw(amountB, address(AAtranche));
    assertEq(strategy.epochNumber(), 0);
    assertEq(strategy.lastWithdrawRequest(userA), 0);

    // 3) Epoch ends; borrower repays only half of pendingBasis via
    //    stopEpochWithDuration(loss). Inside stop:
    //      strategy.deposit() bumps epochNumber to 1,
    //      collectWithdrawFunds(half) writes lossRecoveryPriceByEpoch[1] = 0.5e18.
    deal(defaultUnderlying, borrower, partialRepay);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(lossAmount, 0);
    assertEq(strategy.epochNumber(), 1);
    assertEq(strategy.lossRecoveryPriceByEpoch(1), RECOVERY_FULL / 2); // stored at 1
    assertEq(strategy.lossRecoveryPriceByEpoch(0), 0);                 // lookup key: empty

    // 4) userA claims: loss-adjusted path reads epoch 0 -> 0, funded path
    //    passes (epochNumber 1 > lastWithdrawRequest 0) and pays FULL basis.
    uint256 balBefore = underlying.balanceOf(userA);
    cdoEpoch.claimWithdrawRequest(userA);           // via CDO
    assertEq(underlying.balanceOf(userA) - balBefore, fullBasisA); // not half

    // 5) userB's claim reverts on insufficient strategy balance (insolvency),
    //    even though a 0.5 haircut should have left both funded.
    vm.expectRevert();
    cdoEpoch.claimWithdrawRequest(userB);
}
```

The assertion `lossRecoveryPriceByEpoch(0) == 0` while all epoch-0 receipts look up that exact key demonstrates the boundary off-by-one: the haircut is stored on the wrong side of the epoch increment, converting a designed 50% socialized loss into first-come full payout plus a stuck/insolvent remainder.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L259-294)
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L411-430)
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
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L596-617)
```text
  function deposit(uint256 _amount)
    external
    virtual
    override
    returns (uint256) {
    _onlyIdleCDO();
    if (_amount > 0) {
      underlyingToken.safeTransferFrom(msg.sender, address(this), _amount);
      _mint(msg.sender, _amount);
    }

    if (IIdleCDOEpochVariant(idleCDO).isEpochRunning()) {
      // deposit done on stopEpoch (before setting the var to false) so we reset the counter
      totEpochDeposits = 0;
      epochNumber += 1;
    } else {
      // deposit done between epochs so we increase the counter
      totEpochDeposits += _amount;
    }

    return _amount;
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
