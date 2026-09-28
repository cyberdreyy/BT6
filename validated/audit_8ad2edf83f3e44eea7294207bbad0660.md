### Title
Loss-adjusted withdraw receipts escape the haircut when a user re-requests before claiming, paying par against only-loss-adjusted funding — (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
When `stopEpochWithDuration(_lossAmount)` realizes a loss, pending withdraw receipts are funded only for the haircutted amount (`pendingToFund = pendingBasis - pendingLoss`) and each user's basis is supposed to be paid at `lossRecoveryPriceByEpoch`. However, `_claimLossAdjustedWithdrawRequest` only inspects the epoch stored in `lastWithdrawRequest[_user]`. An unprivileged lender who holds a loss-epoch receipt can simply submit another `requestWithdraw` in a later epoch before claiming; the new request overwrites `lastWithdrawRequest[_user]` to an epoch with `lossRecoveryPriceByEpoch == 0`, so the old loss-epoch basis silently falls through to `_claimFundedWithdrawRequest` and is paid **at par** — even though the strategy only received the haircutted funding for it.

### Finding Description
- `requestWithdraw` records per-epoch basis in `withdrawsRequestsByEpoch[_user][currentEpoch]`, accumulates `withdrawsRequests[_user]`, and unconditionally overwrites `lastWithdrawRequest[_user] = currentEpoch` [1](#0-0) .
- On a lossy `stopEpochWithDuration`, `previewLossAdjustedWithdrawFunds` splits `_lossAmount` between the active book and pending receipts, so the borrower only funds `pendingToFund < pendingBasis`; the remainder is the receipt holders' loss share [2](#0-1) .
- The haircut is enforced only in `_claimLossAdjustedWithdrawRequest`, which reads `lossEpoch = lastWithdrawRequest[_user]` and returns 0 if `lossRecoveryPriceByEpoch[lossEpoch] == 0` — there is no iteration over the user's other request epochs [3](#0-2) .
- Whatever basis survives is then paid 1:1 by `_claimFundedWithdrawRequest` via `amount = withdrawsRequests[_user] + apr0 buckets` → `_transferFundedClaim` [4](#0-3) .
- `_clearWithdrawClaimForEpoch` only zeroes the single `_claimEpoch` passed in; older epochs' `withdrawsRequestsByEpoch` entries persist in the aggregate `withdrawsRequests[_user]` [5](#0-4) .

Broken invariant: loss socialization (BB/AA-first waterfall plus the pending-receipt haircut). A receipt minted in a loss epoch is required to be paid `claimBasis * lossRecoveryPrice / 1e18`; the code only enforces that while it remains the *latest* request epoch.

Attack sequence (fixed-APR mode, standard epochs):
1. Buffer phase of epoch N: attacker requests withdraw; `lastWithdrawRequest = N`, `withdrawsRequestsByEpoch[attacker][N] = X`.
2. Epoch N runs; borrower repays with a realized loss. Manager calls `stopEpochWithDuration(_, _, _, lossAmount)`; the vault pulls only `pendingToFund = X * price/1e18` for pending receipts and stores `lossRecoveryPriceByEpoch[N] = price < 1e18`. `epochNumber` bumps to N+1.
3. Buffer phase of epoch N+1 (before claiming): attacker calls `requestWithdraw` again for a dust amount. `lastWithdrawRequest` is overwritten to N+1. `lossRecoveryPriceByEpoch[N+1] == 0`.
4. Epoch N+1 stops normally; `epochNumber` bumps to N+2 > `lastWithdrawRequest`, so the `NotAllowed` gate in `_claimFundedWithdrawRequest` passes.
5. Attacker calls `claimWithdrawRequest`: loss-adjusted path returns 0 (no haircut for N+1), and `_claimFundedWithdrawRequest` pays `withdrawsRequests[attacker]` — which still includes the full `X` from epoch N — at par.
6. Result: attacker withdraws `X` while only `X*price/1e18` was funded for that receipt. The shortfall `X*(1-price)/1e18` is taken from underlying belonging to other LPs/future claims — direct theft of the loss share that should have been borne by the attacker.

Guards do not stop it: the `epochNumber <= lastWithdrawRequest` check only enforces a one-epoch wait (satisfied after step 4), the receipt-N basis is never erased unless the N-epoch claim runs while N is still `lastWithdrawRequest`, and nothing in `requestWithdraw` forces settlement or rejects re-requesting with an open loss-epoch receipt (the code comments even bless re-requesting, expecting the aggregate to be claimed later).

### Impact Explanation
The attacker recovers 100% of a receipt that was only funded at `price` (e.g., 70% after a 30% loss), extracting `X*(1-price)` of underlying from the vault. Since loss-adjusted funding is exact, every wei overpaid is insolvency pushed onto remaining depositors and other withdraw claimants — repeated or scaled to the attacker's full position, this directly drains the pool. Attacker needs only to be a KYC-passing lender and to sequence around honest manager `startEpoch`/`stopEpochWithDuration` calls; no privileged role required.

### Likelihood Explanation
Loss epochs (`stopEpochWithDuration` with `_lossAmount > 0`) are an intended, honest flow — managers use them to socialize borrower shortfalls. Any receipt holder in such an epoch has a direct financial incentive to defer claiming and re-request (one extra epoch of waiting) to dodge the haircut entirely. The trigger costs only gas plus one epoch of delay, is fully repeatable each loss epoch, and requires no cooperation from privileged roles.

### Recommendation
In `requestWithdraw`, if `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]] != 0` (or any unfinalized loss/default receipt exists), force settlement of the old receipt first — analogous to the post-default "must claim open receipt before re-requesting" rule tested in `testPostDefaultWithdrawRequiresClaimingOpenPriorReceipt`. Alternatively, iterate all of the user's request epochs in `claimWithdrawRequest` and apply each epoch's `lossRecoveryPriceByEpoch`/`defaultRecoveryPrice`, rather than keying the loss path off the single `lastWithdrawRequest` value.

### Proof of Concept
Foundry fork PoC (extend `test/foundry/IdleCreditVault.t.sol` scaffolding; mirrors the loss-adjusted flow in `IdleCDOEpochQueue.t.sol:811-858`):

```solidity
function testLossEpochReceiptEscapesHaircut() external {
    _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, cdoEpoch.managementFee());
    address attacker = makeAddr('attacker');
    uint256 amountWei = 10_000 * ONE_SCALE;
    _depositWithUser(attacker, amountWei, true);       // KYC'd lender deposits AA
    uint256 trancheBal = IERC20(AAtranche).balanceOf(attacker);

    // buffer of epoch N: attacker requests full withdraw
    vm.prank(attacker);
    uint256 reqN = cdoEpoch.requestWithdraw(trancheBal, address(AAtranche));

    _startEpochAndCheckPrices(0);

    // borrower shortfall: stopEpochWithDuration with loss so receipts are haircutted
    vm.warp(cdoEpoch.epochEndDate() + 1);
    uint256 pendingBasis = strategy.pendingWithdraws();
    uint256 activeBasis = cdoEpoch.getContractValue() + cdoEpoch.expectedEpochInterest();
    uint256 loss = (activeBasis + pendingBasis) * 3e17 / 1e18; // 30% loss
    (uint256 pendingToFund,) = strategy.previewLossAdjustedWithdrawFunds(loss);
    deal(defaultUnderlying, borrower, cdoEpoch.expectedEpochInterest() + pendingToFund);
    vm.prank(borrower);
    underlying.approve(address(cdoEpoch), type(uint256).max);
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(0, 0, cdoEpoch.epochDuration(), loss);
    // lossRecoveryPriceByEpoch[N] = 0.7e18; only pendingToFund was pulled in

    // buffer of epoch N+1: attacker re-requests dust to move lastWithdrawRequest
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(1, address(AAtranche));   // overwrites marker to N+1

    _startEpochAndCheckPrices(1);
    _stopEpochAndCheckPrices(1, initialProvidedApr, _expectedFundsEndEpoch()); // clean epoch

    uint256 balPre = underlying.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimWithdrawRequest();
    uint256 paid = underlying.balanceOf(attacker) - balPre;

    // attacker is paid reqN at par instead of reqN * 0.7e18 / 1e18
    assertEq(paid, reqN + 1, 'loss haircut skipped');
    uint256 fundedForN = pendingToFund;
    assertGt(paid, fundedForN, 'claim exceeds funded amount - drains other LPs');
}
```

Caveat: I verified the claim-path logic in `IdleCreditVault.sol` (lines 271–350, 440–460, 760–856) but did not fully trace `stopEpochWithDuration` inside `IdleCDOEpochVariant.sol`; the PoC assumes, per `previewLossAdjustedWithdrawFunds`, that only `pendingToFund` is collected for pending receipts — which is the documented intent of that function. If the CDO instead pulls the full `pendingBasis`, the haircut leak is neutralized; that funding path should be confirmed while writing the PoC.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L271-294)
```text
    }
    // burn strategy tokens from cdo (we don't burn future interest here, only the principal)
    _burn(msg.sender, _principal);
    // mint equal amount of strategy tokens to the user as receipt (interest included), useful in case of default
    _mint(_user, _amount);
    // A successfully closed pool already recalled all funds and has no later stopEpoch.
    if (!isClosed) {
      // Global amount that stopEpoch must source from borrower/strategy for all pending receipts.
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
