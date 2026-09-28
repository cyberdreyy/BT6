### Title
Overwriting `lastWithdrawRequest` lets a loss-adjusted withdraw receipt claim at par, stealing other claimants' funded recovery — (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
CVE-2019-17342 is a race in `XENMEM_exchange` where stale state survived a non-atomic exchange, letting a guest keep rights it should have lost. The idle-tranches analog lives in `IdleCreditVault`'s loss-adjusted withdrawal path: the epoch that suffered a `stopEpochWithDuration` haircut is looked up through the single-slot pointer `lastWithdrawRequest[_user]`, while the actual per-epoch debt lives in `withdrawsRequestsByEpoch[_user][epoch]`. A second `requestWithdraw` in a later epoch overwrites the pointer but not the per-epoch ledger, so the haircutted receipt is "exchanged" for a stale funded one and is paid at par from funds that were only partially collected.

### Finding Description
When a user calls `requestWithdraw`, the vault records the request epoch in `lastWithdrawRequest[_user]` and accrues the basis in both `withdrawsRequests[_user]` (aggregate) and `withdrawsRequestsByEpoch[_user][currentEpoch]`. [1](#0-0) 

If the borrower under-funds receipts at `stopEpochWithDuration`, `collectWithdrawFunds` stores `lossRecoveryPriceByEpoch[epochNumber]` and clears `pendingWithdraws` — i.e., the vault physically receives only `pendingBasis * lossRecoveryPrice / RECOVERY_FULL`. [2](#0-1) 

At claim time, `claimWithdrawRequest` first tries `_claimLossAdjustedWithdrawRequest`, which derives the loss epoch solely from `lastWithdrawRequest[_user]`; if `lossRecoveryPriceByEpoch[lastWithdrawRequest]` is zero it silently skips, and `_claimFundedWithdrawRequest` then pays the entire aggregate `withdrawsRequests[_user]` at par. [3](#0-2) [4](#0-3) [5](#0-4) 

The invariant break: a user who requested in haircut epoch N and then made any second `requestWithdraw` in epoch N+1 gets `lastWithdrawRequest = N+1`. `lossRecoveryPriceByEpoch[N+1] == 0`, so the epoch-N haircutted basis is never routed through `_clearWithdrawClaimForEpoch(user, N, …)` (which is the only code that zeroes `withdrawsRequestsByEpoch[user][N]` at the haircut price). [6](#0-5)  The funded-claim path's only gate is `epochNumber > lastWithdrawRequest` (one epoch wait), which the epoch N+1 request satisfies after epoch N+2 stops. [7](#0-6)  Result: receipt intended to pay `basis * price` pays `basis` in full.

### Impact Explanation
The vault only collected `pendingBasis * lossRecoveryPrice / RECOVERY_FULL` for epoch-N receipts, yet pays the epoch-N basis at par inside the aggregate. The over-payment (`basis * (1 - price)`) is taken from the same balance that funds other users' claims and, once `defaultRecoveryReserve` is set, `_transferFundedClaim` can also spend reserve-adjacent funds. Concrete loss: with `pendingBasis = 100_000` and `lossRecoveryPrice = 0.5e18`, an attacker holding the whole epoch-N bucket plus a 1-wei epoch-N+1 request claims 100_000 instead of 50_000 — a 50_000 underlying theft making other claimants' funded receipts unpayable (insolvency for honest users). Attacker needs only be a KYC-passed lender/tranche holder; all privileged actors stay honest (manager legitimately calls `stopEpochWithDuration` with a realized loss — a normal protocol path, not borrower-default freezing).

### Likelihood Explanation
Requires a `stopEpochWithDuration` loss epoch (an intended recovery mode per `previewLossAdjustedWithdrawFunds`) and an attacker holding tranche tokens in two consecutive buffer phases — trivially achievable by splitting one deposit's requests across two epochs. No timing race is needed; the bug is deterministic bookkeeping. Existing guards don't stop it: `withdrawsRequests` aggregate is only cleared in the funded path (set to 0 after paying par), the per-epoch mapping is never consulted against the haircut for a non-last epoch, and `lossRecoveryPriceByEpoch` lookups keyed on the mutable `lastWithdrawRequest` silently miss. [5](#0-4) [8](#0-7) 

### Recommendation
Do not derive the loss epoch from the single-slot `lastWithdrawRequest`. Iterate or track all epochs with `lossRecoveryPriceByEpoch[epoch] != 0` that have nonzero `withdrawsRequestsByEpoch[_user][epoch]` (e.g., store the set of loss epochs, or record per-user the oldest unsettled loss epoch), and in `claimWithdrawRequest` clear every such epoch's basis at its haircut price before paying the funded remainder. Alternatively, on a new `requestWithdraw`, force-settle any prior epoch whose `lossRecoveryPriceByEpoch` is nonzero so a stale haircut can never be overwritten by a newer `lastWithdrawRequest`.

### Proof of Concept
Foundry fork sketch against the deployed vault (setup mirrors `test/foundry/IdleCreditVault.t.sol` helpers `_depositWithUser`, `_startEpochAndCheckPrices`, `_stopEpochAndCheckPrices`):

```solidity
function testClaimLossAdjustedAfterNewRequestPaysPar() external {
    IdleCreditVault vault = IdleCreditVault(address(strategy));
    address attacker = makeAddr('attacker');
    _depositWithUser(attacker, 100_000 * ONE_SCALE, true);

    // epoch N buffer: attacker requests withdraw of half
    uint256 req1 = _requestWithdrawWithUser(attacker, trancheBal(attacker) / 2);

    // manager stops epoch N with a real loss -> collectWithdrawFunds stores
    // lossRecoveryPriceByEpoch[N] = 0.5e18 and vault receives only 50%
    _stopEpochWithLoss(0, /*loss covers ~50% of pending*/);

    // epoch N+1 buffer: attacker deposits a bit more and requests again.
    // This overwrites lastWithdrawRequest[attacker] = N+1, leaving
    // withdrawsRequestsByEpoch[attacker][N] haircutted-basis uncleared.
    _depositWithUser(attacker, 1_000 * ONE_SCALE, true);
    _requestWithdrawWithUser(attacker, trancheBal(attacker));

    // start + stop epochs N+1 and N+2 normally so the one-epoch wait passes
    _startEpochAndCheckPrices(1);
    _stopEpochAndCheckPrices(1, initialProvidedApr, _expectedFundsEndEpoch());

    uint256 balPre = underlying.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimWithdrawRequest();

    // BUG: req1 paid at PAR instead of req1 * 0.5
    assertEq(
        underlying.balanceOf(attacker) - balPre,
        req1 + req2,                          // full basis
        'loss-adjusted receipt paid at par'
    );
    // vault paid pendingBasis instead of pendingBasis * lossRecoveryPrice
    // -> later honest claimants' funded claims revert on insufficient balance.
}
```

Key assertions: `vault.lossRecoveryPriceByEpoch(N) == 0.5e18`, `vault.withdrawsRequestsByEpoch(attacker, N) == req1` remains nonzero even though the aggregate was paid at par, and a second honest user with a funded receipt sees their `claimWithdrawRequest` underflow/revert because the vault disbursed more than it collected via `collectWithdrawFunds`.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L282-293)
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
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L312-313)
```text
    amount += _claimLossAdjustedWithdrawRequest(_user);
    return amount + _claimFundedWithdrawRequest(_user);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L326-328)
```text
    if (IIdleCDOEpochVariant(idleCDO).epochEndDate() != 0 && (epochNumber <= lastWithdrawRequest[_user])) {
      revert NotAllowed();
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
