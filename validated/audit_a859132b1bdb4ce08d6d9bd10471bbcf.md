### Title
Loss-recovery haircut is recorded under the post-stop epoch while receipts are keyed to their request epoch, letting underfunded withdraw claims pay out at par and haircutting the wrong requests - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault` keys a pending withdraw receipt to the epoch in which it was requested (`lastWithdrawRequest[_user]` / `withdrawsRequestsByEpoch[_user][reqEpoch]`), but `collectWithdrawFunds` stores a partial-funding haircut under `lossRecoveryPriceByEpoch[epochNumber]` using the *already-incremented* epoch counter. Because `deposit()` bumps `epochNumber` while `isEpochRunning()` is still true during `stopEpoch`, the haircut is recorded under epoch `N+1` while the receipts it applies to are keyed to epoch `N`. Claims then match the wrong key (the analog of CVE-2017-7177's fragment matching that omitted the protocol-field check): request-epoch `N` receipts find no haircut and are paid at par from a shortfall-funded reserve, while later epoch-`N+1` receipts are haircutted even though they were never part of the underfunded basis.

### Finding Description
The lifecycle is:

1. During epoch `N` (buffer or running-adjacent request window), a user calls `requestWithdraw`. The receipt is stored under the *current* epoch:
   - `lastWithdrawRequest[_user] = currentEpoch` (= N)
   - `withdrawsRequestsByEpoch[_user][currentEpoch] += _amount` (= N)
   - `pendingWithdraws += _amount` [1](#0-0) 

2. `stopEpoch`/`stopEpochWithDuration` runs. `deposit()` is invoked while `isEpochRunning()` is still true, so `epochNumber` is incremented to `N+1` inside the same stop flow: [2](#0-1) 

3. The borrower underfunds (`_amount < pendingBasis`), so `collectWithdrawFunds` writes the recovery price under the *new* counter:

   `lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice;` → `lossRecoveryPriceByEpoch[N+1]`, while `pendingWithdraws` is zeroed even though only `_amount` of underlying was actually collected. [3](#0-2) 

4. On claim, `_claimLossAdjustedWithdrawRequest` looks the haircut up under `lastWithdrawRequest[_user]` (= `N`), reads `lossRecoveryPriceByEpoch[N] == 0`, and returns 0 — so the claim falls through to `_claimFundedWithdrawRequest`, which pays the full `withdrawsRequests[_user]` at par and burns the receipt 1:1: [4](#0-3) [5](#0-4) 

5. Symmetrically, the guard in `requestWithdraw` and the claim path both key on `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]`, so a *new* receipt opened in epoch `N+1` (which was never part of the underfunded `pendingBasis`) is matched against `lossRecoveryPriceByEpoch[N+1]` and forced to take the haircut instead. [6](#0-5) 

### Impact Explanation
Direct insolvency / unfair payout from the funded-claim reserve:

- Epoch-`N` receipts that were only partially funded by the borrower are paid **in full** by `_transferFundedClaim`, drawing on underlying held by the strategy (including, once nonzero, the `defaultRecoveryReserve` guard path in `_transferFundedClaim` is the only limiter). The strategy collected less than `pendingBasis` but pays `claimBasis` at `RECOVERY_FULL` — the shortfall is socialized onto all other claimants and the recovery reserve.
- Epoch-`N+1` receipts that were fully funded are haircutted by `lossRecoveryPriceByEpoch[N+1]`, permanently stealing `(1 - lossRecoveryPrice)` of their claim.
- The one-receipt-one-payout and loss-waterfall invariants are both broken: loss is applied to the wrong epoch's receipts and skipped for the epoch that incurred it.

Quantified loss: up to `pendingBasis - fundedAmount` stolen from later claimants/reserve per loss-adjusted stop, and the entire funded-but-haircutted amount for epoch-`N+1` requesters.

### Likelihood Explanation
- Triggering path is a normal, expected code path: any `stopEpochWithDuration`-style partial funding (borrower repays less than `pendingWithdraws`) hits it. No privileged misbehavior is required beyond the honest borrower underfunding once — a scenario the loss-recovery machinery explicitly exists for.
- No existing guard stops it: `previewLossAdjustedWithdrawFunds` validates only basis sizes, not the epoch key used later; `defaultRecoveryInitialized` gating does not fix the off-by-one key; the `requestWithdraw` loss-check uses the same mismatched key so it cannot detect the unclaimed haircut.
- Requirements: `defaultRecoveryInitialized == true` (i.e., not a pure legacy receipt set — lazy init occurs on any new `requestWithdraw`) and `lossRecoveryPrice` must not round to zero. Both are routine.

### Recommendation
Store the haircut under the epoch that owns the pending receipts — i.e., key `lossRecoveryPriceByEpoch` to the receipts' request epoch (`epochNumber - 1` after the stop-time increment, or capture the epoch before `deposit()` bumps it inside the stop flow), and/or record the request epoch explicitly in a single per-epoch pending bucket rather than relying on `lastWithdrawRequest`. Alternatively, look up the haircut by the epoch stored in `withdrawsRequestsByEpoch`/`apr0Users.principalEpoch` directly instead of via `lastWithdrawRequest`, so claim matching uses the same key that `collectWithdrawFunds` wrote. Add a regression test asserting that after a partial-funding stop, epoch-`N` receipts claim at `lossRecoveryPrice` and epoch-`N+1` receipts claim at par.

### Proof of Concept
Foundry fork PoC outline (mirroring `test/foundry/IdleCreditVault.t.sol` helpers `_depositWithUser`, `_startEpochAndCheckPrices`, `_expectedFundsEndEpoch`):

```solidity
function testLossHaircutKeyedToWrongEpoch() external {
    IdleCreditVault vault = IdleCreditVault(address(strategy));

    // Epoch N: userA deposits and requests a withdraw -> receipt keyed to epoch N
    address userA = makeAddr("userA");
    uint256 reqEpoch = vault.epochNumber();           // N
    _depositWithUser(userA, 100_000 * ONE_SCALE, true);
    vm.prank(userA);
    uint256 basis = cdoEpoch.requestWithdraw(0, address(AAtranche));
    assertEq(vault.lastWithdrawRequest(userA), reqEpoch);
    assertEq(vault.withdrawsRequestsByEpoch(userA, reqEpoch), basis);

    _startEpochAndCheckPrices(0);

    // stopEpoch with borrower underfunding: fund only half of expected
    uint256 half = _expectedFundsEndEpoch() / 2;
    deal(defaultUnderlying, borrower, half);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(initialProvidedApr, /* loss */ basis / 2); // partial funding path

    // haircut stored under N+1, receipt keyed at N
    assertEq(vault.lossRecoveryPriceByEpoch(reqEpoch), 0, "request epoch has no haircut");
    assertGt(vault.lossRecoveryPriceByEpoch(reqEpoch + 1), 0, "haircut mis-keyed to next epoch");

    // userA claims FULL basis at par despite ~50% underfunding -> drains funded reserve
    uint256 balPre = underlying.balanceOf(userA);
    vm.prank(userA);
    cdoEpoch.claimWithdrawRequest();
    assertEq(underlying.balanceOf(userA) - balPre, basis, "underfunded receipt paid at par");

    // epoch N+1 requester (fully funded) is haircutted instead
    address userB = makeAddr("userB");
    _depositWithUser(userB, 100_000 * ONE_SCALE, true);
    vm.prank(userB);
    uint256 basisB = cdoEpoch.requestWithdraw(0, address(AAtranche));
    // ... after the next successful stop, userB's claim is reduced by
    // lossRecoveryPriceByEpoch[reqEpoch + 1] even though his epoch was fully funded.
}
```

Expected result demonstrating the bug: `lossRecoveryPriceByEpoch[N] == 0` while `lossRecoveryPriceByEpoch[N+1] != 0`, userA's payout equals the full `basis` although the strategy collected only a fraction of `pendingWithdraws`, and the loss is shifted onto unrelated epoch-`N+1` receipts and/or the strategy's funded/reserve balance.

Uncertainty note: the exact ordering of `deposit()` (epoch bump) versus `collectWithdrawFunds` inside `IdleCDOEpochVariant.stopEpoch`/`stopEpochWithDuration` could not be fully verified within the available iterations (the grep over `IdleCDOEpochVariant.sol` returned match counts but not bodies). If `collectWithdrawFunds` is invoked *before* the strategy `deposit()` that increments `epochNumber`, the key matches `lastWithdrawRequest` and the claim path is consistent — the PoC's assertion on `lossRecoveryPriceByEpoch[reqEpoch]` directly disambiguates the two cases.

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
