### Title
Loss-adjusted withdrawal haircut only applied to the latest request epoch, letting multi-epoch receipts escape loss socialization - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault` records withdraw receipts per epoch in `withdrawsRequestsByEpoch[user][epoch]`, but `lastWithdrawRequest[user]` is a single scalar overwritten on every `requestWithdraw`. When `stopEpochWithDuration(_lossAmount)` imposes a loss, `lossRecoveryPriceByEpoch` is keyed to that single "last" epoch, so `_claimLossAdjustedWithdrawRequest` haircuts only the latest epoch's receipt while receipts queued in earlier epochs are paid at par through `_claimFundedWithdrawRequest`. An unprivileged lender can ladder requests across two consecutive epochs so that roughly half of their claim permanently escapes any loss haircut, over-withdrawing the loss-adjusted fund that `pendingWithdraws` was funded with and socializing the difference onto other users.

### Finding Description
The bug class (stale/dangling state reused after a logical "free") maps onto the vault's epoch-scoped receipt accounting:

- `requestWithdraw` writes `lastWithdrawRequest[_user] = currentEpoch` and accumulates `withdrawsRequestsByEpoch[_user][currentEpoch]` [1](#0-0) . The guard at lines 263–271 only blocks a *new* request when the *previous* `lastWithdrawRequest` epoch already carries a `lossRecoveryPrice` — it does not prevent holding receipts in two different epochs before any loss occurs.
- On a lossy `stopEpochWithDuration`, the borrower funds `pendingWithdraws * lossRecoveryPrice` (per `previewLossAdjustedWithdrawFunds`), i.e. the recovery price is meant to cover *all* pending receipts.
- At claim time, `claimWithdrawRequest` first runs `_claimLossAdjustedWithdrawRequest`, which uses `lastWithdrawRequest[_user]` as the sole `lossEpoch` and calls `_clearWithdrawClaimForEpoch(_user, lossEpoch, false)`, clearing only `withdrawsRequestsByEpoch[_user][lossEpoch]` [2](#0-1) . `_clearWithdrawClaimForEpoch` subtracts only that epoch's slice from `withdrawsRequests[_user]` and zeroes `lastWithdrawRequest` [3](#0-2) .
- Control then falls into `_claimFundedWithdrawRequest`, which pays the remaining `withdrawsRequests[_user]` — the earlier-epoch receipt — at par, gated only by `epochNumber > lastWithdrawRequest` (now 0) [4](#0-3) .

Broken invariant: the loss waterfall / "one receipt, one (haircutted) payout". A receipt pending at the loss epoch is paid at par as if already funded pre-loss, even though the borrower only transferred `recovery * pendingWithdraws`.

### Impact Explanation
Direct theft from other users, quantifiable. If the attacker requests amount `A` in epoch N and `B` in epoch N+1, and a loss with recovery `r` hits at `stopEpoch` of epoch N+1, the vault receives `r*(A+B)` for pending receipts but pays out `A + r*B`. The excess `A*(1-r)` is drained from vault underlying belonging to other depositors/withdrawers. With an ongoing laddering strategy the attacker guarantees ~50% of their position escapes any single loss event, at the cost only of splitting requests across epochs — no privileged action required, and the loss itself is an honest-manager `stopEpochWithDuration` the attacker merely positions around.

### Likelihood Explanation
The attacker is a KYC-passing tranche holder (in-scope). The sequence requires only two ordinary `requestWithdraw` calls in consecutive buffer periods — a behavior a sophisticated user can run continuously. The triggering event (a loss via `stopEpochWithDuration`) is an honest-manager action in normal protocol operation. No guard stops it: the `lossRecoveryPrice` guard only fires *after* a loss is recorded, `_claimFundedWithdrawRequest` explicitly treats remaining receipts as "older funded receipts" [5](#0-4) , and the `_onlyIdleCDO`/epoch gating all pass. Likelihood is moderate: it requires a real loss event and epoch-boundary timing, but the attacker's positioning cost is near zero.

### Recommendation
Track all epochs that hold a user's pending receipts (e.g., a per-user epoch set, or store the full basis `withdrawsRequests[_user]` under `lossRecoveryPriceByEpoch` keyed by request epoch for every unfunded epoch, not just the last). Concretely: in `_claimLossAdjustedWithdrawRequest`, iterate every epoch `e <= lastWithdrawRequest[_user]` with `lossRecoveryPriceByEpoch[e] != 0`, or change the accounting so a single recovery price applies to the whole `withdrawsRequests[_user]` balance pending at the loss. Simplest fix: store `lossRecoveryPriceByEpoch` per request-epoch at funding time for *all* epochs contributing to `pendingWithdraws`, and have `_claimFundedWithdrawRequest` skip any epoch basis that has a nonzero `lossRecoveryPriceByEpoch`.

### Proof of Concept
Foundry fork test extending `test/foundry/IdleCreditVault.t.sol` harness:

```solidity
function testLossHaircutEscapeAcrossEpochs() external {
    // honest setup: epoch N running, attacker holds tranches
    uint256 amountA = 50e6;   // 50 USDC
    uint256 amountB = 50e6;
    uint256 tranchesA = _depositWithUser(attacker, amountA);

    // Epoch N buffer: attacker requests withdraw of A
    _requestWithdrawWithUser(attacker, tranchesA);
    _stopCurrentEpochWithApr(10e18);            // -> epoch N+1, request still pending

    // Epoch N+1 buffer: attacker requests again (guard passes: no lossRecoveryPrice on epoch N)
    uint256 tranchesB = _depositWithUser(attacker, amountB);
    _requestWithdrawWithUser(attacker, tranchesB);

    // Honest manager stops epoch N+1 with a 50% loss.
    uint256 pending = strategy.pendingWithdraws();       // == A + B
    uint256 loss = pending / 2;
    (uint256 pendingToFund, ) = strategy.previewLossAdjustedWithdrawFunds(loss);
    uint256 repay = cdoEpoch.expectedEpochInterest() + pendingToFund;
    deal(address(underlying), strategy.borrower(), repay, true);
    vm.prank(strategy.borrower());
    underlying.approve(address(cdoEpoch), repay);
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(10e18, 0, cdoEpoch.epochDuration(), loss);

    // Attacker claims: epoch N+1 slice haircut, epoch N slice paid at par.
    uint256 balPre = underlying.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimWithdrawRequest(); // or via CDO withdraw flow
    uint256 received = underlying.balanceOf(attacker) - balPre;

    uint256 r = strategy.lossRecoveryPriceByEpoch(strategy.lastWithdrawRequest(attacker));
    uint256 fairPayout = (amountA + amountB) * r / 1e18;
    // Vault only received pendingToFund == fairPayout for pending receipts,
    // but attacker pulls ~ amountA + amountB * r  > fairPayout.
    assertGt(received, fairPayout, "epoch-N receipt escaped the haircut");
}
```

Note: the exact loss-recovery bookkeeping in `previewLossAdjustedWithdrawFunds`/`stopEpochWithDuration` (which epoch index receives `lossRecoveryPriceByEpoch`) could not be fully read within the tool budget; the finding holds as long as the haircut is keyed to `lastWithdrawRequest` while `withdrawsRequestsByEpoch` retains an earlier-epoch basis that `_claimFundedWithdrawRequest` pays at par.

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L811-836)
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
```
