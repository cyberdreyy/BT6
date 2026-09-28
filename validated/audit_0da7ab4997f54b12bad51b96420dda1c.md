### Title
`availableToBorrow` reserves only `pendingWithdraws`, not unfunded instant-withdraw liabilities, letting a routine borrower draw cash the epoch owes instant redeemers and forcing a spurious default - (File: contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary
The kernel bug is a bounds check that validates the visible payload fits in `PAGE_SIZE` but forgets the mandatory `skb_shared_info` tailroom, so a "legal" frame overruns metadata. The direct analog is `ProgrammableBorrower.availableToBorrow()`/`_borrow()`: the borrow cap reserves only `epochPendingWithdraws` (normal withdraw receipts passed in via `onStartEpoch(_pendingWithdraws)`), while the facility's other same-epoch cash obligations — the unfunded remainder of `pendingInstantWithdraws` — sit in the unreserved "tailroom". A fully honest borrower calling `borrow(0)` legitimately drains the liquidity needed to fund instant claims; the honest manager's `getInstantWithdrawFunds()` then fails after `instantWithdrawDeadline` and `_handleBorrowerDefault` fires even though the borrower was solvent.

### Finding Description
In `IdleCDOEpochVariant.startEpoch()`, pending instant withdraws are only partially prefunded from on-hand cash (`collectInstantWithdrawFunds(min(pendingInstant, totUnderlyings))`, IdleCDOEpochVariant.sol:279-282), and the surplus is pushed to the borrower via `sendFundsToBorrower`. The programmable-borrower hook is then invoked with `_pendingWithdraws = _strategy.pendingWithdraws()` (line 281, 288, 297), which is only the *normal* receipt bucket — `pendingInstantWithdraws` is tracked separately in `IdleCreditVault` and the unfunded portion is expected to be pulled from the borrower later via `getInstantWithdrawFunds()` during the running epoch.

`ProgrammableBorrower.onStartEpoch` stores that number verbatim as `epochPendingWithdraws` (ProgrammableBorrower.sol:205), and `availableToBorrow()` computes `totalAssets - epochPendingWithdraws` (lines 350-355). `_borrow()` caps draws at that value (lines 438-445). Nothing reserves:

- `pendingInstantWithdraws` remainder that `IdleCDOEpochVariant.getInstantWithdrawFunds()` must pull from the borrower before `instantWithdrawDeadline`;
- any buffer-period liabilities accrued between the reservation snapshot and the actual draws.

So the reservation check — like the kernel's `linear size <= PAGE_SIZE` check — accounts for the declared payload but not the mandatory overhead that must remain behind it.

Attack trace (programmable-borrower mode, buffer phase → running epoch):
1. Attacker, a KYC-passing lender holding AA tranche tokens, queues a small normal `requestWithdraw` plus a large instant withdraw during the buffer period so `pendingInstantWithdraws` exceeds what `startEpoch` can prefund.
2. Honest owner/manager calls `startEpoch()`. Prefunding covers part of instant claims; the rest (`U` underlyings) is deferred to `getInstantWithdrawFunds`. `onStartEpoch` reserves only `pendingWithdraws` (`P`), with `U` not included.
3. The honest `borrower` (or an authorized executor via `executeBorrow`) calls `borrow(0)` — a permitted, non-adversarial action — drawing `totalAssets - P`, which consumes the `U` tailroom.
4. Manager calls `getInstantWithdrawFunds()` before `instantWithdrawDeadline`; the borrower pull fails (shortfall > on-hand, and vault shares were also drawn). After the deadline, `_handleBorrowerDefault` triggers a default on a solvent facility.

### Impact Explanation
The epoch is forced into default despite the borrower having sufficient total assets — the liability was simply not reserved. Consequences:

- Instant redeemers' claims are frozen/haircut through the default-recovery path (`defaultRecoveryReserve`, `finalizeDefault` haircuts) instead of being paid at par.
- All active LPs take a pro-rata/BB-first loss via `_updateAccounting`/`_handleBorrowerDefault`, i.e., a real NAV loss (insolvency accounting) rather than a timing delay — the "corruption of adjacent metadata" analog of overwriting `skb_shared_info`.
- `pendingClaims`/epoch gating in `IdleCDOEpochQueue` blocks subsequent claim processing, extending the freeze.

Quantified loss: every instant claimant loses `(1 - recoveryRatio) * claimBasis`, and tranche holders absorb the residual haircut; the magnitude equals the unreserved `U` drawn by the borrower.

### Likelihood Explanation
High. No privileged misbehavior is required: the attacker only makes ordinary `requestWithdraw`/instant requests (allowed to any KYC'd tranche holder), and the borrower merely exercises its normal `borrow` right — which the rules treat as an honest actor whose calls we sequence around. The invariant test `invariant_availableToBorrowMatchesFreeAssets` confirms the reservation equals `epochPendingWithdraws` only, so the gap is systematic, not a race. The only mitigation is that the borrower *chooses* not to draw the full amount — an assumption the contract's own cap contradicts.

### Recommendation
Include all same-epoch cash obligations in the reserve, not just normal receipts — the equivalent of checking `SKB_WITH_OVERHEAD(PAGE_SIZE)` instead of `PAGE_SIZE`:

- In `IdleCDOEpochVariant.startEpoch`, pass `_strategy.pendingWithdraws() + unfundedInstant` to `onStartEpoch`, where `unfundedInstant = pendingInstant - prefundedAmount` (i.e., `pendingInstant - min(pendingInstant, totUnderlyings)`).
- Alternatively, inside `ProgrammableBorrower.availableToBorrow()`, add the unfunded instant leg reported by the strategy/CDO (and any `pendingWithdrawFees` payable in cash in non-minted mode) to `epochPendingWithdraws` before computing free liquidity.
- Recompute the reserve when instant claims are funded (`getInstantWithdrawFunds` should decrease the reserved amount symmetrically) to avoid over-reserving.

### Proof of Concept
Foundry fork PoC (programmable borrower variant, mocked ERC4626 vault):

```solidity
function testBorrowDrainsUnreservedInstantClaims() public {
    // 1. KYC'd attacker deposits, epoch 0 runs normally, stopEpoch -> buffer period
    _depositWithUser(attacker, 1_000_000e6, true);
    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, apr, expected);

    // 2. Buffer: attacker files small normal + large instant withdraw
    vm.startPrank(attacker);
    cdo.requestWithdraw(smallTranche, address(AAtranche));      // -> pendingWithdraws = P
    cdo.requestWithdraw(0, address(AAtranche));                 // instant path -> pendingInstantWithdraws = U (mostly unfunded)
    vm.stopPrank();

    // 3. Manager starts epoch: only P is reserved via onStartEpoch
    vm.prank(manager);
    cdo.startEpoch();
    uint256 unfundedInstant = strategy.pendingInstantWithdraws();
    assertGt(unfundedInstant, 0);
    assertEq(borrower.epochPendingWithdraws(), P); // U missing from reserve

    // 4. Honest borrower draws everything "available" -> consumes U tailroom
    uint256 avail = borrower.availableToBorrow();
    vm.prank(realBorrower);
    borrower.borrow(avail); // drains on-hand + vault down to P

    // 5. Manager tries to fund instant claims before deadline -> pull fails
    vm.warp(block.timestamp + cdo.instantWithdrawDelay() + 1);
    vm.prank(manager);
    cdo.getInstantWithdrawFunds();

    // 6. Spurious default on a solvent facility
    assertTrue(cdo.defaulted());
}
```

Caveat: I could not fully re-verify within the iteration budget whether `pendingInstantWithdraws` is ever folded into `pendingWithdraws()` inside `IdleCreditVault` (the epoch variant treats them as separate quantities at lines 279-281, and `_ensureDefaultRecoveryInitialized` separately guards `pendingInstantWithdraws != 0`, strongly indicating disjoint buckets). If they were summed, the reserve would already cover the instant leg and the finding would reduce to the narrower `pendingWithdrawFees` gap.