### Title
Stale-epoch APR0 withdraw receipt escapes default haircut and is paid at par after `finalizeDefaultRecovery` - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`IdleCreditVault._claimDefaultedWithdrawRequest` only clears receipts whose per-epoch records match `defaultRecoveryEpoch` (the epoch in which default is finalized). An APR0 receipt opened in an earlier epoch (`apr0Users[user].principalEpoch < defaultRecoveryEpoch`) is included in `pendingWithdraws`/`totalBasis` when the recovery price is computed, but is never routed through the haircut claim. After finalization it falls through to `_claimFundedWithdrawRequest`, which settles it via `_settleApr0` and pays `settledPrincipal + settledInterest` **at par** from `_transferFundedClaim` — while the recovery price assumed it would only be paid `defaultRecoveryPrice` cents on the dollar. This is the vault analog of the CVE-2018-8807 use-after-free: a receipt "freed" from the default-epoch clearing path is still live in `apr0Users` and gets paid a second, fuller time.

### Finding Description
When a user calls `requestWithdraw` while `unscaledApr == 0`, the request is recorded in `apr0Users[_user].principal`/`principalEpoch` (not in `withdrawsRequestsByEpoch`) and added to `pendingWithdraws` [1](#0-0) . `prepareStopEpochWithApr0` may add APR0 net interest to `pendingWithdraws` and zeroes `apr0TotalPrincipal`, but `apr0Users[_user].principalEpoch` keeps the original request epoch [2](#0-1) .

On default, `finalizeDefaultRecovery` includes the full `pendingWithdraws` (which still contains the stale APR0 basis) in `totalBasis` and sets `defaultRecoveryEpoch = epochNumber` [3](#0-2) .

When the user later calls `claimWithdrawRequest`, `_claimDefaultedWithdrawRequest` calls `_clearWithdrawClaimForEpoch(_user, defaultEpoch, true)`, but `_withdrawClaimAmountsForEpoch` only counts APR0 principal when `principalEpoch == _claimEpoch` [4](#0-3) . A receipt from epoch `N-1` therefore yields `claimBasis == 0`, `pendingWithdraws` is never decremented, and `lastWithdrawRequest` is untouched.

Execution then reaches `_claimFundedWithdrawRequest`: the gate `epochNumber <= lastWithdrawRequest[_user]` passes because `epochNumber` (N) > `principalEpoch`/`lastWithdrawRequest` (N-1) [5](#0-4) . `_settleApr0` moves the principal to `settledPrincipal` and credits `apr0RateByEpoch` interest [6](#0-5) , and the user is paid `settledPrincipal + settledInterest` at par via `_transferFundedClaim`, which only protects `defaultRecoveryReserve`, not the haircut [7](#0-6) .

### Impact Explanation
The receipt was part of `totalBasis` when `defaultRecoveryPrice` was computed, so the recovery pool was sized assuming it would be paid `basis * defaultRecoveryPrice / RECOVERY_FULL`. Instead it is paid 1:1 from strategy-held underlying. Every such claim over-pays `(1 - recoveryPrice) * basis`, directly draining either the default recovery reserve's backing buffer or funds earmarked for other defaulted/post-default claimants, leaving later claimants undercollateralized (insolvency / permanent loss for honest users). Additionally `pendingWithdraws` is left permanently overstated, corrupting any later loss-split accounting in `previewLossAdjustedWithdrawFunds`/`collectWithdrawFunds`.

### Likelihood Explanation
Requires an APR0-mode vault (`unscaledApr == 0`), a user who requested withdrawal in an epoch strictly before the default epoch, and a borrower default with `recoveryPrice < RECOVERY_FULL`. All are reachable by an ordinary KYC-passed lender with no privileged cooperation: request during epoch N-1, let the epoch roll to N, borrower defaults, `finalizeDefaultRecovery` executes, then claim. Withdrawing attackers are the intended beneficiaries of the bypass, so the attack is self-serve once the vault is in the defaulted phase.

### Recommendation
In `_claimDefaultedWithdrawRequest` (or `_withdrawClaimAmountsForEpoch`/`_clearWithdrawClaimForEpoch`), treat the default claim as covering **all** outstanding receipt basis for the user, not just `principalEpoch == defaultRecoveryEpoch`: include `apr0User.principal`/`settledPrincipal`/`settledInterest` from any earlier unfinalized epoch, decrement `pendingWithdraws` accordingly, and clear `lastWithdrawRequest`/the APR0 struct. Alternatively, in `claimWithdrawRequest`, when `defaultRecoveryFinalized` route any remaining `withdrawsRequests`/`apr0Users` balance through `defaultRecoveryPrice` instead of the par-funded path.

### Proof of Concept
Foundry fork PoC sketch (mirroring `test/foundry/IdleCreditVault.t.sol` helpers `_startEpochAndCheckPrices`, `_stopEpochAndCheckPrices`, default tests):

```solidity
function testApr0StaleEpochEscapesDefaultHaircut() external {
    _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, cdoEpoch.managementFee());
    vm.prank(owner);
    cdoEpoch.setIsAYSActive(false);
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(0, 0); // APR0 mode

    uint256 amountWei = 10000 * ONE_SCALE;
    idleCDO.depositAA(amountWei);

    _startEpochAndCheckPrices(0);              // epoch 0 running
    _stopEpochAndCheckPrices(0, 0, _expectedFundsEndEpoch()); // -> epochNumber 1, apr0 still 0

    // user requests full withdraw during epoch 1 (principalEpoch = 1)
    uint256 mintedAA = IERC20Detailed(address(AAtranche)).balanceOf(address(this));
    cdoEpoch.requestWithdraw(mintedAA, address(AAtranche));

    _startEpochAndCheckPrices(1);              // epoch 1 running
    _stopEpochAndCheckPrices(1, 0, _expectedFundsEndEpoch()); // -> epochNumber 2, apr0 bucket closed

    // borrower defaults during epoch 2 (before user's receipt is claimed/settled)
    _triggerBorrowerDefault();                 // defaulted() == true
    _finalizeDefaultRecoveryWithPartialRecovery(); // e.g. recoveryPrice = 50% of RECOVERY_FULL
    // strategy.defaultRecoveryEpoch() == 2 != principalEpoch (1)

    uint256 balPre = IERC20Detailed(defaultUnderlying).balanceOf(address(this));
    cdoEpoch.claimWithdrawRequest();           // succeeds: epochNumber(2) > lastWithdrawRequest(1)
    uint256 paid = IERC20Detailed(defaultUnderlying).balanceOf(address(this)) - balPre;

    // Expected haircut: requested * 50%. Actual: paid == requested (par) -> overpaid 50%.
    assertGt(paid, requested * strategy.defaultRecoveryPrice() / strategy.RECOVERY_FULL());
}
```

Key assertions: `strategy.defaultRecoveryEpoch() != apr0Users(user).principalEpoch` at finalization, `pendingWithdraws` unchanged after the claim, and `paid` equals par rather than `basis * defaultRecoveryPrice / RECOVERY_FULL`.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L279-286)
```text
      pendingWithdraws += _amount;
    }
    // save the epoch of the last withdraw request (buffer + epochDuration is 1 epoch)
    lastWithdrawRequest[_user] = currentEpoch;
    // APR=0 requests keep separate accounting and settle interest at stopEpoch.
    // `_amount` here is the post-management-fee principal bucket for that flow.
    if (unscaledApr == 0 && !isClosed) {
      _requestWithdrawApr0(_amount, _user);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L326-328)
```text
    if (IIdleCDOEpochVariant(idleCDO).epochEndDate() != 0 && (epochNumber <= lastWithdrawRequest[_user])) {
      revert NotAllowed();
    }
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L533-540)
```text
    if (_apr0NetInterest != 0) {
      // Funds owed to withdraw requesters increase by APR0 net interest.
      pendingWithdraws += _apr0NetInterest;
      // Save per-epoch net rate; each APR0 request accrues exactly once on its request epoch.
      apr0RateByEpoch[epochNumber] = (_apr0NetInterest * 1e18) / _principal;
    }
    // Close current APR0 bucket so it cannot accrue again on later stopEpoch calls.
    apr0TotalPrincipal = 0;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L545-565)
```text
  function _settleApr0(address _user) internal {
    Apr0UserData storage _apr0User = apr0Users[_user];
    uint256 _principal = _apr0User.principal;
    if (_principal == 0) {
      return;
    }
    uint256 _reqEpoch = _apr0User.principalEpoch;
    // Settle only after stopEpoch bumped epochNumber (ie after one full wait epoch).
    if (_reqEpoch >= epochNumber) {
      return;
    }
    // Move principal from "open APR0 bucket" to "settled bucket" (same principal, not duplicated).
    _apr0User.settledPrincipal += _principal;
    uint256 _rate = apr0RateByEpoch[_reqEpoch];
    if (_rate != 0) {
      // Convert per-epoch rate to claimable underlying interest.
      _apr0User.settledInterest += (_principal * _rate) / 1e18;
    }
    _apr0User.principal = 0;
    _apr0User.principalEpoch = 0;
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L679-693)
```text
    uint256 pendingBasis = defaultPendingClaimBasis();
    uint256 totalBasis = activeBasis + pendingBasis;
    if (totalBasis == 0) revert NotAllowed();

    // Some recovery funds may already be in this strategy: partially prefunded instant requests
    // and borrower-send funds that failed at epoch start. Count both without pulling them again.
    uint256 prefundedReserve = _defaultPrefundedInstantReserve();
    uint256 reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve;
    // Recovery can be above par if the recovered funds exceed the computed basis.
    uint256 recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;

    defaultRecoveryFinalized = true;
    defaultRecoveryReserve = reserveAmount;
    defaultRecoveryPrice = recoveryPrice;
    defaultRecoveryEpoch = epochNumber;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L865-880)
```text
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
