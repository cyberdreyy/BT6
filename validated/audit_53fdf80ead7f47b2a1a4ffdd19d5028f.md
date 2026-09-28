### Title
Attacker front-runs `stopEpochWithDuration` `_lossAmount` with an instant-withdraw request to exit at pre-loss price - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
The Bancor report describes a sandwich around a privileged parameter change that alters the effective sell price: buy before the change, sell after, pocketing the difference. In `idle-tranches` the strongest analog is `IdleCDOEpochVariant.stopEpochWithDuration(_newApr, _interest, _duration, _lossAmount)`. When the manager stops an epoch with a realized `_lossAmount`, the loss is socialized across active positions (BB-first) and pending *normal* withdraw receipts via `lossRecoveryPriceByEpoch`. However, **instant-withdraw receipts are burned at the pre-loss tranche price and are excluded from the loss basis**, so a tranche holder who sees the loss-bearing `stopEpochWithDuration` in the mempool can front-run it with `requestWithdraw`, route through the instant-withdraw path, and claim the full pre-loss amount funded later by the borrower — escaping the loss entirely while remaining LPs absorb it.

### Finding Description
During the buffer phase (`isEpochRunning == true`, `allowAAWithdrawRequest`/`allowBBWithdrawRequest` true), `requestWithdraw` calls `_updateAccounting()` and `_trancheToUnderlyings(_amount, _tranche)` at the *current* (pre-loss) tranche price. When the new epoch APR is lower than the previous one (and `disableInstantWithdraw`/`isProgrammableBorrower` are false), the request is routed to the instant-withdraw path, which burns the tranche tokens immediately and records a fixed claim `instantWithdrawsRequests[_user] += amount` in `IdleCreditVault` at the pre-loss valuation.

When the manager's `stopEpochWithDuration` then executes:

- `IdleCreditVault.previewLossAdjustedWithdrawFunds(_lossAmount)` computes the pending-receipt haircut using `pendingBasis = pendingWithdraws` — `pendingInstantWithdraws` is a separate counter and is **not** part of the pending basis, so instant receipts receive no haircut.
- `_strategy.burnStrategyTokens(_lossAmount)` plus `_forceUpdateAccounting()` applies the remaining loss BB-first to active NAVs, lowering `priceAA`/`priceBB`.
- The attacker's tranche tokens are already burned; their claim is denominated in underlying at the pre-loss price.

Later, `getInstantWithdrawFunds()` pulls the full `pendingInstantWithdraws` amount from the borrower and `claimInstantWithdrawRequest` pays the attacker par value. In contrast, honest users holding normal `withdrawsRequests` for that epoch can only claim `claimBasis * lossRecoveryPriceByEpoch[epoch]` — i.e., they eat a pro-rata haircut, and active holders eat the active-side loss. The attacker converts what should have been a shared loss into an externality dumped entirely on everyone else.

Preconditions:
- Vault in epoch/buffer phase with instant withdrawals enabled (i.e., a pending APR decrease scenario, `disableInstantWithdraw == false`, non-programmable borrower).
- Attacker is a KYC'd tranche holder (`isWalletAllowed` passes) — an explicitly allowed unprivileged actor.
- Loss amount < active basis so `stopEpochWithDuration` does not revert or trigger `_emergencyShutdown`/`_handleBorrowerDefault` (which would disable AA requests); a partial loss within BB coverage is sufficient.

### Impact Explanation
Direct theft/value transfer equal to the loss share the attacker dodges. Concretely, if the attacker holds tranche value `V` and the epoch loss implies a post-loss tranche price haircut of `h%`, they extract `V` at pre-loss price instead of `V * (1 - h)`, stealing `V * h` from the borrower-funded instant-withdraw pull and from remaining holders whose `lastNAV`/`priceAA`/`priceBB` absorb the full `_lossAmount`. For a BB holder in a loss that partially exhausts the junior tranche this can approach 100% of their position value dodged. Broken invariant: loss waterfall / fair loss socialization — identical-epoch claims are paid at different prices depending only on mempool ordering.

### Likelihood Explanation
`stopEpochWithDuration` with a nonzero `_lossAmount` is a manager transaction visible in the public mempool; any KYC'd holder monitoring it can atomically front-run with `requestWithdraw` in the same block (no same-block guard exists on `requestWithdraw`, only on the base `_withdraw`). It requires the instant-withdraw route to be open, which is exactly the configuration where APR is being decreased — a common co-occurrence with realized losses, since managers cut APR and realize losses in the same `stopEpochWithDuration` call. Requires no flash loan even; the attacker already holds tranche tokens.

### Recommendation
Include `pendingInstantWithdraws` in the loss basis used by `previewLossAdjustedWithdrawFunds`/`collectInstantWithdrawFunds`, so instant receipts receive the same `lossRecoveryPriceByEpoch` haircut as normal receipts. Alternatively, disallow the instant-withdraw route (or all `requestWithdraw`) once the epoch end is reached and the stop transaction is pending, or defer pricing of requests made in the buffer window to post-stop prices.

### Proof of Concept
```solidity
// SPDX-License-Identifier: AGPL-3.0
pragma solidity ^0.8.0;

// In test/foundry/IdleCreditVault.t.sol harness
function testFrontRunLossWithInstantWithdraw() external {
    uint256 amount = 10_000 * ONE_SCALE;
    address attacker = makeAddr('attacker'); // KYC'd BB holder
    _depositWithUser(attacker, amount, false); // BB tranche
    idleCDO.depositAA(amount);

    // enable instant withdraw route (APR decrease scenario)
    uint256 instantDelay = cdoEpoch.instantWithdrawDelay();
    vm.prank(manager);
    cdoEpoch.setInstantWithdrawParams(instantDelay, 1000, false);

    // run epoch 0 normally
    _startEpochAndCheckPrices(0);
    vm.warp(cdoEpoch.epochEndDate() + 1);

    uint256 lossAmount = 2_000 * ONE_SCALE; // partial loss, covered by BB
    uint256 duration = cdoEpoch.epochDuration();

    // --- MEV: attacker sees manager's stopEpochWithDuration(.., lossAmount)
    //     in the mempool and front-runs it in the same block ---
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(0, cdoEpoch.BBTranche()); // routes to instant withdraw, burns at PRE-loss price

    // manager's tx lands
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(0, 0, duration, lossAmount);

    // owner funds instant withdraws at par after delay
    vm.warp(block.timestamp + cdoEpoch.instantWithdrawDelay() + 1);
    _getInstantFunds();

    uint256 balPre = IERC20Detailed(defaultUnderlying).balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest();
    uint256 paid = IERC20Detailed(defaultUnderlying).balanceOf(attacker) - balPre;

    // attacker was paid at pre-loss price despite holding through the loss epoch
    uint256 preLossPrice = ONE_SCALE; // ~= 1e18 before loss
    uint256 dodged = amount - amount * cdoEpoch.virtualPrice(address(BBtranche)) / ONE_SCALE;
    assertGt(dodged, 0, 'no loss dodged');
    // paid reflects full pre-loss basis, not the haircut that normal receipts received
    assertApproxEqAbs(paid, amount * preLossPrice / ONE_SCALE, 2, 'attacker paid at par');
}
```

Expected result: `paid` equals the attacker's full pre-loss underlying value while `virtualPrice(BBTranche)` dropped by the active-side loss and `lossRecoveryPriceByEpoch[epoch]` is < `RECOVERY_FULL` for normal receipts — confirming asymmetric loss escape.