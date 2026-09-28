### Title
Default recovery excludes prior-epoch instant-withdraw receipts, leaving them permanently unclaimable or paying them from other claimants' recovery reserve - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.finalizeDefaultRecovery` sizes the default recovery reserve using `defaultPendingClaimBasis()`, which only counts instant-withdraw receipts recorded under the *current* `epochNumber` (`instantWithdrawClaimsByEpoch[epochNumber]`). Instant receipts requested in an earlier epoch that were still unfunded when the borrower defaulted are never included in `totalBasis`. After finalization, `_claimDefaultedInstantWithdrawRequest` only clears receipts keyed to `defaultRecoveryEpoch`, so the stale prior-epoch remainder in `instantWithdrawsRequests[_user]` falls through to the par-value `_transferFundedClaim` path — for which no funding was ever collected or reserved. The analog to the ATS bug: a "trailing section" (older-epoch receipt data) is forwarded past the validation that sizes the reserve, letting an unaccounted claim smuggle through the finalized accounting boundary.

### Finding Description
In `requestInstantWithdraw`, each request is recorded per request-epoch: `instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount` and `instantWithdrawClaimsByEpoch[currentEpoch] += _amount`, while the unfunded remainder is tracked globally in `pendingInstantWithdraws` (lines 366–374).

At default, `defaultPendingClaimBasis()` adds instant claims only for the current epoch and only when `pendingInstantWithdraws != 0` (lines 644–649). Receipts keyed to any earlier epoch are excluded from `totalBasis`, so `defaultRecoveryPrice` and `defaultRecoveryReserve` are computed without them, even though `pendingInstantWithdraws` still contains their basis.

After `defaultRecoveryFinalized`, `claimInstantWithdrawRequest` runs `_claimDefaultedInstantWithdrawRequest(_user)`, which clears only `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` (lines 843–856). The user's remaining `instantWithdrawsRequests[_user]` — the prior-epoch unfunded basis — is then burned and paid 1:1 via `_transferFundedClaim` (lines 387–392). The only guard is `balance - reserve < _amount` in `_transferFundedClaim` (line 904); nothing ties the payout to actually-collected funds for that epoch.

Two outcomes, both broken:

- If the strategy holds non-reserve underlying (e.g., partial prefunding, or funds routed for other purposes), the stale receipt is paid at full par while all default-epoch claimants absorb the haircut — the payout silently consumes funds that dilute or steal from `defaultRecoveryReserve` beneficiaries.
- Otherwise the claim permanently reverts. The user holds burned-on-request receipts that were counted in `pendingInstantWithdraws` (so the borrower was debited the basis) but were excluded from the recovery pool — a permanent freeze of an entitled claim that can never be resolved, because `_ensureDefaultRecoveryInitialized` already ran and there is no path to re-open recovery accounting.

### Impact Explanation
Unprivileged lenders who requested an instant withdrawal in epoch N−1 and remained unfunded into defaulted epoch N either (a) drain an amount equal to their stale receipt basis at par, directly taking funds belonging to recovery-reserve claimants (theft proportional to their unfunded instant balance), or (b) have their claim permanently frozen — entitled funds that the borrower was charged for in `pendingInstantWithdraws` but that no claimant can ever withdraw (insolvency equal to the excluded prior-epoch instant basis).

### Likelihood Explanation
Requires a pending instant withdrawal to span an `epochNumber` boundary and then a borrower default — i.e., instant requests in the buffer epoch that are only partially funded via `collectInstantWithdrawFunds` before `startEpoch` rolls the epoch and default occurs. This is a realistic sequencing for instant-withdraw-heavy pools under stress, which is precisely when defaults happen. All roles involved (manager stopping the epoch, CDO calling `finalizeDefaultRecovery`) behave honestly.

### Recommendation
Make the recovery basis epoch-agnostic: in `defaultPendingClaimBasis()`, aggregate unfunded instant claims across all epochs (e.g., track a global `totalInstantClaimBasis` incremented in `requestInstantWithdraw` and decremented in `claimInstantWithdrawRequest`/`_claimDefaultedInstantWithdrawRequest`), and in `_defaultPrefundedInstantReserve` compare against that global basis rather than `instantWithdrawClaimsByEpoch[epochNumber]`. In `_claimDefaultedInstantWithdrawRequest`, clear the user's full `instantWithdrawsRequests[_user]` remainder at `defaultRecoveryPrice` instead of only the `defaultRecoveryEpoch` slice, so unfunded stale receipts get the haircut and funded ones keep par treatment.

### Proof of Concept
Foundry fork PoC sketch (against `test/foundry/IdleCreditVault.t.sol` harness):

```solidity
function testStaleEpochInstantReceiptExcludedFromRecovery() external {
    // Setup: deposit, start epoch, so epochNumber == 0.
    idleCDO.depositAA(10_000 * ONE_SCALE);
    _startEpochAndCheckPrices(0);

    // Attacker requests instant withdraw during epoch 0 buffer/running.
    cdoEpoch.requestInstantWithdraw(instantAmt); // records under epoch 0

    // Epoch rolls to 1 without collectInstantWithdrawFunds covering it
    // (pendingInstantWithdraws > 0, instantWithdrawClaimsByEpoch[0] = instantAmt).
    _startEpochAndCheckPrices(1);

    // A second instant request in epoch 1 keeps pendingInstantWithdraws != 0.
    cdoEpoch.requestInstantWithdraw(instantAmt2); // under epoch 1

    // Borrower defaults; manager/guardian finalize.
    // finalizeDefaultRecovery bases totalBasis on
    //   pendingWithdraws + instantWithdrawClaimsByEpoch[1]  (epoch-0 basis missing!)
    _handleBorrowerDefaultAndFinalize(recovery);

    IdleCreditVault s = IdleCreditVault(address(strategy));
    // instantWithdrawsRequests[attacker] still contains epoch-0 remainder after
    // _claimDefaultedInstantWithdrawRequest clears only the epoch-1 slice.
    // claimInstantWithdrawRequest then attempts par payout of unfunded basis:
    //  - either drains underlying backing the recovery reserve (theft), or
    //  - reverts forever in _transferFundedClaim (permanent freeze).
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest();
    // assert stolen amount > haircut basis, or expect NotAllowed forever.
}
```

Uncertainty note: I was not able to read `IdleCDOEpochVariant.stopEpoch`/`_afterStopEpoch` ordering in this session to fully confirm the exact sequence in which `epochNumber` rolls relative to partial instant funding, but the epoch-keyed accounting asymmetry (`instantWithdrawClaimsByEpoch[epochNumber]` vs. aggregate `instantWithdrawsRequests`/`pendingInstantWithdraws`) at `contracts/strategies/idle/IdleCreditVault.sol:644-649` and `:843-856` is the concrete broken invariant either way.