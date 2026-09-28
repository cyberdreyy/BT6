### Title
Stale `withdrawsRequests` aggregate lets a user evade the `stopEpochWithDuration` loss haircut by re-requesting a withdraw - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`CVE-2016-5280` is a use-after-free: an element is removed from `nsTextNodeDirectionalityMap` while a stale reference to it is still dereferenced. The analog here is stale-entry reuse across a cleared epoch boundary. When `collectWithdrawFunds` receives less than `pendingWithdraws`, the shortfall is recorded as `lossRecoveryPriceByEpoch[epochNumber]` and the epoch's receipts become claimable only at that haircutted price via `_claimLossAdjustedWithdrawRequest`, which is keyed by `lastWithdrawRequest[_user]` [1](#0-0) . But `requestWithdraw` overwrites `lastWithdrawRequest[_user]` with the new `currentEpoch` while leaving the old epoch's basis inside the aggregate `withdrawsRequests[_user]` [2](#0-1) . The "removed" per-epoch loss mapping is orphaned, and the stale aggregate is still paid out at par by `_claimFundedWithdrawRequest` [3](#0-2) .

### Finding Description
Normal epoch flow, `stopEpochWithDuration` loss mode:

1. Epoch N running. Attacker (KYC'd tranche holder) calls `requestWithdraw`, minting a strategy-token receipt and adding `_amount` to `withdrawsRequests[attacker]`, `withdrawsRequestsByEpoch[attacker][N]`, `pendingWithdraws`; `lastWithdrawRequest[attacker] = N`.
2. Manager calls `stopEpochWithDuration` with a loss; borrower funds less than `pendingWithdraws`. `collectWithdrawFunds` sets `lossRecoveryPriceByEpoch[N] = funded/pending < RECOVERY_FULL` and zeroes `pendingWithdraws`.
3. In the next buffer phase (`epochEndDate != 0`, epoch N+1), the attacker calls `requestWithdraw` again for any small amount. `lastWithdrawRequest[attacker] = N+1`; `withdrawsRequestsByEpoch[attacker][N+1]` and `withdrawsRequests[attacker]` grow.
4. Epoch N+1 stops fully funded. Attacker calls `claimWithdrawRequest`:
   - `_claimLossAdjustedWithdrawRequest` reads `lossRecoveryPriceByEpoch[lastWithdrawRequest]` = `lossRecoveryPriceByEpoch[N+1]` = 0 → returns 0. The epoch-N haircut entry is never reachable again.
   - `_claimFundedWithdrawRequest` passes the `epochNumber > lastWithdrawRequest` gate, `_settleApr0` no-ops, and pays `withdrawsRequests[attacker]` — which still includes the full epoch-N basis — at par via `_transferFundedClaim`, burning `normalAmount` receipt tokens that were minted 1:1 with that basis [4](#0-3) .

The invariant "a loss-adjusted receipt pays only `claimBasis * lossRecoveryPrice`" is broken: the haircutted epoch-N basis is paid twice over — once notionally at the haircut (never taken) and once at par through the stale aggregate. The same stale-pointer shape applies to `apr0Users` (`principalEpoch` matching) and to `instantWithdrawsRequestsByEpoch`, since `_clearWithdrawClaimForEpoch` also resets `lastWithdrawRequest` to 0 only when it equals the cleared epoch [5](#0-4) .

### Impact Explanation
Direct theft / insolvency. The attacker recovers `claimBasis_N * (1 - lossRecoveryPrice_N)` more underlying than funded. Those underlyings sit in the strategy reserved for other users' funded receipts, so honest claimants are left unbacked (last-claimant reverts on `_transferFundedClaim`/burn). Loss magnitude is bounded by `pendingWithdraws * (1 - recoveryPrice)` — i.e., the entire loss that was supposed to be socialized across pending receipts can be shifted onto other LPs.

### Likelihood Explanation
Requires only an unprivileged tranche holder and a `stopEpochWithDuration` partial-loss event (a legitimate, code-supported mode: `previewLossAdjustedWithdrawFunds`/`collectWithdrawFunds`). The re-request needs one extra epoch of waiting and tranche tokens for the small second request. No privileged cooperation, no oracle, no timing race beyond ordinary epoch sequencing. Any user with a pending receipt in a loss epoch can do it, and every such user has incentive to.

### Recommendation
Key the loss-adjusted claim by the receipt's own epoch rather than `lastWithdrawRequest`. Concretely: in `requestWithdraw`, if `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]] != 0`, either revert the new request or force-claim/migrate the old receipt into a separate haircutted bucket before overwriting `lastWithdrawRequest`. Alternatively, iterate `withdrawsRequestsByEpoch` for any epoch with a nonzero `lossRecoveryPriceByEpoch` inside `claimWithdrawRequest`, instead of relying on the mutable `lastWithdrawRequest` pointer.

### Proof of Concept
Foundry fork PoC sketch (extend `test/foundry/IdleCreditVault.t.sol` harness):

```solidity
// deposit, start epoch, request full withdraw as attacker
uint256 shares = idleCDO.depositAA(10000 * ONE_SCALE); // attacker
cdoEpoch.requestWithdraw(shares, address(AAtranche));

// stop epoch N with a realized loss so collectWithdrawFunds gets < pendingWithdraws
// (manager path -> _amount < pendingBasis -> lossRecoveryPriceByEpoch[N] = e.g. 50%)

// buffer of epoch N+1: attacker files a new tiny withdraw request
// overwrites lastWithdrawRequest[attacker] = N+1
uint256 dust = IERC20(AAtranche).balanceOf(attacker) / 100; // or re-deposit
cdoEpoch.requestWithdraw(dust, address(AAtranche));

// run epoch N+1 fully funded; stopEpoch bumps epochNumber > lastWithdrawRequest

// claim: loss path keyed on N+1 finds price 0, funded path pays aggregate at par
uint256 balBefore = underlying.balanceOf(attacker);
cdoEpoch.claimWithdrawRequest();
assertGt(underlying.balanceOf(attacker) - balBefore,
         /* epoch-N basis at par + dust */,
         "haircut evaded via stale lastWithdrawRequest pointer");
```

Expected: attacker's epoch-N basis is paid at 100% despite `lossRecoveryPriceByEpoch[N] < RECOVERY_FULL`, and subsequent honest claims of funded receipts revert for insufficient strategy underlyings.

### Citations

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L811-837)
```text
  function _clearWithdrawClaimForEpoch(address _user, uint256 _claimEpoch, bool _isClearingApr0) internal returns (uint256 claimBasis, uint256 burnAmount) {
    (claimBasis, burnAmount) = _withdrawClaimAmountsForEpoch(_user, _claimEpoch);
    if (claimBasis == 0) return (claimBasis, burnAmount);

    uint256 normalAmount = withdrawsRequestsByEpoch[_user][_claimEpoch];
    if (normalAmount != 0) {
      withdrawsRequestsByEpoch[_user][_claimEpoch] = 0;
      // The aggregate may also include older funded receipts; clear only this epoch's piece.
      withdrawsRequests[_user] -= normalAmount;
    }
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
    if (lastWithdrawRequest[_user] == _claimEpoch) {
      // The cleared epoch was the latest request marker. Any remaining normal/APR0 receipt
      // is older and already funded, so it can continue to the funded-claim path.
      lastWithdrawRequest[_user] = 0;
    }
  }
```
