### Title
Claimed instant-withdraw receipts are never removed from the per-epoch host tables, so a same-epoch default inflates the recovery claim basis and dilutes/steals `defaultRecoveryReserve` — (`contracts/strategies/idle/IdleCreditVault.sol`)

### Summary
This is the direct analog of the `fd_renumber` leak: the guest-facing table (`instantWithdrawsRequests[_user]`, plus the burned receipt tokens) is cleaned correctly on claim, but the underlying "host" tables — `instantWithdrawsRequestsByEpoch[_user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]` — are never decremented when a funded instant withdraw is claimed via `claimInstantWithdrawRequest`. If the pool then defaults while `epochNumber` still equals that epoch and `pendingInstantWithdraws != 0`, `defaultPendingClaimBasis()` re-counts already-paid receipts, inflating `totalBasis` in `finalizeDefaultRecovery`, which lowers `defaultRecoveryPrice` and redistributes recovery funds away from legitimate claimants (including active LPs whose tranche NAV is set from the same ratio).

### Finding Description
`requestInstantWithdraw` records three layers of state: the user aggregate `instantWithdrawsRequests[_user]`, the per-epoch user entry `instantWithdrawsRequestsByEpoch[_user][currentEpoch]`, and the per-epoch global `instantWithdrawClaimsByEpoch[currentEpoch]` (lines 366–374). `claimInstantWithdrawRequest` (lines 380–393) burns the user's receipt tokens, zeroes `instantWithdrawsRequests[_user]`, and pays out via `_transferFundedClaim` — but it leaves both per-epoch entries untouched. Those entries are only ever cleared inside `_claimDefaultedInstantWithdrawRequest` (lines 842–856), which runs exclusively after `defaultRecoveryFinalized && defaultInstantWithdrawsFinalized`.

Two consequences:

1. **Inflated default basis.** `defaultPendingClaimBasis` (lines 644–649) adds `instantWithdrawClaimsByEpoch[epochNumber]` whenever `pendingInstantWithdraws != 0`. A user who already claimed a funded instant receipt still contributes their full amount to that sum. `finalizeDefaultRecovery` then computes `recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis` with `totalBasis` overstated (lines 679–692), so every genuine defaulted-epoch claimant and every active LP (via `_burn`/`_mint` of the CDO balance at line 701–705 and `defaultBBNav`) receives a smaller recovery ratio than warranted. The reserve is finite; earlier claimants are overrepresented and later claimants are underpaid — a net transfer inside the recovery waterfall.
2. **Underflow DoS on the already-claimed user.** If that same user somehow still has a claim path executed, `_claimDefaultedInstantWithdrawRequest` computes `instantWithdrawsRequests[_user] -= claimBasis` with the aggregate already zeroed (line 848), which underflows and reverts — but the primary impact is (1).

Sequence (buffer/running epoch N, instant mode enabled): user requests instant withdraw → manager calls `getInstantWithdrawFunds` → `collectInstantWithdrawFunds` partially or fully funds receipts (`pendingInstantWithdraws` decreased by funded part; `instantWithdrawClaimsByEpoch[N]` unchanged) → user calls `claimInstantWithdrawRequest` and is paid → borrower fails to repay and `stopEpoch`/default path runs within epoch N with some instant receipts still unfunded → `_handleBorrowerDefault`/`finalizeDefaultRecovery` counts the already-paid amount again in `defaultPendingClaimBasis`.

### Impact Explanation
Quantified loss = the sum of already-claimed instant receipt amounts in the defaulting epoch, distributed pro rata as a haircut on every other recovery claimant. With `C` claimed-but-stale instant basis and real basis `B`, the stored `defaultRecoveryPrice` is `R * 1e18 / (B + C)` instead of `R * 1e18 / B`; legitimate claimants lose `C/(B+C)` of their recovery, and the tail of claimants may get nothing once the reserve drains — theft of unclaimed recovery yield, satisfying the impact bar. No privileged misbehavior is needed: the funding flow, claim, and borrower default are all honest-call sequences.

### Likelihood Explanation
Requires an instant-withdraw-enabled (non-programmable) deployment, at least one claimed funded instant receipt plus a still-unfunded remainder (`pendingInstantWithdraws != 0`), and a borrower default in the same epoch — plausible whenever `getInstantWithdrawFunds` only partially sources the queue before the borrower fails. No existing guard stops it: `requestWithdraw`'s stale-receipt revert only covers `lossRecoveryPriceByEpoch`/APR0 paths, and nothing clears the instant per-epoch entries on a normal claim.

### Recommendation
In `claimInstantWithdrawRequest`, iterate the user's non-zero `instantWithdrawsRequestsByEpoch` entries (or track the user's outstanding request epochs) and zero them, subtracting the claimed amount from `instantWithdrawClaimsByEpoch[epoch]`, so the funded-claim path cleans up the same bookkeeping that `requestInstantWithdraw` created. Alternatively, maintain a single "unclaimed instant claims" counter used by `defaultPendingClaimBasis` that is decremented on every funded claim, ensuring the default basis only includes receipts that were never paid.

### Proof of Concept
Foundry fork PoC (scaffolded on `test/foundry/IdleCreditVault.t.sol` helpers `_startEpochAndCheckPrices`, `_stopEpochAndCheckPrices`, `_getInstantFunds`, `_checkDefault`):

```solidity
function testStaleInstantEpochBasisDilutesRecovery() external {
    uint256 amountWei = 10000 * ONE_SCALE;
    idleCDO.depositAA(amountWei);              // attacker = unprivileged KYC'd lender
    // run epoch 0 with APR drop so instant withdraws are enabled
    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr / 2, _expectedFundsEndEpoch());
    uint256 requested = cdoEpoch.requestWithdraw(0, address(AAtranche)); // instant path

    _startEpochAndCheckPrices(1);
    vm.warp(block.timestamp + cdoEpoch.instantWithdrawDelay() + 1);
    // fund only PART of the instant queue so pendingInstantWithdraws > 0 afterwards
    _getInstantFunds();                        // collectInstantWithdrawFunds(partial)
    uint256 balPre = IERC20Detailed(defaultUnderlying).balanceOf(address(this));
    cdoEpoch.claimInstantWithdrawRequest();    // paid in full; per-epoch tables NOT cleaned
    assertGt(IdleCreditVault(address(strategy)).instantWithdrawsRequestsByEpoch(address(this), strategy.epochNumber()), 0);

    // borrower defaults in the SAME epoch while pendingInstantWithdraws != 0
    _checkDefault();                           // -> finalizeDefaultRecovery
    // instantWithdrawClaimsByEpoch[epochNumber] still includes `requested`,
    // so totalBasis > real unpaid basis and defaultRecoveryPrice is diluted.
    // Other claimants' (claimBasis * defaultRecoveryPrice / 1e18) is reduced
    // by ~ requested / totalBasis.
}
```

Key assertion after default: `strategy.instantWithdrawClaimsByEpoch(strategy.epochNumber())` remains ≥ `requested` even though `instantWithdrawsRequests[address(this)] == 0` and the tokens were already paid — proving the claimed basis is double-counted in `finalizeDefaultRecovery`.