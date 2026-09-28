### Title
Cross-mode withdraw requests let APR0 receipts escape the `lossRecoveryPriceByEpoch` haircut and drain funded reserves - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
CVE-2018-7740 is a resource-release accounting bug: a reservation map entry is freed under a crafted offset (`pgoff`), so the kernel releases memory that was never reserved. The analog in `IdleCreditVault` is the epoch-keyed receipt bookkeeping in `requestWithdraw`/`_claimLossAdjustedWithdrawRequest`/`_clearWithdrawClaimForEpoch`: a user who holds receipts under *two different accounting buckets and epoch keys* gets one bucket released under the wrong epoch index, so it is paid at par even though its basis was haircut in `collectWithdrawFunds`.

### Finding Description
`requestWithdraw` tags receipts with the *request* epoch:

- `lastWithdrawRequest[_user] = currentEpoch` and `withdrawsRequestsByEpoch[_user][currentEpoch] += _amount` for normal requests [1](#0-0) 
- APR0 requests instead go to `apr0Users[_user]` with a separate `principalEpoch` key [2](#0-1) 

When `stopEpochWithDuration` realizes a loss, `collectWithdrawFunds` stores `lossRecoveryPriceByEpoch[epochNumber]` and zeroes `pendingWithdraws`, funding only `pendingToFund = pendingBasis - pendingLoss` — i.e., **every** pending receipt (normal *and* APR0, since both incremented `pendingWithdraws`) is haircut pro rata [3](#0-2) .

The claim side, however, resolves the loss under a *single* key: `lastWithdrawRequest[_user]` [4](#0-3) . `_withdrawClaimAmountsForEpoch` only folds the APR0 principal into the haircutted `claimBasis` when `apr0Users[_user].principalEpoch == _claimEpoch` [5](#0-4) .

Sequence:

1. Epoch N buffer, `unscaledApr == 0`: attacker calls `cdoEpoch.requestWithdraw` → APR0 bucket, `principalEpoch = N`, `pendingWithdraws += P`.
2. `stopEpochWithDuration` ends epoch N with no loss; `epochNumber` increments via `deposit()` during the stop flow (test shows `epochNumber == 1` after first stop) [6](#0-5) . The attacker never claims, so `_settleApr0` never runs.
3. Manager sets a non-zero APR for epoch N+1. During the N+1 buffer the attacker requests again → `unscaledApr != 0`, so the normal path is taken: `withdrawsRequestsByEpoch[user][N+1]`, `lastWithdrawRequest[user] = N+1`. The pre-request guard only inspects `lossEpoch = lastWithdrawRequest` (epoch N at that point, `lossRecoveryPriceByEpoch[N] == 0`), so it passes [7](#0-6) .
4. `stopEpochWithDuration` ends epoch N+1 with `_lossAmount > 0` → `lossRecoveryPriceByEpoch[N+1] = p < 1`, `pendingWithdraws = 0`. The aggregate `pendingBasis` used for the haircut **included** the attacker's APR0 principal P.
5. Attacker calls `claimWithdrawRequest`: `_claimLossAdjustedWithdrawRequest` clears only `withdrawsRequestsByEpoch[N+1]` (apr0 skipped since `principalEpoch N != N+1`), pays `normalAmount * p`. Then `_claimFundedWithdrawRequest` → `_settleApr0` sees `reqEpoch N < epochNumber N+1`, settles `P` plus `apr0RateByEpoch[N]` interest, and `_transferFundedClaim` pays **P at par** [8](#0-7) .

The APR0 principal was counted in the lossy `pendingBasis` but is released under a different epoch key, exactly the "release under mismatched index" shape of the CVE. The pre-request guard (`principalEpoch == lossEpoch`) only compares against `lastWithdrawRequest`, so it cannot catch this cross-bucket, cross-epoch mix.

### Impact Explanation
The vault pulled only `pendingToFund` underlying from the borrower. The attacker's APR0 principal P is paid at par from the same funded pool that must also cover other users' haircutted receipts at price `p`. Net effect: the attacker steals `P * (1 - p)` worth of underlying, and the last loss-epoch claimants either receive less than `lossRecoveryPriceByEpoch` promises or their claims revert on insufficient balance — direct theft / insolvency proportional to the attacker's APR0 principal and the realized loss. With a 50% receipt haircut and a large APR0 position, the attacker recovers ~100% while honest receipt holders absorb the deficit.

### Likelihood Explanation
Requires only unprivileged actions: `requestWithdraw` in an APR0 epoch, withholding the claim, then a second `requestWithdraw` after the manager (honest) changes APR — a routine parameter update (`_setScaledApr` on every `stopEpoch`). The subsequent loss `stopEpochWithDuration` is also an honest manager action. No privileged collusion, no reentrancy, no donations needed. The only precondition is an APR transition away from 0 between two consecutive epochs, which is a normal operational event.

### Recommendation
Make the claim path epoch-complete rather than single-keyed:

- In `_claimLossAdjustedWithdrawRequest`, compute the loss-adjusted basis over **all** of the user's receipt buckets for every epoch with a non-zero `lossRecoveryPriceByEpoch`, not only `lastWithdrawRequest` — e.g., include `apr0Users.principal` whenever `lossRecoveryPriceByEpoch[principalEpoch] != 0`, and iterate/clear all `withdrawsRequestsByEpoch` entries the user holds.
- Symmetrically, tighten the `requestWithdraw` guard to scan both buckets against **any** epoch carrying a `lossRecoveryPriceByEpoch` entry (e.g., also check `apr0Users[_user].principalEpoch` and `apr0Users[_user].settledPrincipal` provenance), not just `lastWithdrawRequest`.
- Alternatively, force `_settleApr0`/claim of outstanding APR0 principal before allowing a mixed-mode re-request, so a user can never hold receipts split across the APR0 map and `withdrawsRequestsByEpoch` simultaneously.

### Proof of Concept
Foundry fork test sketch (extends `test/foundry/IdleCreditVault.t.sol` harness):

```solidity
function testApr0EscapesLossHaircut() external {
    // fees 0 for clean math
    _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, 0);

    // 1) deposit and start epoch N with APR = 0
    uint256 amountWei = 10000 * ONE_SCALE;
    idleCDO.depositAA(amountWei);
    _startEpochAndCheckPrices(0); // starts with unscaledApr == 0 (configure apr 0)

    // attacker APR0 request during buffer/epoch-N window
    address attacker = makeAddr('attacker');
    _depositWithUser(attacker, amountWei, true);
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(0, address(AAtranche)); // -> apr0Users bucket, principalEpoch = N

    // honest user makes normal request same epoch (shared pendingBasis)
    address victim = makeAddr('victim');
    _depositWithUser(victim, amountWei, true);
    vm.prank(victim);
    cdoEpoch.requestWithdraw(0, address(AAtranche));

    // 2) stop epoch N normally (no loss), borrower repays interest + pending
    _stopEpochAndCheckPrices(0, initialProvidedApr /* now nonzero apr for N+1 */, _expectedFundsEndEpoch());
    // attacker does NOT claim; apr0 principal stays open with principalEpoch = N

    // 3) buffer of epoch N+1, unscaledApr != 0 -> attacker normal request
    vm.prank(attacker);
    // attacker needs tranche balance again; redeposit then request
    _depositWithUser(attacker, amountWei, true);
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(0, address(AAtranche)); // withdrawsRequestsByEpoch[N+1], lastWithdrawRequest = N+1
    vm.prank(victim);
    // victim also re-requests or keeps old receipt

    // 4) stop epoch N+1 WITH loss (borrower underfunds / _lossAmount > 0)
    uint256 loss = strategy.pendingWithdraws() / 2; // 50% haircut on pending basis
    // fund borrower with pendingToFund + interest, approve, warp past epochEndDate
    ...
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(newApr, interest, duration, loss);
    uint256 p = strategy.lossRecoveryPriceByEpoch(strategy.epochNumber() /* pre-increment key */);

    // 5) attacker claims: gets normal receipt * p AND full apr0 principal at par
    uint256 balPre = underlying.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimWithdrawRequest();
    uint256 got = underlying.balanceOf(attacker) - balPre;
    // assert got > (normalBasis + apr0Principal) * p / 1e18  -> attacker escaped haircut
    // assert strategy balance now insufficient to pay victim at lossRecoveryPrice
}
```

Expected assertion: attacker's payout exceeds the haircut-implied amount by exactly `apr0Principal * (1 - p)`, and the vault's funded balance is short for remaining claimants — demonstrating theft/insolvency rather than a mere revert.

(Note: exact epoch-index wiring — whether `lossRecoveryPriceByEpoch` is keyed pre- or post-increment — should be confirmed in the PoC; `collectWithdrawFunds` runs before the `deposit()` that bumps `epochNumber`, so the loss epoch equals the request epoch, which is what the exploit relies on.)

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L282-294)
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L411-425)
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
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L567-577)
```text
  function _requestWithdrawApr0(uint256 _amount, address _user) internal {
    // Settle any previous APR0 request first, then start/update current epoch bucket.
    _settleApr0(_user);
    Apr0UserData storage _apr0User = apr0Users[_user];
    if (_apr0User.principal == 0) {
      _apr0User.principalEpoch = epochNumber;
    }
    _apr0User.principal += _amount;
    // Epoch-level APR0 principal used only to compute stopEpoch APR0 pro-rata interest.
    apr0TotalPrincipal += _amount;
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L862-880)
```text
  function _withdrawClaimAmountsForEpoch(address _user, uint256 _claimEpoch) internal view returns (uint256 claimBasis, uint256 burnAmount) {
    // We calculate what the user is owed in underlyings (claimBasis) and how many strategy tokens to burn (burnAmount).
    // the amount owned is the sum of the normal withdraw request and the APR0 principal and interest if any.
    uint256 normalAmount = withdrawsRequestsByEpoch[_user][_claimEpoch];
    Apr0UserData storage _apr0User = apr0Users[_user];
    uint256 apr0PrincipalAmount;
    uint256 apr0InterestAmount;
    uint256 principal = _apr0User.principal;
    uint256 principalEpoch = _apr0User.principalEpoch;
    if (principal != 0 && principalEpoch == _claimEpoch) {
      apr0PrincipalAmount = principal;
      uint256 rate = apr0RateByEpoch[principalEpoch];
      if (rate != 0) {
        // APR0 interest increases the user's default claim basis, but not the receipt burn amount.
        apr0InterestAmount += (principal * rate) / RECOVERY_FULL;
      }
    }
    claimBasis = normalAmount + apr0PrincipalAmount + apr0InterestAmount;
    burnAmount = normalAmount + apr0PrincipalAmount;
```

**File:** test/foundry/IdleCreditVault.t.sol (L5755-5757)
```text
    assertEq(IdleCreditVault(address(strategy)).getApr(), _scaleAprWithBuffer(initialProvidedApr), 'apr is wrong');
    assertEq(IdleCreditVault(address(strategy)).epochNumber(), 1, 'epochNumber is wrong');
    assertEq(_vault.isEpochRunning(), false, 'isEpochRunning is wrong');
```
