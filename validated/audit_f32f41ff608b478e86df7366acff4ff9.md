### Title
Loss-adjusted withdrawal receipts are paid at par once a newer request overwrites `lastWithdrawRequest` — `lossRecoveryPriceByEpoch` lookup uses a stale key - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
When `stopEpochWithDuration` realizes a loss, `collectWithdrawFunds` funds pending receipts only partially and stores the haircut as `lossRecoveryPriceByEpoch[epochNumber]` [1](#0-0) . On claim, `_claimLossAdjustedWithdrawRequest` looks up the recovery price using only `lastWithdrawRequest[_user]`, i.e. the user's *most recent* request epoch [2](#0-1) . Because `requestWithdraw` allows stacking a second request before claiming the first, a newer request in a later (non-loss) epoch overwrites `lastWithdrawRequest`, the loss haircut is never applied to the old receipt, and `_claimFundedWithdrawRequest` pays the full aggregate `withdrawsRequests[_user]` at par [3](#0-2) . This mirrors the RAND_poll/fork() bug class: a value (`lossRecoveryPriceByEpoch[lastWithdrawRequest]`) is consumed under a stale/overwritten context key, so the "post-fork" state silently reuses pre-change semantics.

### Finding Description
- `requestWithdraw` records `withdrawsRequestsByEpoch[user][epoch] += amount`, increments the aggregate `withdrawsRequests[user]`, and sets `lastWithdrawRequest[user] = currentEpoch` [4](#0-3) .
- On a lossy stop, `pendingWithdraws` is zeroed and `lossRecoveryPriceByEpoch[epochNumber] = funded * RECOVERY_FULL / pendingBasis`; the strategy only receives the reduced amount [1](#0-0) .
- `claimWithdrawRequest` → `_claimLossAdjustedWithdrawRequest` only checks `lossRecoveryPriceByEpoch[lastWithdrawRequest[user]]`; if that slot is zero it returns 0 and `_claimFundedWithdrawRequest` pays `withdrawsRequests[user]` in full [5](#0-4) .
- So a receipt whose epoch *was* haircut is paid at par whenever any subsequent request exists in a non-loss epoch. No guard (`_clearWithdrawClaimForEpoch`, `_settleApr0`) re-checks older epochs for a stored loss price.

### Impact Explanation
Direct theft / insolvency. The loss-adjusted funding pulled from the borrower was `pendingBasis * price`, but the attacker withdraws the full `pendingBasis`, extracting `(1 - lossRecoveryPrice) * amount1` of underfunded value. The shortfall is socialized onto other funded receipt holders (the strategy's cash is drained first-come-first-served) or onto active LPs, breaking the loss-waterfall invariant that pending receipts share losses pro rata. With a 50% loss price, the attacker doubles that portion of the receipt.

### Likelihood Explanation
Fully attacker-driven sequencing: an unprivileged tranche holder places a withdraw request, waits for a lossy `stopEpochWithDuration` (an honest manager action during borrower underpayment), then submits any size second request in the next buffer and claims after one more epoch. Only requirements: the pool uses `stopEpochWithDuration` with `_lossAmount > 0` and `defaultRecoveryInitialized` is true (lazy-initialized on first request). No privileged misbehavior required.

### Recommendation
In `_claimLossAdjustedWithdrawRequest` / `claimWithdrawRequest`, iterate or track all request epochs with a non-zero `lossRecoveryPriceByEpoch` per user (e.g., keep per-epoch receipts and check each, or store a per-user list/bitmap of loss epochs), instead of keying only on `lastWithdrawRequest`. Alternatively, apply the haircut eagerly at request time by folding the epoch's recovery price into `withdrawsRequestsByEpoch` when a subsequent request is made after a loss epoch, so the aggregate `withdrawsRequests` can never contain an un-haircut basis.

### Proof of Concept
Foundry fork PoC outline (pattern follows `test/foundry/IdleCreditVault.t.sol` helpers `_depositWithUser`, `_startEpochAndCheckPrices`, `_stopEpochAndCheckPrices`):

```solidity
function testLossHaircutBypassedBySecondRequest() external {
  address attacker = makeAddr('attacker');
  _depositWithUser(attacker, 10_000 * ONE_SCALE, true); // AA tranches

  // Buffer of epoch 0: attacker requests withdraw of half
  vm.prank(attacker);
  uint256 req1 = cdoEpoch.requestWithdraw(IERC20(AAtranche).balanceOf(attacker) / 2, address(AAtranche));

  // Epoch 0 runs; stop with a realized loss -> lossRecoveryPriceByEpoch[0] < RECOVERY_FULL
  _startEpochAndCheckPrices(0);
  vm.warp(cdoEpoch.epochEndDate() + 1);
  uint256 pending = IdleCreditVault(address(strategy)).pendingWithdraws();
  uint256 loss = pending / 2; // 50% haircut on pending receipts
  deal(defaultUnderlying, borrower, pending - loss + cdoEpoch.expectedEpochInterest());
  vm.prank(manager);
  cdoEpoch.stopEpochWithDuration(epochDuration, initialProvidedApr, loss);
  assertGt(IdleCreditVault(address(strategy)).lossRecoveryPriceByEpoch(0), 0);

  // Buffer of epoch 1: attacker files a second request -> lastWithdrawRequest = 1
  vm.prank(attacker);
  cdoEpoch.requestWithdraw(IERC20(AAtranche).balanceOf(attacker) / 2, address(AAtranche));

  // Epoch 1 runs and stops cleanly (no loss)
  _startEpochAndCheckPrices(1);
  vm.warp(cdoEpoch.epochEndDate() + 1);
  uint256 expectedInterest = cdoEpoch.expectedEpochInterest();
  deal(defaultUnderlying, borrower, expectedInterest + IdleCreditVault(address(strategy)).pendingWithdraws());
  vm.prank(manager);
  cdoEpoch.stopEpoch(0, expectedInterest);

  // Attacker claims: loss price lookup hits epoch 1 (0) -> funded path pays BOTH receipts at par
  uint256 balPre = underlying.balanceOf(attacker);
  vm.prank(attacker);
  cdoEpoch.claimWithdrawRequest();
  uint256 paid = underlying.balanceOf(attacker) - balPre;

  // Expected honest payout for req1 was req1 * lossRecoveryPrice / RECOVERY_FULL
  assertGt(paid, req1 / 2, 'haircut bypassed: epoch-0 loss receipt paid at par');
}
```

The `req1` portion is paid at 100% instead of the stored ~50% recovery price, withdrawing underfunded underlying from `IdleCreditVault` at the expense of other claimants.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L279-294)
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
    }
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
