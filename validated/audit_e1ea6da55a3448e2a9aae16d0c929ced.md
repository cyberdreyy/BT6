### Title
Loss-adjusted withdraw receipts escape their haircut when the user re-requests in a later epoch - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`IdleCreditVault._claimLossAdjustedWithdrawRequest` only looks up `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` — i.e., it only ever applies the haircut to the *most recent* request epoch. If a user's loss-adjusted receipt was created in epoch N and the user submits a new `requestWithdraw` in a later epoch M before claiming, `lastWithdrawRequest[_user]` is overwritten to M. The epoch-N receipt is never cleared by the loss-adjusted path, stays inside the aggregate `withdrawsRequests[_user]`, and is later paid **at par** by `_claimFundedWithdrawRequest`, even though `collectWithdrawFunds` only collected the haircutted amount for it. This is the vault analog of the reported race between session destruction and callbacks: a stale epoch pointer lets a destroyed (haircutted) request be redeemed as if it were still fully funded.

### Finding Description
When `stopEpochWithDuration` underfunds pending withdrawals, `collectWithdrawFunds` stores `lossRecoveryPriceByEpoch[epochNumber] < RECOVERY_FULL` and zeroes `pendingWithdraws` (contracts/strategies/idle/IdleCreditVault.sol:411-429). The strategy only receives `_amount < pendingBasis` underlyings — the loss is meant to be socialized across that epoch's receipts.

