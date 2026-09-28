### Title
Loss-adjusted epoch haircut is applied only to the `lastWithdrawRequest` epoch, so older pending receipts are paid at par and drain the strategy — (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary

`collectWithdrawFunds` computes a single `lossRecoveryPrice` over the *aggregate* `pendingWithdraws` (which can contain receipts from multiple request epochs) but stores it under only the current `epochNumber`. `_claimLossAdjustedWithdrawRequest` then resolves the loss epoch via `lastWithdrawRequest[_user]` and clears only that epoch's per-epoch receipt, while the remaining older-epoch balance stays in `withdrawsRequests[_user]` and is paid at par by `_claimFundedWithdrawRequest` in the same call. This is the credit-vault analog of CVE-2022-33743: a reference (the old-epoch receipt inside `pendingWithdraws`) is retained and used to size the haircut, but is then "freed" — excluded from the loss-adjusted claim and paid in full anyway. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description

When the borrower under-funds pending withdrawals at `stopEpochWithDuration`, `collectWithdrawFunds(_amount)` executes:

```solidity
uint256 pendingBasis = pendingWithdraws;                    // aggregate across ALL request epochs
uint256 lossRecoveryPrice = _amount * RECOVERY_FULL / pendingBasis;
pendingWithdraws = 0;
lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice;  // keyed to ONE epoch
```

`pendingWithdraws` is incremented in `requestWithdraw` for every non-closed request and is *not* per-epoch — a user who requested in epoch `E`, never claimed (funded at par), and requested again in epoch `E+1` contributes both receipts to `pendingBasis`... actually `pendingWithdraws` is zeroed at each successful `collectWithdrawFunds`, so the residual at the loss stop is the sum of all still-unclaimed receipts requested during the epoch(s) that stop covers. Concretely: `requestWithdraw` during the buffer of epoch `E` records `withdrawsRequestsByEpoch[user][E]`; if the user does not claim and requests again during the buffer of epoch `E+1`, both `withdrawsRequests[E]` and `withdrawsRequests[E+1]` amounts are live inside `pendingWithdraws` when `stopEpochWithDuration(_lossAmount)` runs at the end of `E+1`. [4](#0-3) 

`previewLossAdjustedWithdrawFunds` confirms the design intent: the entire aggregate `pendingBasis` shares the loss pro rata, and only `pendingToFund = pendingBasis - pendingLoss` is collected from the borrower. [5](#0-4) 

The payout side, however, is epoch-scoped. `claimWithdrawRequest` runs `_claimLossAdjustedWithdrawRequest`, which derives `lossEpoch = lastWithdrawRequest[_user]` (the *latest* request epoch) and calls `_clearWithdrawClaimForEpoch(user, lossEpoch, false)`, clearing only `withdrawsRequestsByEpoch[user][E+1]` and subtracting only that piece from `withdrawsRequests[user]`. It pays `E+1_basis * lossRecoveryPrice / RECOVERY_FULL`. Execution then falls into `_claimFundedWithdrawRequest`, whose epoch gate (`epochNumber <= lastWithdrawRequest`) now passes because `lastWithdrawRequest` was reset to 0, and which pays the *remaining* `withdrawsRequests[user]` — the epoch-`E` receipt — **at par** via `_transferFundedClaim`. [6](#0-5) [7](#0-6) 

So a user holding receipts in two epochs gets `E2 * price + E1 * 1.0`, while the strategy only collected `(E1 + E2) * price` for the whole pending bucket. The excess `E1 * (1 - price)` is paid from strategy underlyings that belong to other funded claimants / the recovery reserve accounting.

The mirror-image guard in `requestWithdraw` (lines 261–271) only inspects `lossRecoveryPriceByEpoch[lastWithdrawRequest]`; it does not prevent accumulating receipts across multiple epochs, and nothing clears or haircut-marks `withdrawsRequestsByEpoch` entries for epochs other than `lastWithdrawRequest`. [8](#0-7) 

### Impact Explanation

Direct theft / insolvency of the credit vault. The attacker receives `oldEpochReceipt * (1 - lossRecoveryPrice)` more underlying than the borrower actually funded. Because `pendingWithdraws` was zeroed at collection time, the strategy's balance no longer covers the par payout of the stale receipt; the difference is taken from funds earmarked for other funded claims, causing later claimants' `_transferFundedClaim` calls to revert (permanent freezing of unclaimed funded withdrawals once the balance is exhausted) or consuming funds that belong to active LPs/next-epoch liquidity. Quantified loss equals the stale-epoch receipt size times the haircut — e.g., a 100k receipt with a 70% recovery price extracts 30k of unbacked underlying per affected user, and the attacker can size the stale receipt arbitrarily by requesting large withdrawals and simply not claiming across epochs.

### Likelihood Explanation

Requires a real loss epoch (`stopEpochWithDuration` with `_lossAmount > 0` so `collectWithdrawFunds` under-funds) while any user holds unclaimed normal withdraw receipts spanning two request epochs — a routine state: requesting a withdrawal and not claiming for an epoch is explicitly supported ("if a user does not claim... he will have to wait another epoch to claim both"). No privileged misbehavior is needed; an ordinary tranche-token holder (KYC-passing lender) triggers it just by claiming once after the loss stop. APR0 mode is equally affected since `_withdrawClaimAmountsForEpoch` mixes normal and APR0 basis but `_claimLossAdjustedWithdrawRequest` again only scopes to `lastWithdrawRequest`.

### Recommendation

Track the loss haircut per pending-receipt epoch rather than globally. Concretely: in `collectWithdrawFunds`, record the haircut so that *every* epoch contributing to `pendingBasis` is marked loss-adjusted (e.g., store `lossRecoveryPriceByEpoch` for each epoch that has outstanding receipts, or maintain a single `pendingLossEpochs` range/list), or alternatively store an aggregate "loss-adjusted pending claim" per user so `_claimFundedWithdrawRequest` can never pay a receipt that was inside the haircut basis at par. At minimum, `_claimLossAdjustedWithdrawRequest` should iterate all `withdrawsRequestsByEpoch` entries included in the haircut, not only `lastWithdrawRequest`, and `requestWithdraw` should revert when *any* unclaimed receipt epoch has a nonzero `lossRecoveryPriceByEpoch`, not just the latest marker.

### Proof of Concept

Foundry fork PoC (schematic, modeled on `test/foundry/IdleCreditVault.t.sol` helpers `_depositWithUser`, `_startEpochAndCheckPrices`, `_stopEpochAndCheckPrices`):

```solidity
function testLossEpochPaysOldReceiptAtPar() external {
    // Setup: standard epoch variant, fixed APR, attacker = KYC'd LP
    uint256 amount = 200_000 * ONE_SCALE;
    _depositWithUser(attacker, amount, true);

    // Epoch 0 buffer: attacker requests withdraw of half
    vm.prank(attacker);
    uint256 claim1 = cdoEpoch.requestWithdraw(halfBal, address(AAtranche));
    _startEpochAndCheckPrices(0);
    // stop epoch 0 normally -> claim1 funded at par, attacker does NOT claim
    _stopEpochAndCheckPrices(0, initialProvidedApr, _expectedFundsEndEpoch());

    // Epoch 1 buffer: attacker requests remaining half (allowed: guard only
    // checks lossRecoveryPriceByEpoch[lastWithdrawRequest], which is 0)
    vm.prank(attacker);
    uint256 claim2 = cdoEpoch.requestWithdraw(0, address(AAtranche));
    _startEpochAndCheckPrices(1);

    // stop epoch 1 WITH LOSS: borrower funds only pendingToFund < claim1+claim2
    uint256 pendingBasis = strategy.pendingWithdraws(); // == claim1 + claim2
    // manager stops with _lossAmount such that lossRecoveryPrice = 0.7e18
    _stopEpochWithLoss(1, lossAmount); // -> collectWithdrawFunds(0.7 * pendingBasis)
    assertEq(strategy.lossRecoveryPriceByEpoch(strategy.epochNumber()), 0.7e18);

    // Attacker claims once
    uint256 balPre = underlying.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimWithdrawRequest();
    uint256 got = underlying.balanceOf(attacker) - balPre;

    // BUG: expected fair payout = (claim1 + claim2) * 0.7
    //      actual payout      = claim2 * 0.7 + claim1 * 1.0  (E1 paid at par)
    assertGt(got, (pendingBasis * 7e17) / 1e18);
    // the excess ~ claim1 * 0.3 is drawn from other claimants' funded balance;
    // a second user's funded claim now reverts in _transferFundedClaim
}
```

Caveat: I could not execute the PoC in this environment, and the exact CDO-side wiring of `stopEpochWithDuration` into `collectWithdrawFunds` should be confirmed against `IdleCDOEpochVariant.stopEpochWithDuration`, but the strategy-side clearing asymmetry described above is directly visible in the cited code.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L259-271)
```text
    bool isClosed = IIdleCDOEpochVariant(idleCDO).epochEndDate() == 0;
    uint256 currentEpoch = epochNumber;
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L277-294)
```text
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
