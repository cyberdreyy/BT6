### Title
Instant-withdraw path in `requestWithdraw` bypasses interest-share adjustment (`diff`) and performance fees, misallocating yield between tranches and fee recipients - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
`IdleCDOEpochVariant.requestWithdraw` has two mutually exclusive exits. The normal path computes `(interest, diff)` via `_calcInterestWithdrawRequest`, deducts `_totalWithdrawFees` (management + performance fee), credits `pendingWithdrawFees`, and adjusts `interestForOverUnderPerformance` by `diff` (contracts/IdleCDOEpochVariant.sol:772-790). The instant-withdraw path — taken whenever `lastEpochApr > currentApr + instantWithdrawAprDelta` — skips all of this and books the full gross tranche value into `IdleCreditVault.requestInstantWithdraw` (contracts/IdleCDOEpochVariant.sol:761-769). Like the Overlay bug (liquidation gate uses one accounting basis, fee payout uses another), the same economic action — exiting the vault — is valued and fee-assessed by two inconsistent methods depending on a collateral condition, so value that should flow to the opposite tranche and to `feeReceiver` is silently kept by the instant withdrawer or lost.

### Finding Description
In `requestWithdraw`:

```solidity
if (_isInstantWithdrawEnabled()) {
  uint256 currentApr = creditVault.unscaledApr();
  if (lastEpochApr > (currentApr + instantWithdrawAprDelta)) {
    creditVault.requestInstantWithdraw(_underlyings, msg.sender);
    _withdrawOps(_amount, _underlyings, _tranche);   // burns NAV by FULL underlyings
    return _underlyings;
  }
}
uint256 principal = _underlyings;
(uint256 interest, int256 diff) = _calcInterestWithdrawRequest(_underlyings, _tranche);
uint256 totalFees = _totalWithdrawFees(principal, interest);
_underlyings = principal + interest - totalFees;
pendingWithdrawFees += totalFees;
interestForOverUnderPerformance += diff;
creditVault.requestWithdraw(_underlyings, msg.sender, principal);
```

Two inconsistencies:

1. **`diff` is never applied on the instant path.** `_calcInterestWithdrawRequest` returns `diff = interestWithoutSplitRatio - interest` — positive for AA (the over-performance a withdrawn AA position would have generated for BB) and negative for BB (interest that must be removed from `expectedEpochInterest` since BB principal left). `interestForOverUnderPerformance` is consumed at `startEpoch` to correct `expectedEpochInterest`. An AA instant withdrawal fails to add the positive `diff`, so BB holders lose the over-performance that the freed TVL share should have produced; a BB instant withdrawal fails to subtract the negative `diff`, so `expectedEpochInterest` stays inflated while `_withdrawOps` already removed the principal from NAV — the next epoch's interest expectation includes yield on capital that no longer exists.
2. **Performance/management fees are never charged.** `_tranchePrice` already embeds accrued net interest after `_updateAccounting()`, so an instant withdrawer extracts principal *plus* accrued interest with zero `fee` (performance) and zero `managementFee`, while an identical withdrawal through the normal path pays both via `_totalWithdrawFees` (IdleCDOEpochVariant.sol:900-906). Those fees would otherwise accrue to `pendingWithdrawFees`/`unclaimedFees` and be minted to `feeReceiver` as AA shares (`_depositFees`).

### Impact Explanation
- **AA instant withdraw:** BB tranche permanently loses the `diff` over-performance for the rest of the epoch cycle; quantified as `diff ≈ interestWithoutSplitRatio − AAShare`, i.e. the junior-premium portion of the withdrawn position's epoch interest.
- **BB instant withdraw:** `expectedEpochInterest` is overstated by `|diff|`; at `startEpoch` the borrower-facing interest obligation is computed on NAV that already excluded the withdrawn principal, producing either an over-claim on the borrower (insolvency pressure on honest borrower funding) or an uncollectible expectation that breaks the loss/interest waterfall in `previewLossAdjustedWithdrawFunds`/`_lossActiveBasis`, which reconcile `expectedEpochInterest` against `pendingWithdrawFees`.
- **Fee loss:** `fee%` of accrued interest plus `managementFee` on the receipt duration is not collected on instant exits — direct loss of unclaimed yield to `feeReceiver` and, via `_depositFees` minting, dilution/misallocation between remaining LPs.

The trigger is unprivileged: any KYC-passed tranche holder calls `requestWithdraw` during a buffer period right after the manager lowers `unscaledApr` (a routine, honest operation each rate reset). No privileged misbehavior is required.

### Likelihood Explanation
Medium-high. APR reductions are a normal recurring event; whenever `lastEpochApr > currentApr + instantWithdrawAprDelta` the instant branch is taken for *every* request in that window, deterministically skipping `diff` and fees. Whether the zero-fee treatment is intended is uncertain — the code comments justify only skipping *buffer-period* interest, not skipping the tranche-split correction or the performance fee on already-accrued interest — but the missing `interestForOverUnderPerformance` adjustment has no plausible justification since NAV and expected interest become permanently inconsistent.

### Recommendation
Mirror the fix pattern from the external report (align fee/solvency accounting on a single basis): on the instant path, compute `(interest, diff) = _calcInterestWithdrawRequest(_underlyings, _tranche)` before branching, apply `interestForOverUnderPerformance += diff`, deduct `_totalWithdrawFees(principal, interest)` (or at minimum the performance-fee component on accrued interest), and pass the net amount to `requestInstantWithdraw` so pending instant claims and NAV burns stay consistent with the normal path.

### Proof of Concept
Foundry fork PoC sketch (against `test/foundry/IdleCreditVault.t.sol` harness):

```solidity
// 1. Deposit AA + BB, run one epoch, stopEpoch so lastEpochApr = providedApr.
// 2. Manager lowers unscaledApr by > instantWithdrawAprDelta (normal rate reset).
// 3. Attacker (KYC'd AA holder) calls cdoEpoch.requestWithdraw(bal, AAtranche)
//    during the buffer -> takes instant branch.
// Assert: creditVault.instantWithdrawsRequests(attacker) == full _trancheToUnderlyings
// Assert: cdoEpoch.pendingWithdrawFees() unchanged (fees skipped)
// Assert: cdoEpoch.interestForOverUnderPerformance() unchanged (diff skipped),
//         while an equivalent normal-path request would have increased it by diff.
// 4. startEpoch -> expectedEpochInterest misses the AA over-performance;
//    after stopEpoch, BB virtualPrice is strictly lower than the
//    normal-path baseline; loss = diff + performanceFee(interest).
```

Caveat: I could not fully trace `startEpoch`'s consumption of `interestForOverUnderPerformance` or confirm in-repo documentation that instant withdrawals are intended to be fee-free; if fee-free instant exit is a documented design choice, the `diff` omission remains a standalone inconsistency regardless.