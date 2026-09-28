### Title
Loss-adjusted withdraw receipt escapes haircut and is paid at par after a defaulted-epoch claim clears `lastWithdrawRequest` - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.claimWithdrawRequest` runs three sequential payout paths: `_claimDefaultedWithdrawRequest`, `_claimLossAdjustedWithdrawRequest`, and `_claimFundedWithdrawRequest`. When a user holds (1) a withdraw receipt from an earlier epoch that was funded at a reduced `lossRecoveryPriceByEpoch` via `collectWithdrawFunds` (i.e., a `stopEpochWithDuration` partial-loss epoch), and (2) a second receipt created in the defaulted epoch, `_clearWithdrawClaimForEpoch` resets `lastWithdrawRequest[_user]` to 0 because the cleared epoch equals the latest marker. The subsequent `_claimLossAdjustedWithdrawRequest` then reads `lossEpoch = lastWithdrawRequest[_user] == 0`, finds `lossRecoveryPriceByEpoch[0] == 0`, and returns 0 — so the loss haircut is never applied. The still-recorded loss-epoch amount in `withdrawsRequests[_user]` is then paid **at par** by `_claimFundedWithdrawRequest`. This is the idle-tranches analog of the double-payment bug class: one receipt is paid on the wrong (un-haircut) basis, drawing funds that belong to other claimants.

### Finding Description
In `requestWithdraw`, each new request overwrites `lastWithdrawRequest[_user] = currentEpoch` and accumulates `withdrawsRequests[_user]` plus `withdrawsRequestsByEpoch[_user][currentEpoch]` (`IdleCreditVault.sol:282-293`). When a stop epoch realizes a loss, `collectWithdrawFunds` funds less than `pendingWithdraws`, stores `lossRecoveryPriceByEpoch[epochNumber]`, and zeroes `pendingWithdraws` (`IdleCreditVault.sol:411-422`). Claimants of that epoch are supposed to receive only `claimBasis * lossRecoveryPrice / RECOVERY_FULL` through `_claimLossAdjustedWithdrawRequest`, which is keyed solely by `lastWithdrawRequest[_user]` (`IdleCreditVault.sol:789-800`).

After the borrower defaults and `finalizeDefaultRecovery` runs, `claimWithdrawRequest` calls `_claimDefaultedWithdrawRequest`, which invokes `_clearWithdrawClaimForEpoch(_user, defaultRecoveryEpoch, true)`. That clears only `withdrawsRequestsByEpoch[defaultEpoch]` and, at `IdleCreditVault.sol:832-835`, sets `lastWithdrawRequest[_user] = 0` when the cleared epoch is the latest marker — even though an older, unclaimed loss-adjusted receipt still exists in `withdrawsRequestsByEpoch[_user][lossEpoch]` and in the aggregate `withdrawsRequests[_user]`. Control then reaches `_claimLossAdjustedWithdrawRequest`, which looks up `lossRecoveryPriceByEpoch[0]` (unset → returns 0), and `_claimFundedWithdrawRequest`, which pays the entire remaining `withdrawsRequests[_user]` at par via `_transferFundedClaim` after the `epochNumber > lastWithdrawRequest` check trivially passes (`lastWithdrawRequest == 0`).

### Impact Explanation
The attacker recovers `lossBasis * (1 - lossRecoveryPrice) / RECOVERY_FULL` more than entitled — the exact loss amount that was supposed to be socialized onto their receipt. `_transferFundedClaim` only guards against spending `defaultRecoveryReserve`, so the excess is paid from strategy underlyings earmarked for other funded/defaulted claimants, producing direct insolvency for later claimers (the last claimants' transfers revert or the reserve guard blocks legitimate claims). Loss scales with the loss-epoch haircut: with a 50% `lossRecoveryPrice`, a user with a 100k-underlying loss-epoch receipt and any dust defaulted-epoch receipt extracts ~50k extra underlying.

### Likelihood Explanation
Requires an unprivileged lender who (a) requested a withdraw in an epoch that ends in a `stopEpochWithDuration` loss (borrower under-funds via `collectWithdrawFunds` — an honest-but-lossy path), (b) does not claim, (c) requests a new withdraw in the epoch that later defaults, and (d) claims after `finalizeDefaultRecovery`. None of these steps require privileged collusion; the user simply sequences their own requests around honest manager/owner calls. The guard set (`_onlyIdleCDO`, `defaultRecoveryFinalized` checks, epoch gating) does not cover this ordering because `lastWithdrawRequest` is used both as "latest request marker" and as the key for the loss-adjusted price lookup, and it is unconditionally zeroed on the default-epoch clear.

### Recommendation
Track the loss-adjusted epoch explicitly rather than relying on `lastWithdrawRequest`. Either store per-user the epoch carrying a `lossRecoveryPriceByEpoch` haircut (e.g., `lossAdjustedEpoch[_user]` set when `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]] != 0` at claim/request time), or iterate `withdrawsRequestsByEpoch` over all epochs with nonzero balances instead of keying off the single `lastWithdrawRequest` marker. At minimum, in `_clearWithdrawClaimForEpoch`, do not zero `lastWithdrawRequest` when another uncleared `withdrawsRequestsByEpoch` entry for a different epoch still exists — recompute the marker to the remaining receipt's epoch so `_claimLossAdjustedWithdrawRequest` still applies the haircut.

### Proof of Concept
Foundry fork PoC sketch (against the existing `test/foundry/IdleCreditVault.t.sol` harness):

```solidity
function testLossAdjustedReceiptPaidAtParAfterDefault() external {
    // 1. Deposit AA for attacker and other users; start epoch 0.
    uint256 amt = 100_000 * ONE_SCALE;
    idleCDO.depositAA(amt); // attacker = address(this)
    _depositWithUser(victim, amt, true);

    // 2. Attacker requests withdraw in epoch that will end with a loss.
    cdoEpoch.requestWithdraw(attackerTranches / 2, address(AAtranche));
    uint256 lossEpoch = strategy.epochNumber();

    // 3. stopEpochWithDuration where borrower funds only 50% of pendingWithdraws
    //    -> collectWithdrawFunds sets lossRecoveryPriceByEpoch[lossEpoch] = 0.5e18.
    _stopEpochWithPartialFunding(0.5e18); // helper: borrower under-funds pending

    // 4. Attacker does NOT claim; requests a second withdraw in the next epoch.
    _startEpochAndCheckPrices(1);
    cdoEpoch.requestWithdraw(attackerTranches / 4, address(AAtranche));

    // 5. Borrower defaults in this epoch; finalizeDefaultRecovery runs.
    _defaultAndFinalize(); // cdoEpoch.defaulted() == true, defaultRecoveryFinalized == true

    // 6. Single claimWithdrawRequest call:
    //    - _claimDefaultedWithdrawRequest clears epoch-2 receipt, sets lastWithdrawRequest=0
    //    - _claimLossAdjustedWithdrawRequest reads lossRecoveryPriceByEpoch[0] -> skipped
    //    - _claimFundedWithdrawRequest pays remaining withdrawsRequests (the loss-epoch
    //      receipt) at PAR instead of at 0.5 * basis.
    uint256 balPre = underlying.balanceOf(address(this));
    cdoEpoch.claimWithdrawRequest();
    uint256 paid = underlying.balanceOf(address(this)) - balPre;

    uint256 lossBasis = /* attacker epoch-`lossEpoch` claim basis */;
    // Attacker received lossBasis instead of lossBasis * 0.5
    assertGe(paid, lossBasis, "loss-adjusted receipt paid at par");
}
```

The assertion holds because after step 6's internal clear, `strategy.lastWithdrawRequest(attacker) == 0` while `strategy.withdrawsRequestsByEpoch(attacker, lossEpoch)` was still nonzero going into `_claimFundedWithdrawRequest` — the haircut stored in `lossRecoveryPriceByEpoch[lossEpoch]` is never applied.