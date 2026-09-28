### Title
Withdrawal receipt priced with governance-mutable `managementFee`/`fee` causes user loss when fee update front-runs `requestWithdraw` — (File: contracts/IdleCDOEpochVariant.sol)

### Summary
`requestWithdraw` fixes the withdrawal receipt's value at execution time by deducting an upfront management fee (for one full epoch plus remaining buffer) and a performance fee from principal + projected interest. Both fee parameters are stored values that the honest owner can update at any time. If the owner's fee update transaction is included in a block before a user's `requestWithdraw`, the user's receipt is locked at the new, higher fee — permanently reducing the claimable amount with no recourse, mirroring the AvailBridge `getFee(length)` front-run bug.

### Finding Description
In `IdleCDOEpochVariant.requestWithdraw`, the amount a user will be able to claim is computed and locked in at request time:

- `requestWithdraw` calls `_calcInterestWithdrawRequest` and then `_totalWithdrawFees(principal, interest)`, subtracts the fees, and passes the net `_underlyings` to `creditVault.requestWithdraw(...)`, which stores the receipt under `lastWithdrawRequest`/`withdrawsRequestsByEpoch` for the caller. The receipt is fixed: "The receipt is fixed now and leaves live NAV" (`contracts/IdleCDOEpochVariant.sol:772-790`).
- `_totalWithdrawFees` reads the live `managementFee` via `_calculateManagementFee(_principal, _withdrawRequestManagementFeeDuration())` and the live `fee` (performance) via `_netGainAfterFees` (`contracts/IdleCDOEpochVariant.sol:889-906`).
- `_withdrawRequestManagementFeeDuration` charges for a full `epochDuration` plus any remaining `bufferPeriod` (`contracts/IdleCDOEpochVariant.sol:925-931`), so the management-fee term is large by design.
- The owner can update both parameters at any time with no timelock and no per-user slippage check: `setFeeParams(address,uint256)` sets `fee` directly in `IdleCDO.sol:860-864`, and the credit-vault variant exposes an equivalent setter for `managementFee`/`feeSplit` (used in tests as `_setFeeParams(TL_MULTISIG, 10000, FULL_ALLOC, cdoEpoch.managementFee())`, `test/foundry/IdleCreditVault.t.sol:3689-3690`).
- There is no `maxFee`/`minReceived` parameter on `requestWithdraw` (`contracts/IdleCDOEpochVariant.sol:739-791`), and the preview function `maxWithdrawable` (`contracts/IdleCDOEpochVariant.sol:911-918`) uses the same live values, so a quote obtained in block N is not honored in block N+1.

Attack/loss sequence (running or buffer phase, any non-programmable epoch variant):

1. User reads `maxWithdrawable(user, AA)` → expects receipt `R`.
2. Honest owner submits `setFeeParams`/`_setFeeParams` raising `managementFee` and/or `fee` (e.g. 0% → `MAX_FEE`).
3. Owner's tx is ordered first; user's `requestWithdraw` executes next and `_totalWithdrawFees` now computes a much larger deduction, so the stored receipt is `R' < R`.
4. The difference accrues to `feeReceiver` via `pendingWithdrawFees` (`contracts/IdleCDOEpochVariant.sol:778`); the user's tranche tokens are already burned in `_withdrawOps` and the lower amount is the only claimable quantity.

### Impact Explanation
Permanent loss of user funds equal to `ΔmanagementFee * principal * (epochDuration + remainingBuffer) / year` plus `Δfee` applied to projected interest. With `MAX_FEE` the loss can approach the entirety of the receipt's interest/management-fee-covered portion. Unlike the AvailBridge case the tx won't revert (no `msg.value` check), so the loss is silent — worse for the user. No existing guard prevents it: `_checkNotAllowed` only checks allow-flags/KYC, `_skimDonatedAssets` only sweeps raw donations, and neither bounds fee charged at request time.

### Likelihood Explanation
Requires only that an honest governance fee update lands in front of a pending `requestWithdraw`. Fee updates are legitimate operations (tests exercise `_setFeeParams` routinely), and `requestWithdraw` is most commonly called exactly during buffer periods when parameter changes are also likely. The user bears the loss; no attacker capability is needed beyond being a normal tranche holder, consistent with the threat model (sequencing around honest privileged calls is allowed). Severity is bounded by `MAX_FEE` and the duration charged.

### Recommendation
Let the user bound the fee at request time, e.g. add `uint256 _minExpectedUnderlying` (or `maxFeeBps`) parameter to `requestWithdraw` and revert if `principal + interest - totalFees < _minExpectedUnderlying`. Alternatively cache/snapshot the fee parameters at `stopEpoch`/epoch boundaries so a request's economics are pinned to values observable before submission, matching the AvailBridge recommendation to use a cached fee rather than a live read.

### Proof of Concept
Foundry-style sketch against `test/foundry/IdleCreditVault.t.sol` harness:

```solidity
function testRequestWithdrawFeeFrontRun() external {
    // deposit and run epoch 0
    uint256 amountWei = 10000 * ONE_SCALE;
    idleCDO.depositAA(amountWei);
    _startEpochAndCheckPrices(0);

    // user preview under current fees
    uint256 expected = cdoEpoch.maxWithdrawable(address(this), idleCDO.AATranche());

    // honest owner fee update gets ordered before user's requestWithdraw
    _setFeeParams(TL_MULTISIG, 50000 /* performance 50% */, FULL_ALLOC, 20000 /* mgmt 20% */);

    // user's tx executes with new fees; receipt is lower than preview
    uint256 requested = cdoEpoch.requestWithdraw(0, address(AAtranche));
    assertLt(requested, expected);
    // loss accrued to feeReceiver accounting
    assertGt(cdoEpoch.pendingWithdrawFees(), 0);
}
```

The assertion `requested < expected` demonstrates the same un-cached live-fee read as `getFee(length)` in the AvailBridge report, with the loss quantified as `expected - requested` paid into `pendingWithdrawFees`.