The claim path in `claimWithdrawRequest` runs `_claimLossAdjustedWithdrawRequest`, which derives the claim epoch solely from `lastWithdrawRequest[_user]`: [1](#0-0) 

But `requestWithdraw` unconditionally sets `lastWithdrawRequest[_user] = currentEpoch` for every new request (line 282). `_clearWithdrawClaimForEpoch` does reset `lastWithdrawRequest` to 0 only when it clears the *matching* epoch (lines 832-836), so once the marker moves to epoch M, the epoch-N loss receipt is unreachable by the loss-adjusted path.

The receipt is not lost, though: `withdrawsRequestsByEpoch[_user][N]` and the aggregate `withdrawsRequests[_user]` still contain the epoch-N basis, and the user still holds the minted strategy-token receipt (minted 1:1 at request time, line 275). After epoch M's stopEpoch, `_claimFundedWithdrawRequest` pays `withdrawsRequests[_user]` in full and burns the receipt tokens (lines 338-349). The haircut recorded in `lossRecoveryPriceByEpoch[N]` is silently dropped.

### Impact Explanation
Direct theft / insolvency. Suppose pending basis of 100 USDC in epoch N is funded at 50% (`lossRecoveryPriceByEpoch[N] = 0.5e18`, strategy holds only 50 USDC for it). The attacker re-requests in epoch M, waits one epoch, and `claimWithdrawRequest` pays the epoch-N receipt at par — claiming 100 USDC against a 50 USDC reserve. The extra 50 USDC comes out of underlyings funded for other users' receipts (the same pooled `_transferFundedClaim` balance), so later legitimate claimants are left unpaid. The "one receipt one payout" and "loss waterfall" invariants are broken: a receipt that should have been haircut is redeemed at par. Quantified loss = `claimBasis_N * (RECOVERY_FULL - lossRecoveryPriceByEpoch[N]) / RECOVERY_FULL` per re-requesting user.

### Likelihood Explanation
Fully attacker-controlled sequence with no privileged collusion:
1. Attacker (KYC'd lender) requests a normal withdraw in epoch N.
2. Honest manager calls `stopEpochWithDuration` and the borrower underfunds → loss price stored, `pendingWithdraws = 0`.
3. Attacker makes a fresh deposit (or kept a second position) and calls `requestWithdraw` again in epoch M — `lastWithdrawRequest` is overwritten. Nothing in `requestWithdraw` forces claiming the old receipt first (the code explicitly allows stacking requests; see comment at lines 323-324).
4. After epoch M ends normally, `claimWithdrawRequest` reverts nothing: `epochNumber > lastWithdrawRequest` passes, the loss path sees `lossRecoveryPriceByEpoch[M] == 0` and returns 0, and the funded path pays the whole aggregate including the epoch-N basis at par.

Requirements: a realized loss epoch (`stopEpochWithDuration` with partial funding — a legitimate manager/borrower flow, not a malicious action) plus a second request, both routine operations. No reentrancy, oracle manipulation, or privileged behavior needed.

### Recommendation
Apply the haircut per epoch rather than only on `lastWithdrawRequest`. Concretely:
- In `_claimLossAdjustedWithdrawRequest` (or a wrapper around the funded claim), iterate or track all epochs with a nonzero `lossRecoveryPriceByEpoch` for the user — e.g., keep a per-user set/dirty flag, or have `requestWithdraw` refuse/re-route while the user's `lastWithdrawRequest` epoch has an unsettled loss price.
- Simplest fix: in `_claimFundedWithdrawRequest`, subtract any per-epoch basis whose `lossRecoveryPriceByEpoch` is nonzero from `normalAmount` before paying at par, and clear those entries, paying them at their recovery price in the same call.
- Alternatively, settle (auto-claim) the pending loss-adjusted receipt inside `requestWithdraw` before overwriting `lastWithdrawRequest`.

### Proof of Concept
Foundry fork PoC sketch (mirroring `test/foundry/IdleCreditVault.t.sol` helpers such as `_startEpochAndCheckPrices`, `_stopEpochAndCheckPrices`):

```solidity
function testLossAdjustedReceiptEscapesHaircut() external {
    IdleCreditVault vault = IdleCreditVault(address(strategy));
    uint256 amount = 10_000 * ONE_SCALE;

    // user1 (victim) and attacker both deposit and get AA tranches
    _depositWithUser(user1, amount, true);
    idleCDO.depositAA(amount); // attacker = address(this)
    _transferBurnedTrancheTokens(address(this), true);

    // Epoch N: both request normal withdraw during buffer
    vm.prank(user1);
    cdoEpoch.requestWithdraw(0, address(AAtranche));
    uint256 attackerReceipt = cdoEpoch.requestWithdraw(mintedAA / 2, address(AAtranche));

    _startEpochAndCheckPrices(0);

    // Borrower underfunds: stopEpochWithDuration collects only 50%
    uint256 pending = vault.pendingWithdraws();
    uint256 funded = pending / 2;
    deal(defaultUnderlying, borrower, funded);
    vm.prank(borrower);
    underlying.approve(address(cdoEpoch), funded);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(funded /* loss-adjusted funding */, ...);

    uint256 lossPrice = vault.lossRecoveryPriceByEpoch(vault.epochNumber());
    assertGt(lossPrice, 0);
    assertLt(lossPrice, vault.RECOVERY_FULL());

    // Epoch M: attacker requests again -> lastWithdrawRequest overwritten
    _startEpochAndCheckPrices(1);
    cdoEpoch.requestWithdraw(IERC20(AAtranche).balanceOf(address(this)), address(AAtranche));
    vm.warp(cdoEpoch.epochEndDate() + 1);
    _stopEpochAndCheckPrices(1, initialProvidedApr, _expectedFundsEndEpoch());

    // Claim: loss path is skipped (epoch M has no loss price),
    // funded path pays aggregate incl. epoch-N basis AT PAR
    uint256 balBefore = underlying.balanceOf(address(this));
    cdoEpoch.claimWithdrawRequest();
    uint256 got = underlying.balanceOf(address(this)) - balBefore;

    // Attacker received the full epoch-N basis instead of the 50% haircut,
    // draining reserves funded for user1's receipt.
    assertGt(got, attackerReceipt * lossPrice / vault.RECOVERY_FULL());
}
```

Expected result: the attacker's epoch-N basis is paid at par; a subsequent `claimWithdrawRequest` by `user1` reverts on `safeTransfer` (or pays less than entitled), demonstrating insolvency equal to the skipped haircut.

### Citations

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
