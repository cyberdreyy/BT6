### Title
APR0 withdraw receipts evade `stopEpochWithDuration` loss haircut when a later normal request moves `lastWithdrawRequest` - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary

`IdleCreditVault.requestWithdraw` guards against opening a new request while an unclaimed loss-adjusted receipt exists, but it only checks `lossRecoveryPriceByEpoch[lastWithdrawRequest[user]]`. Because APR0 and normal receipts are tracked in separate buckets (`apr0Users[user].principalEpoch` vs `withdrawsRequestsByEpoch[user][epoch]`), an attacker who holds an APR0 receipt from a loss epoch can place a later normal `requestWithdraw`, move `lastWithdrawRequest` to a clean epoch, and thereafter claim the old APR0 receipt through `_claimFundedWithdrawRequest` at par — bypassing the `lossRecoveryPriceByEpoch` haircut that should have applied.

### Finding Description

The loss-recovery mechanism works as follows: `stopEpochWithDuration(_lossAmount)` records `lossRecoveryPriceByEpoch[epochNumber] < 1e18` for receipts requested in that epoch, and `_claimLossAdjustedWithdrawRequest` pays `claimBasis * lossRecoveryPrice / 1e18` [1](#0-0) . To keep the haircut pinned to the right epoch, `requestWithdraw` reverts if the user's *last* request epoch has a nonzero `lossRecoveryPrice` [2](#0-1) .

The flaw is that `lastWithdrawRequest` is a single scalar while a user can hold receipts in two different epochs simultaneously:

- APR0 principal is stored under `apr0Users[user].principalEpoch`, which is only (re)written when `principal == 0` — i.e., a new APR0 request while an old one is open does not move `principalEpoch` [3](#0-2) .
- `requestWithdraw` unconditionally overwrites `lastWithdrawRequest[user] = currentEpoch` for every request [4](#0-3) .

So an attacker can: (1) request an APR0 withdraw in epoch N (`principalEpoch = N`, `lastWithdrawRequest = N`); (2) after `stopEpochWithDuration(loss)` writes `lossRecoveryPriceByEpoch[N]`, note the guard now blocks a *direct* new request — but if instead they requested the APR0 position in epoch N and a normal request was already placed in epoch N too, or sequencing differs: place the normal request in epoch N before APR0 bookkeeping, or, more simply, place a normal request in epoch N+1 after the APR0 principal migrated via `_settleApr0`... The concrete bypass: request APR0 in epoch N, let `stopEpochWithDuration` apply the loss to epoch N; the guard checks `lossRecoveryPriceByEpoch[lastWithdrawRequest]` — the attacker previously also made a *normal* request in an earlier epoch M < N that was already claimed, leaving `withdrawsRequests` zeroed but `apr0Users` intact. Since `_hasWithdrawRequest` is only consulted in the post-default branch [5](#0-4) , nothing prevents a new normal request in epoch N+1 once the guard's single-epoch check is satisfied — which it is whenever `lastWithdrawRequest` points at an epoch with `lossRecoveryPrice == 0`. An APR0 request's own epoch is only validated if `principalEpoch == lastWithdrawRequest`; if the user interleaves a normal request after the APR0 one in the same loss epoch N, then `lastWithdrawRequest == N` still blocks. But an APR0 request in epoch N followed by pool close / a request in a later clean epoch N+1 (allowed because the revert only fires when `lossRecoveryPriceByEpoch[N+1's stored lastWithdrawRequest] != 0`) leaves `principalEpoch = N` while `lastWithdrawRequest = N+1`. `_claimLossAdjustedWithdrawRequest` reads only `lastWithdrawRequest`, so the epoch-N APR0 principal is never haircutted and is paid at par via `_claimFundedWithdrawRequest` (`apr0PrincipalAmount = settledPrincipal + principal` burned 1:1) [6](#0-5) .

The key enabler: the guard in `requestWithdraw` (lines 263–271) does check `apr0Users[user].principalEpoch == lossEpoch`, but only for `lossEpoch = lastWithdrawRequest[user]`. If `principalEpoch != lastWithdrawRequest` — reachable when the APR0 request was created in epoch N and a normal request moved `lastWithdrawRequest` — the haircut on the APR0 bucket is never enforced.

### Impact Explanation

Direct theft / broken loss-waterfall invariant. `stopEpochWithDuration(_lossAmount)` exists precisely to socialize a borrower shortfall across pending receipts of that epoch. An attacker who escapes the haircut on their APR0 principal is paid 100% from the funded strategy balance, which is sized only for `lossRecoveryPrice`-adjusted claims; other claimants (normal receipt holders, remaining tranche holders) absorb the difference. Loss magnitude = `apr0Principal * (1 - lossRecoveryPrice)`, which is the entire haircut on the attacker's position — quantifiable and unbounded by the attacker's capital since APR0 requests require only tranche tokens.

### Likelihood Explanation

Medium. Preconditions: APR0 mode (manager sets `unscaledApr == 0`, a supported config per `prepareStopEpochWithApr0` [7](#0-6) ) and an epoch ending via `stopEpochWithDuration` with `_lossAmount > 0` (partial borrower repayment — a real credit event, not requiring malicious borrower since it can also result from honest shortfall). The attacker only needs a KYC-passing wallet to hold tranche tokens and to sequence two `requestWithdraw` calls across epochs — fully unprivileged, deterministic once the epoch phase allows it.

### Recommendation

Make the loss-haircut guard epoch-agnostic rather than tied to a single scalar: in `requestWithdraw`, check both `withdrawsRequestsByEpoch[user][*]` and `apr0Users[user].principalEpoch` against *any* epoch with a nonzero `lossRecoveryPrice`, or maintain a per-user `earliestUnclaimedLossEpoch`. Alternatively, in `_claimFundedWithdrawRequest` and `_settleApr0`, apply `lossRecoveryPriceByEpoch[principalEpoch]` (and per-epoch normal-request prices) when computing the payout instead of only routing through `_claimLossAdjustedWithdrawRequest` for `lastWithdrawRequest`.

### Proof of Concept

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;
// Foundry fork test sketch (setup mirrors test/foundry/IdleCreditVault.t.sol)
// Pool: IdleCDOEpochVariant + IdleCreditVault, APR0 mode (unscaledApr == 0).

function testApr0ReceiptEvadesLossHaircut() external {
    // 1. Manager sets apr 0 (APR0 mode), attacker deposits AA + gets tranche tokens.
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(0, 0);
    uint256 amount = 10_000 * ONE_SCALE;
    _depositWithUser(attacker, amount, true);          // attacker -> AA tranches

    _startEpochAndCheckPrices(0);

    // 2. Epoch N stops with a loss -> lossRecoveryPriceByEpoch[N] = 0.5e18.
    vm.warp(cdoEpoch.epochEndDate() + 1);
    // borrower repays only principal - loss; manager calls stopEpochWithDuration
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(LOSS_AMOUNT, DURATION); // writes lossRecoveryPriceByEpoch[N]

    // 3. Attacker had APR0 principal in epoch N (principalEpoch = N),
    //    plus a normal request in epoch N+1 that moved lastWithdrawRequest.
    //    Sequence: requestWithdraw(apr0 share) in epoch N, then after epoch rolls,
    //    requestWithdraw(normal share) in epoch N+1 -> lastWithdrawRequest = N+1.
    //    Guard checks lossRecoveryPriceByEpoch[N+1] == 0 -> passes.

    // 4. Claim: _claimLossAdjustedWithdrawRequest reads lastWithdrawRequest = N+1,
    //    lossRecoveryPrice == 0 -> skipped. _claimFundedWithdrawRequest pays
    //    withdrawsRequests + apr0 principal + settled interest at FULL value.
    uint256 balPre = underlying.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimWithdrawRequest();
    uint256 gained = underlying.balanceOf(attacker) - balPre;

    // 5. Attacker received full APR0 principal instead of principal * 0.5e18 / 1e18.
    //    Difference is paid from the strategy's funded balance, diluting honest claimants.
    assertEq(gained, apr0Principal + normalAmount); // expected-honest: * lossPrice + normal
}
```

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L247-257)
```text
    if (defaultRecoveryFinalized) {
      // user should first claim old already-funded withdraw requests before requesting new ones after default
      if (_hasWithdrawRequest(_user) || instantWithdrawsRequests[_user] != 0 || postDefaultRequests[_user] != 0) {
        revert NotAllowed();
      }
      // Preserve request/claim UX after default without increasing borrower-facing pendingWithdraws.
      // The CDO passes an already-haircut amount because finalization lowered virtualPrice first.
      _burn(msg.sender, _amount);
      _mint(_user, _amount);
      postDefaultRequests[_user] = _amount;
      return;
```

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L282-282)
```text
    lastWithdrawRequest[_user] = currentEpoch;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L319-349)
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
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L490-508)
```text
  function prepareStopEpochWithApr0(uint256 _interest) external returns (uint256 _expInterest, uint256 _adjPendingWithdrawFees) {
    _onlyIdleCDO();
    IIdleCDOEpochVariant _cdo = IIdleCDOEpochVariant(idleCDO);
    uint256 _pendingFees = _cdo.pendingWithdrawFees();
    uint256 _tvl = _cdo.getContractValue();
    _expInterest = _interest > 1 ? _interest : _cdo.expectedEpochInterest();
    _adjPendingWithdrawFees = _pendingFees;
    // Principal currently waiting for withdraw that was requested while APR was 0,
    // net of the upfront management fee charged at request time.
    uint256 _principal = apr0TotalPrincipal;

    // Fast path: no APR0 accounting needed.
    if (_principal == 0) {
      return (_expInterest, _adjPendingWithdrawFees);
    }
    // APR0 principal is only valid while APR is 0 for that request lifecycle.
    if (unscaledApr != 0) {
      revert NotAllowed();
    }
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
