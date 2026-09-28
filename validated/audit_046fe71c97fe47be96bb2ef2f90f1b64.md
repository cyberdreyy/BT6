### Title
Loss-adjusted withdraw receipts are keyed by the post-increment epoch at `collectWithdrawFunds` but looked up by the request-time epoch via `lastWithdrawRequest`, so haircutted receipts fall through to the funded-claim path and pay out at par - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The external report describes an identifier that is normalized on the write path but used raw on the read path, producing an encoding mismatch that makes a legitimate operation revert or misroute. The same bug class exists in `IdleCreditVault`: the per-epoch key under which a stop-epoch loss price is stored (`lossRecoveryPriceByEpoch[epochNumber]`, written after `deposit()` bumps `epochNumber` during `stopEpoch`) does not match the key used to read it (`lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]`, where `lastWithdrawRequest` was set to the pre-increment `epochNumber` at request time). Because the lookup returns `0`, `_claimLossAdjustedWithdrawRequest` silently skips the haircut and the claim falls through to `_claimFundedWithdrawRequest`, which pays the full unhaircutted basis.

### Finding Description
In `requestWithdraw`, the receipt epoch is captured as `uint256 currentEpoch = epochNumber` and stored both in `lastWithdrawRequest[_user] = currentEpoch` and `withdrawsRequestsByEpoch[_user][currentEpoch]`. [1](#0-0) 

When the epoch is stopped with a realized loss, the CDO calls `collectWithdrawFunds(_amount)`. If `_amount < pendingBasis`, the partial-funding haircut is written as `lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice` using the *current* `epochNumber`. [2](#0-1) 

However, `epochNumber` is incremented inside `deposit()` when the deposit happens "on stopEpoch (before setting the var to false)", i.e. during the same `stopEpoch` flow that later collects the withdraw funds. [3](#0-2) 

On the claim side, `_claimLossAdjustedWithdrawRequest` derives the lookup epoch from `lastWithdrawRequest[_user]` — the *request-time* epoch — and returns `0` if `lossRecoveryPriceByEpoch[lossEpoch] == 0`. [4](#0-3) 

So for a request made in epoch N (stored under key N), the loss price is stored under key N+1 (post-increment). The read at key N returns `0`, the loss-adjusted path is skipped entirely, and execution reaches `_claimFundedWithdrawRequest`, whose only epoch gate is `epochNumber <= lastWithdrawRequest[_user]` — which passes because `epochNumber` (N+1) > `lastWithdrawRequest` (N). It then pays `withdrawsRequests[_user]` at full par via `_transferFundedClaim`. [5](#0-4) 

The mirror-image encoding mismatch in the report (name lowered for lookup, raw for encoding) is reproduced here as: epoch key "raw" (request-time) on the user/receipt side, epoch key "shifted" (post-increment) on the loss-price side.

### Impact Explanation
The loss waterfall is bypassed: a pending withdraw receipt that `stopEpochWithDuration`/`collectWithdrawFunds` intended to settle at `lossRecoveryPrice < RECOVERY_FULL` is instead paid 1:1 from strategy-held underlying. Since `pendingWithdraws` was zeroed and only the partial amount was collected, paying full basis draws the shortfall from funds belonging to other claimants or the default-recovery reserve (`_transferFundedClaim` only avoids the reserve bucket, not other funded balances). This is direct over-payment / insolvency of the pending-claims bucket, quantified as `claimBasis * (1 - lossRecoveryPrice)` per affected receipt, repeatable for every user who requested a withdraw in the loss epoch. Additionally, the stored `lossRecoveryPriceByEpoch` entry becomes unreachable dust and `withdrawsRequestsByEpoch[_user][N]` is cleared through the funded path without ever applying the haircut, corrupting subsequent epoch accounting.

### Likelihood Explanation
The trigger requires only normal honest-actor sequencing: a lender calls `requestWithdraw` during a running epoch, then the borrower under-funds `stopEpoch` (a partial-loss stop), which is a supported, non-privileged-adversary code path (`previewLossAdjustedWithdrawFunds` exists precisely for this). No attacker privileges are needed beyond being a withdraw requester in the loss epoch. The one caveat: this analysis assumes `deposit()` (which increments `epochNumber`) runs inside `stopEpoch` before `collectWithdrawFunds` in `IdleCDOEpochVariant`; that ordering is implied by the inline comment at line 608-610 and by the documented "buffer + epochDuration is 1 epoch" convention, but if the CDO collects funds *before* the increment, the keys would align and the issue would reduce to a no-op. A Foundry fork PoC should confirm the ordering first.

### Recommendation
Use a single, consistent epoch key for loss-adjusted receipts. Either:
- Store `lossRecoveryPriceByEpoch` under the epoch the pending receipts were requested in (e.g. track `lastPendingWithdrawEpoch` set in `requestWithdraw` and use it in `collectWithdrawFunds`), or
- Read the loss price using the same key the write used — e.g. look up `lossRecoveryPriceByEpoch[epochNumber]` (the settle epoch) rather than `lastWithdrawRequest[_user]` in `_claimLossAdjustedWithdrawRequest`, and record the settle-epoch on the request instead of the request-epoch.

Additionally, add an invariant check in `_claimFundedWithdrawRequest`: if `pendingWithdraws == 0` was reached via a partial collect for the user's request epoch, the claim must route through the loss-adjusted path, not silently pay at par.

### Proof of Concept
```solidity
// Foundry fork test sketch (contracts/strategies/idle/IdleCreditVault.sol target)
function testLossEpochKeyMismatch() external {
    // Epoch N running: user requests withdraw of W underlying
    vm.prank(cdo);
    vault.requestWithdraw(W, user, W);
    // lastWithdrawRequest[user] == epochNumber (N)
    // withdrawsRequestsByEpoch[user][N] == W

    // stopEpoch: deposit() bumps epochNumber to N+1, borrower funds only W/2
    vm.startPrank(cdo);
    vault.deposit(0);                    // inside stopEpoch -> epochNumber = N+1
    vault.collectWithdrawFunds(W / 2);   // lossRecoveryPriceByEpoch[N+1] = RECOVERY_FULL/2
    vm.stopPrank();

    // User claims. _claimLossAdjustedWithdrawRequest reads
    // lossRecoveryPriceByEpoch[lastWithdrawRequest[user]] == price[N] == 0 -> skip.
    // _claimFundedWithdrawRequest passes gate (N+1 > N) and pays W in full.
    uint256 balPre = underlying.balanceOf(user);
    vm.prank(cdo);
    vault.claimWithdrawRequest(user);
    uint256 paid = underlying.balanceOf(user) - balPre;

    // Expected haircut payout: W/2. Actual: W (full par) -> overpayment of W/2
    assertEq(paid, W);            // buggy: pays full basis
    // assertEq(paid, W / 2);     // correct behavior
}
```

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L411-426)
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
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L607-614)
```text
    if (IIdleCDOEpochVariant(idleCDO).isEpochRunning()) {
      // deposit done on stopEpoch (before setting the var to false) so we reset the counter
      totEpochDeposits = 0;
      epochNumber += 1;
    } else {
      // deposit done between epochs so we increase the counter
      totEpochDeposits += _amount;
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
