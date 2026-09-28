### Title
Loss-adjusted withdraw receipts escape the haircut when the user re-requests in a later epoch - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary

The seqiv bug class is a pending-request lifetime error: the code only handled one deferred-completion signal (`EINPROGRESS`), so a backlogged request (`EBUSY`) had its data freed while still in flight — a use-after-free. The analog in `IdleCreditVault` is the withdraw-receipt state machine: loss-adjusted claims are keyed solely off `lastWithdrawRequest[_user]`, a single-slot marker that is overwritten by every new `requestWithdraw`. A user holding a haircut receipt from a loss epoch can issue a second withdraw request in a later healthy epoch, which "frees" (orphans) the pointer to the loss-adjusted receipt. The claim path then treats the still-recorded `withdrawsRequestsByEpoch` amount as a normal funded receipt and pays it at par, escaping the crystallized loss and underpaying everyone else in that loss epoch.

### Finding Description

`stopEpochWithDuration` can realize a loss and call `collectWithdrawFunds` with `_amount < pendingWithdraws`. That branch zeroes `pendingWithdraws` and stores `lossRecoveryPriceByEpoch[epochNumber]` — a recovery ratio all pending receipts of that epoch must claim at [1](#0-0) .

On claim, `claimWithdrawRequest` tries `_claimLossAdjustedWithdrawRequest` before `_claimFundedWithdrawRequest`. The loss path does `lossEpoch = lastWithdrawRequest[_user]` and looks up `lossRecoveryPriceByEpoch[lossEpoch]` [2](#0-1) . If the lookup misses it returns early and the funded path pays the full `withdrawsRequests[_user]` at par [3](#0-2) .

The problem: `requestWithdraw` unconditionally sets `lastWithdrawRequest[_user] = currentEpoch` and accumulates `withdrawsRequests[_user]` and `withdrawsRequestsByEpoch[_user][currentEpoch]` [4](#0-3) . There is no per-user record of which past epochs carry a `lossRecoveryPriceByEpoch` claim. Once `lastWithdrawRequest` moves to a newer epoch, the loss-epoch receipt can never reach `_claimLossAdjustedWithdrawRequest` again (the marker is only reset by `_clearWithdrawClaimForEpoch`, which is only invoked *for* `lastWithdrawRequest` or the default epoch), yet its `withdrawsRequests`/`withdrawsRequestsByEpoch` balance is still fully counted by `_claimFundedWithdrawRequest`.

Attack sequence:

1. Epoch E (running): attacker calls `requestWithdraw` via `IdleCDOEpochVariant.requestWithdraw` (they are a KYC'd tranche holder). Receipt recorded under epoch E.
2. Epoch E ends with a realized loss: manager calls `stopEpochWithDuration(_lossAmount)`, borrower funds `pendingToFund < pendingBasis`. `lossRecoveryPriceByEpoch[E]` is set to e.g. 90%.
3. Epoch E+1 buffer/running: attacker calls `requestWithdraw` again (any small amount). `lastWithdrawRequest[attacker] = E+1`; `withdrawsRequests[attacker]` now contains both receipts.
4. Epoch E+1 ends normally, fully funded. Attacker calls `claimWithdrawRequest`:
   - `_claimLossAdjustedWithdrawRequest`: `lossRecoveryPriceByEpoch[E+1] == 0` → returns 0.
   - `_claimFundedWithdrawRequest`: `epochNumber > E+1` → passes; pays `withdrawsRequests[attacker]` (loss-epoch + new-epoch receipts) at 100%.
5. Attacker received their loss-epoch receipt at par while every other epoch-E redeemer is capped at the recovery price. The strategy's funded reserve is drained by the haircut amount `withdrawsRequestsByEpoch[attacker][E] * (1 - lossRecoveryPrice)`, leaving later claimants of epoch E (or the default reserve) short.

No guard stops this: `_checkOnlyOwnerOrManager` is not on this path, `_onlyIdleCDO` is satisfied (requests go through the CDO), the epoch gating in `_claimFundedWithdrawRequest` passes once E+1 ends, and `defaultRecoveryFinalized` is not required for the loss path.

### Impact Explanation

Direct theft with a quantified loss: the attacker extracts `R_E * (RECOVERY_FULL - lossRecoveryPriceByEpoch[E]) / RECOVERY_FULL` more underlying than entitled, where `R_E` is their epoch-E receipt. This amount comes out of the strategy's funded claim reserve, so honest epoch-E claimants (or the reserve backing default recovery) are left permanently underfunded — broken "one receipt, one payout" and loss-socialization invariants. The haircut applied by `stopEpochWithDuration` is selectively bypassed per-user.

### Likelihood Explanation

Requires only two ordinary `requestWithdraw` calls by an unprivileged tranche holder across two epochs, plus a `stopEpochWithDuration` loss epoch (a normal protocol operation executed by the honest manager). No privileged collusion, no reentrancy, no timing luck beyond the loss epoch occurring while the attacker has a pending receipt. The loss does not need to be caused by the attacker; any partial-loss stopEpoch suffices, including small ones. Likelihood is moderate-high in any pool that ever uses the loss-adjusted stop flow.

### Recommendation

Track loss-adjusted claims per user per epoch instead of relying on the single `lastWithdrawRequest` marker. Either:

- Record each user's request epochs (e.g., a per-user epoch list or bitmask) and have `_claimLossAdjustedWithdrawRequest` iterate over all epochs with `lossRecoveryPriceByEpoch[epoch] != 0` for that user, or
- At request time, eagerly settle/roll the user's prior loss-adjusted receipt into a haircutted basis bucket (store a per-user `lossAdjustedClaims` total reduced by `lossRecoveryPriceByEpoch[oldEpoch]` whenever a new `requestWithdraw` arrives while `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]] != 0`), so the receipt can never revert to par.

Additionally, `requestWithdraw` should not be allowed to silently strand a pending loss-adjusted claim; either claim/settle it first or revert.

### Proof of Concept

Foundry fork-style sketch, building on the harness in `test/foundry/IdleCDOEpochQueue.t.sol` (lines 811-836 already demonstrate a loss-adjusted stop via `stopEpochWithDuration`):

```solidity
function testLossReceiptEscapesHaircut() external {
    // Epoch E running; attacker is a normal KYC'd tranche holder
    uint256 trancheAmount = ONE_TRANCHE;
    _requestWithdrawWithUser(attacker, trancheAmount);       // receipt in epoch E

    vm.prank(manager);
    cdoEpoch.startEpoch();
    vm.warp(cdoEpoch.epochEndDate() + 1);

    // Manager stops epoch E with a realized loss: e.g. fund only 90% of pending
    uint256 pendingBasis = strategy.pendingWithdraws();
    uint256 activeBasis  = cdoEpoch.getContractValue() + cdoEpoch.expectedEpochInterest();
    uint256 totalBasis   = activeBasis + pendingBasis;
    uint256 totalRecovery = totalBasis * 90 / 100;           // 10% loss
    uint256 lossAmount    = totalBasis - totalRecovery;
    (uint256 pendingToFund, ) = strategy.previewLossAdjustedWithdrawFunds(lossAmount);
    uint256 repay = cdoEpoch.expectedEpochInterest() + pendingToFund;
    deal(address(underlying), strategy.borrower(), repay, true);
    vm.prank(strategy.borrower());
    underlying.approve(address(cdoEpoch), repay);
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(10e18, 0, cdoEpoch.epochDuration(), lossAmount);

    uint256 lossEpoch = strategy.epochNumber();
    assertGt(strategy.lossRecoveryPriceByEpoch(lossEpoch), 0);

    // Epoch E+1: attacker re-requests, overwriting lastWithdrawRequest
    vm.prank(manager);
    cdoEpoch.startEpoch();
    _depositWithUser(attacker, 1e6);                          // fresh tranche tokens
    _requestWithdrawWithUser(attacker, ONE_TRANCHE / 100);   // lastWithdrawRequest -> E+1

    // Stop epoch E+1 normally, fully funded
    _stopCurrentEpochWithApr(10e18);

    // Claim: loss path misses (lossRecoveryPriceByEpoch[E+1] == 0),
    // funded path pays the whole aggregate at par.
    uint256 balPre = underlying.balanceOf(attacker);
    vm.prank(address(cdoEpoch)); // or via cdoEpoch.claimWithdrawRequest()
    strategy.claimWithdrawRequest(attacker);

    uint256 par = strategy_pendingBasis_at_request; // what attacker deposited for epoch E receipt
    // Attacker got ~100% of the epoch-E receipt instead of ~90%
    assertGt(underlying.balanceOf(attacker) - balPre,
             pendingBasis * strategy.lossRecoveryPriceByEpoch(lossEpoch) / RECOVERY_FULL);
}
```

Expected result: the attacker's epoch-E receipt is paid at par; the aggregate funded reserve is short by `pendingBasis_attacker * (1 - lossRecoveryPrice)`, which honest loss-epoch claimants cannot recover.

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L411-429)
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
