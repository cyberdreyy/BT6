### Title
Missing slippage/deadline protection on `requestWithdraw` and `depositDuringEpoch` lets delayed execution reprice receipts/mints against changed fees, APR and NAV - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
The external report describes a transaction that is priced at execution time rather than at signing time: a delayed `flip` tx pays whatever `feePercentage` is current when it mines. The same defect exists in `IdleCDOEpochVariant`: neither `requestWithdraw` nor `depositDuringEpoch` accepts a `minAmountOut`/`deadline` parameter. The withdrawal receipt and the mid-epoch mint are both computed from live state (`fee`, `managementFee`, `trancheAPRSplitRatio`, scaled APR, `expectedEpochInterest`, `lastNAV`) at inclusion time, so a lender whose tx sits in the mempool during the buffer period can be charged a materially higher upfront management fee (or minted fewer tranche tokens) than quoted, with no way to bound or cancel.

### Finding Description
`requestWithdraw` (IdleCDOEpochVariant.sol:739-791) converts tranche tokens to underlyings at the current `_tranchePrice`, projects one epoch of interest via `_calcInterestWithdrawRequest` (lines 856-878), and subtracts `totalFees` computed in `_totalWithdrawFees` (lines 900-906). The upfront management fee is `_calculateManagementFee(_principal, _withdrawRequestManagementFeeDuration())`, where the duration is one `epochDuration` plus the remaining buffer (lines 925-931). Critically, when `_mgmtFee >= _interest` the fee charged is the full management fee, i.e. it eats into principal, not just yield.

`fee` and `managementFee` are mutable by the honest owner/manager via `setFeeParams`/`setManagementFee` (used freely in tests, e.g. `test/foundry/IdleCDOEpochQueue.t.sol:793`), and the tranche price and expected interest change on every `_updateAccounting()` and `stopEpoch`. A `requestWithdraw` transaction broadcast during the buffer period but included after a fee increase — or after a forced-loss `_updateAccounting`/`stopEpochWithDuration(_lossAmount)` reprices the tranche — produces a smaller, irreversible receipt: tranche tokens are burned in `_withdrawOps` and the receipt is minted 1:1 in the strategy (`IdleCreditVault.requestWithdraw`, lines 272-295). The user cannot reclaim the tranche tokens; the only recovery path is the haircutted claim.

`depositDuringEpoch` (lines 656-733) has the same shape: `_minted = (_amount + trancheInterest) * _trancheTotSupply / expectedFinal`, where `trancheInterest` is net of `_calculateManagementFee(_amount, remaining)` and `fee`. A delayed deposit mined after a fee increase mints fewer tranche tokens for the same underlying transferred.

### Impact Explanation
A lender requesting withdrawal of, e.g., 100,000 USDC of AA tranches with `epochDuration = 30 days`, `bufferPeriod = 5 days` pays `principal * managementFee * ~35/365` upfront. If `managementFee` is raised from 0 to 10% (10000 bps) while the tx is pending, the receipt shrinks by ~959 USDC that is credited to `pendingWithdrawFees` and paid to `feeReceiver`/owner at `stopEpoch`. Since `_totalWithdrawFees` charges the full management fee whenever it meets or exceeds projected interest, the loss is unbounded by yield and scales with `epochDuration` and the fee rate — at the 100% cap roughly ~9.6% of principal per 35-day request window. This is a direct, quantified loss of user funds extracted through an honest privileged parameter change the user had no deadline to reject.

### Likelihood Explanation
Likelihood is medium-to-low: it requires the user's transaction to be delayed (mempool congestion, low gas, builder reordering) across an honest owner/manager fee or parameter change during the buffer period — exactly the sequencing the threat model permits (sequence around owner/manager calls; they need not be malicious). Fee changes during a live credit vault's buffer window are a normal operational action, and withdraw requests are only enabled during the buffer, so the exposure window coincides with the precise period in which users batch their request transactions. No existing guard helps: `_checkNotAllowed` gates only `allowAA/BBWithdrawRequest` and KYC; there is no staleness check on `fee`, `managementFee`, or the computed `_underlyings`.

### Recommendation
Add a `minAmountOut` parameter (and optionally a `deadline`/`block.timestamp` expiry) to `requestWithdraw` and `depositDuringEpoch`, reverting when the computed `_underlyings`/`_minted` is below the caller's bound or when the tx is mined after the deadline. Alternatively, snapshot and expose a `requestFeeNonce`/`paramsHash` that users can pin. This gives lenders the same execution-time control the report recommends for `flip`.

### Proof of Concept
Foundry-style sketch against the epoch variant (buffer period, fee change lands before the delayed request):

```solidity
// test/foundry/NoDeadlineRequestWithdraw.t.sol
function testRequestWithdraw_noDeadline_feeIncrease() public {
    // setup: epoch 1 running, user holds AA tranches
    uint256 amount = 100_000 * ONE_SCALE;
    uint256 trancheAmount = idleCDO.depositAA(amount);

    // stop epoch so requests are enabled (buffer period)
    _stopCurrentEpochWithApr(10e18);   // stopEpoch + epochNumber bump

    // --- user signs requestWithdraw here; tx sits in mempool ---
    uint256 quoted = cdoEpoch.requestWithdraw(trancheAmount, address(AAtranche));
    // revert state manually in test (snapshot/expect): quoted = principal + interest - fee

    // honest owner raises managementFee while user tx is pending
    vm.prank(owner);
    cdoEpoch.setManagementFee(10_000); // 10% annualized (bps) — check exact scale in setFeeParams

    // user's delayed tx finally executes -> priced against NEW fee
    uint256 actual = cdoEpoch.requestWithdraw(trancheAmount, address(AAtranche));

    uint256 expectedFee = _calcManagementFee(
        trancheAmount * cdoEpoch.tranchePrice(address(AAtranche)) / 1e18,
        /* epochDuration + remaining buffer */ ~35 days);
    assertLt(actual, quoted);
    assertEq(quoted - actual, expectedFee - oldFee, "user paid increased fee with no minOut");
}
```

The same pattern applies to `depositDuringEpoch`: snapshot the quoted `_minted`, have the owner raise `fee`/`managementFee`, then mine the delayed deposit and assert fewer tranche tokens are minted for identical underlying. Uncertainty: the exact setter names/signatures (`setFeeParams` argument order, whether `managementFee` can be raised during the buffer only vs. also mid-epoch) should be confirmed in `IdleCDO.sol`/`IdleCDOCreditVault.sol`, but tests confirm `setFeeParams` is callable by the owner while withdraw requests are open.