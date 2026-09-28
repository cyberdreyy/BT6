### Title
Loss-adjusted and default-recovery claim payouts round down per user while the funded basis is cleared in full, permanently trapping the residual underlying in `IdleCreditVault` - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
This is the same bug class as the Derby `withdrawAllowance()` report: a global accounting bucket (`reservedFunds` there; `pendingWithdraws` / `defaultRecoveryReserve` here) is cleared or decremented using the full recorded basis, while users are paid a per-claim rounded-down amount. The difference accumulates in the strategy's underlying balance with no withdrawal path, and `defaultRecoveryReserve` residue additionally blocks funded claims via the `_transferFundedClaim` solvency guard.

### Finding Description
`collectWithdrawFunds` collects a single aggregate funded amount for all pending receipts. On a partial (loss-adjusted) funding it stores `lossRecoveryPriceByEpoch[epochNumber] = _amount * RECOVERY_FULL / pendingBasis` — itself rounded down — and then zeroes `pendingWithdraws` entirely (`IdleCreditVault.sol:417-421`). Each user later redeems via `_claimLossAdjustedWithdrawRequest`, which pays `claimBasis * lossRecoveryPrice / RECOVERY_FULL`, rounding down again per user (`IdleCreditVault.sol:799`). The sum of all payouts is strictly less than the `_amount` that was transferred in: the vault absorbs `(pendingBasis*price/RECOVERY_FULL) - funded` plus up to one unit per claimant. Because `pendingWithdraws` was already reset to 0, no one owns this remainder and no function skims it.

The same pattern exists on the default path. `finalizeDefaultRecovery` sizes `defaultRecoveryReserve` from aggregate basis (`reserveAmount * RECOVERY_FULL / totalBasis`, `IdleCreditVault.sol:688-691`), while `_claimDefaultedWithdrawRequest` / `_claimDefaultedInstantWithdrawRequest` pay `claimBasis * defaultRecoveryPrice / RECOVERY_FULL` per user and decrement `defaultRecoveryReserve` by that rounded amount (`IdleCreditVault.sol:782-783`, `IdleCreditVault.sol:855`). APR0 interest compounds the drift: `prepareStopEpochWithApr0` adds the aggregate `_apr0NetInterest` to `pendingWithdraws` (`IdleCreditVault.sol:535`) using rate `apr0RateByEpoch = _apr0NetInterest * 1e18 / _principal` (`IdleCreditVault.sol:537`), but each user only claims `principal * rate / 1e18` (`IdleCreditVault.sol:561`, `IdleCreditVault.sol:876`), so `pendingWithdraws -= claimBasis` at `IdleCreditVault.sol:778` leaves a non-zero residue that mirrors the reported `reservedFunds` remainder exactly.

### Impact Explanation
Underlying tokens equal to the cumulative per-claim rounding loss remain permanently locked in `IdleCreditVault`: there is no skim, sweep, or rescue function, and no user receipt corresponds to them. After default finalization the residue is trapped inside `defaultRecoveryReserve`, and the `_transferFundedClaim` guard (`balance - reserve < _amount` reverts, `IdleCreditVault.sol:900-904`) means the stranded reserve can never be spent and contributes to blocking legitimate funded claims when the residual grows. The per-epoch loss is bounded by roughly one wei per claimant plus the aggregate price-rounding term, so this is a permanent-freezing / accounting-drift issue rather than direct theft — magnitude grows with the number of claims and epochs, matching the reported "accumulate multiple error values" behavior.

### Likelihood Explanation
The residue is created deterministically whenever (a) `stopEpochWithDuration` loss-adjusts pending receipts, (b) APR0 interest is settled at `stopEpoch` (rate division almost always truncates when `_apr0NetInterest * 1e18` is not an exact multiple of `apr0TotalPrincipal`), or (c) a default is finalized at a recovery price that does not divide every claim basis evenly. Any unprivileged lender simply claiming a withdrawal triggers the drift; no privileged cooperation is needed beyond the honest manager calling `stopEpoch`/`finalizeDefault`.

### Recommendation
Make the reserve decrement match the cleared basis, not the paid amount — the exact fix proposed in the source report:
- In `_claimDefaultedWithdrawRequest`, the accounting is already correct (`pendingWithdraws -= claimBasis`); the fix needed is for the APR0 bucket: track `apr0TotalPrincipal`'s settled interest as a per-epoch aggregate and reconcile `pendingWithdraws` with the recorded `_apr0NetInterest` rather than the sum of rounded per-user interest (e.g., let the last claimant of an epoch receive the remainder, or store `pendingWithdraws` additions per-user at request time).
- For loss-adjusted and default claims, either compute `lossRecoveryPrice`/`defaultRecoveryPrice` with rounding-up on funding so `sum(payouts) <= funded` is guaranteed exact, or add an explicit dust-reconciliation step at finalization/last-claim that redirects the remainder (e.g., to `feeReceiver` or back to active NAV) instead of leaving it unclaimable.
- At minimum, add a permissionless `sweep`/`skim` restricted to `balance - reservedObligations` so residue does not accumulate forever.

### Proof of Concept
Foundry fork sketch (extend `test/foundry/IdleCreditVault.t.sol` helpers `_depositWithUser`, `_startEpochAndCheckPrices`, `_stopEpochAndCheckPrices`):

```solidity
function testLossAdjustedClaimResidue() external {
    uint256 amt = 10000 * ONE_SCALE;
    // two users deposit and request withdraw in buffer epoch
    address u1 = makeAddr("u1"); address u2 = makeAddr("u2");
    _depositWithUser(u1, amt, true);
    _depositWithUser(u2, amt, true);
    vm.prank(u1); cdoEpoch.requestWithdraw(0, address(AAtranche));
    vm.prank(u2); cdoEpoch.requestWithdraw(0, address(AAtranche));

    _startEpochAndCheckPrices(0);
    // stopEpochWithDuration with a loss that haircut pending receipts
    uint256 pendingBasis = strategy.pendingWithdraws();
    vm.warp(cdoEpoch.epochEndDate() + 1);
    // borrower funds pendingToFund < pendingBasis (loss path sets lossRecoveryPriceByEpoch)
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(/* lossAmount > 0 */);
    uint256 funded = IERC20Detailed(defaultUnderlying).balanceOf(address(strategy));

    vm.startPrank(u1); cdoEpoch.claimWithdrawRequest(); vm.stopPrank();
    vm.startPrank(u2); cdoEpoch.claimWithdrawRequest(); vm.stopPrank();

    uint256 residue = IERC20Detailed(defaultUnderlying).balanceOf(address(strategy));
    assertGt(residue, 0, "unclaimable residue accumulated");
    assertEq(strategy.pendingWithdraws(), 0, "basis already cleared in full");
    // no function can move `residue` out of the strategy
}
```

For the APR0 variant: set `unscaledApr = 0`, request withdraws from two users with principal `P` chosen so `_apr0NetInterest * 1e18 % P != 0`, call `stopEpoch(overrideInterest, 0)`, have both users `claimWithdrawRequest()` after the next epoch, then assert `strategy.pendingWithdraws() > 0` while every user's `withdrawsRequests`/`apr0Users` are zero — a residual basis owed to no one, which forces the borrower to over-fund (or the residue to sit) on subsequent epochs, directly reproducing the Derby `reservedFunds` drift.