### Title
Stale scaled APR after `setEpochParams` duration change misprices withdraw-request and epoch interest - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
`IdleCDOEpochVariant` caches a duration-scaled APR in the strategy (`IdleCreditVault.lastApr`), computed once as `unscaledApr * (epochDuration + bufferPeriod) / epochDuration` inside `setAprsWithBuffer` (contracts/strategies/idle/IdleCreditVault.sol:217-220). When `epochDuration` is later changed via `setEpochParams`, that cached scaled APR is **not recomputed**, so all subsequent interest math (`_calcInterest`, `_calcInterestWithdrawRequest`, `expectedEpochInterest`, and the fixed receipt minted by `requestWithdraw`) is priced against a stale duration ratio. This is the same bug class as JLSEC-2026-159: a derived parameter (`log2unitSize` / scaled APR) goes stale when a related dimension changes while other values stay constant.

### Finding Description
- `_setScaledApr` writes `lastApr = unscaledApr * (epochDuration + bufferPeriod) / epochDuration` via `setAprsWithBuffer` (IdleCDOEpochVariant.sol:544-546, IdleCreditVault.sol:217-219).
- `setEpochParams` (IdleCDOEpochVariant.sol:117-125) updates `epochDuration` and `bufferPeriod` but never re-scales the APR. The code comment only warns that *bufferPeriod* must not change; nothing compensates for an `epochDuration` change.
- `stopEpochWithDuration` (IdleCDOEpochVariant.sol:520-530) does re-scale after stopping, but a plain `setEpochParams` call between stop and the next `startEpoch` — the normal parameter-update path — leaves `lastApr` stale.
- Interest is then computed as `amount * lastApr * epochDuration / 365 days` (`_calcInterest`, line 807-809), and withdraw-request interest as `... * duration / (duration + buffer)` (`_calcInterestWithdrawRequest`, lines 856-878). With a stale scale factor, the effective APR becomes `unscaledApr * (D_old + b) / D_old * D_new / (D_new + b)` instead of `unscaledApr`.

Concretely, for `D_old=30d, b=5d, D_new=60d`: every withdrawer's fixed receipt and the epoch's expected interest are inflated by a factor of `(35/30)*(60/65) ≈ 1.077` — about 7.7% excess on top of the intended APR for the entire period. For a longer change (e.g. 30d → 180d) the overpayment factor grows toward `(35/30)*(180/185) ≈ 1.135`, and approaches `(D_old+b)/D_old` asymptotically. Conversely a duration *decrease* under-accrues and shortchanges the new epoch's `expectedEpochInterest`.

### Impact Explanation
The receipt minted in `requestWithdraw` (IdleCDOEpochVariant.sol:773-791) is a **fixed** underlying claim added to `pendingWithdraws`, which the borrower must fund at the next `stopEpoch` via `collectWithdrawFunds`. An unprivileged KYC-passing lender (a `requestWithdraw` caller) can:

1. Wait for (or anticipate) the honest manager/owner calling `setEpochParams` to lengthen `epochDuration` while the pool is in the buffer phase.
2. Call `requestWithdraw` — the receipt is minted at the stale, inflated rate (excess ≈ `(D_old+b)(D_new) / (D_old(D_new+b)) − 1` of principal over the epoch).
3. Claim the inflated `pendingWithdraws` payout via `claimWithdrawRequest` after one epoch, paid from funds pulled from the borrower at `stopEpoch`.

The excess is a direct transfer of value to the withdrawer: either the honest borrower overpays (pulled by `getFundsFromBorrower`, line 408), or, if the borrower's repayment is capped/short, the shortfall triggers `_handleBorrowerDefault` and losses are socialized onto remaining LPs through the recovery path. This breaks the fair-mint/burn and one-receipt-one-payout invariants: the receipt no longer equals principal + contractually agreed interest.

### Likelihood Explanation
- Requires only an honest owner/manager `setEpochParams` duration change — a routine, documented operation (`stopEpochWithDuration` exists precisely because duration changes are expected). The attacker is an ordinary KYC'd lender sequencing around it, with no privileged action.
- Duration increases produce inflation on both withdraw receipts and `expectedEpochInterest`; the effect is proportional and deterministic, not rounding dust (multiple percent for a 2× change).
- Existing guards do not stop it: `setEpochParams` has no rescale or same-duration check; `requestWithdraw`/`startEpoch` happily consume the stale `lastApr`; `_skimDonatedAssets`, KYC, and epoch gating are irrelevant. There is no invariant check that `lastApr == unscaledApr * (epochDuration + bufferPeriod) / epochDuration`.
- Caveat: the attacker's gain is bounded by the inflated interest (not principal), and the borrower's honest repayment funds it — matching a Medium-severity economic-mispricing profile.

### Recommendation
In `setEpochParams`, re-scale the cached APR for the new duration, e.g. call `_setScaledApr(IdleCreditVault(strategy).unscaledApr())` after updating `epochDuration`/`bufferPeriod` (mirroring what `stopEpochWithDuration` does at line 527). Alternatively store only `unscaledApr` and compute the scaled value on the fly from current `epochDuration`/`bufferPeriod` at each use site (`_calcInterest`, `depositDuringEpoch`, `_calcInterestWithdrawRequest`, `writeOffDeposit`), eliminating the stale derived value entirely. Extend the existing code comment to cover `epochDuration`, not just `bufferPeriod`.

### Proof of Concept
Foundry fork test sketch (setup mirrors `test/foundry/IdleCreditVault.t.sol`):

```solidity
function testStaleScaledAprAfterDurationChange() external {
    // epochDuration = 30 days, bufferPeriod = 5 days, unscaledApr = 10e18 (10%)
    // lastApr was scaled at init/stop: lastApr = 10e18 * 35/30 = 11.667e18
    uint256 amount = 100_000 * ONE_SCALE;
    idleCDO.depositAA(amount);                       // KYC'd attacker deposit

    // honest manager lengthens the epoch between stop and next start
    vm.prank(manager);
    cdoEpoch.setEpochParams(60 days, 5 days);        // lastApr NOT rescaled

    // attacker requests withdraw; interest priced with stale scale
    uint256 expected = _interestAtApr(amount, 10e18, 60 days, 5 days); // intended
    uint256 requested = cdoEpoch.requestWithdraw(0, address(AAtranche));
    // requested - expected == amount * 10e18/100 * ((35/30)*(60/65) - 60/65) / 365d
    //  ≈ 7.7% more interest than the borrower agreed to pay
    assertGt(requested, expected);

    // borrower funds the inflated pendingWithdraws at stopEpoch;
    // attacker claims the excess via claimWithdrawRequest next epoch
}
```

Key assertions: `IdleCreditVault(strategy).lastApr()` remains `11.667e18` after `setEpochParams(60 days, 5 days)` instead of `10e18 * 65/60 = 10.833e18`, and `withdrawsRequestsByEpoch[user][epoch]` exceeds the correctly-priced receipt by the quantified factor.