### Title
Post-default withdraw requests drain the fixed `defaultRecoveryReserve`, permanently freezing default-epoch claimants' recovery - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
After a borrower default is finalized, `IdleCreditVault.requestWithdraw` lets any tranche holder create a new "post-default" withdraw receipt (`postDefaultRequests`) and `claimWithdrawRequest` pays it 1:1 out of `defaultRecoveryReserve` via `_transferDefaultRecovery`. That reserve was sized exactly to cover only the pre-finalization claim basis (`reserveAmount = recovered + prefunded + priorReserve`, with `recoveryPrice = reserve / totalBasis`). Post-default requests were never part of `totalBasis`, yet they consume the same finite reserve. Once enough post-default claims are paid, earlier default-epoch receipts can no longer claim — `_transferDefaultRecovery` underflows `defaultRecoveryReserve` and reverts, permanently freezing their recovery share. This mirrors the Astaria bug class: proceeds are distributed through a path whose allocation state was never populated (here, the recovery reserve never accounts for post-finalization claimants), letting a later claimant take funds earmarked for existing creditors.

### Finding Description
`finalizeDefault` (IdleCDOEpochVariant.sol:194-225) calls `IdleCreditVault.finalizeDefaultRecovery`, which computes `totalBasis = activeBasis + pendingBasis` and stores `defaultRecoveryReserve = reserveAmount` and `defaultRecoveryPrice = reserveAmount / totalBasis` (IdleCreditVault.sol:680-692). The reserve covers exactly `totalBasis * recoveryPrice`; nothing more is ever added, since `startEpoch` reverts while `defaulted` is true (IdleCDOEpochVariant.sol:239).

Post-finalization, `finalizeDefault` re-enables withdraw requests (`allowAAWithdrawRequest = allowBBWithdrawRequest = true`, line 217-218). A tranche holder then calls `requestWithdraw`, which reaches the `defaultRecoveryFinalized` branch in `IdleCreditVault.requestWithdraw` (lines 247-257): it burns the CDO's strategy tokens, mints a receipt to the user, and sets `postDefaultRequests[_user] = _amount`. On `claimWithdrawRequest`, `_claimPostDefaultWithdrawRequest` (lines 760-767) pays `amount` 1:1 through `_transferDefaultRecovery`, which does `defaultRecoveryReserve -= _amount` (line 915).

The invariant `sum(all claims) <= defaultRecoveryReserve` is broken: total payable claims become `pendingBasis*price + active claims + Σ postDefaultRequests`, strictly greater than the reserve. The over-consumption is real, not just accounting: `_transferDefaultRecovery` decrements the reserve state variable, so when it hits zero any remaining defaulted-epoch claim (`_claimDefaultedWithdrawRequest`, `_claimDefaultedInstantWithdrawRequest`) reverts on underflow — recovery is permanently frozen for those users.

The attacker only needs to hold tranche tokens (any KYC'd lender), wait for the honest owner/manager to call `finalizeDefault`, then `requestWithdraw` + `claimWithdrawRequest`. The haircut applied via `virtualPrice` at request time does not fix this — it prices the attacker's receipt fairly in tranche terms but the reserve that pays it was never increased to cover it. There is no guard limiting post-default claims to actual remaining funds beyond the raw reserve balance.

Uncertainty I could not fully resolve within available iterations: whether additional underlying legitimately sits in the strategy post-finalization (e.g., borrower repays later directly to the strategy) that could absorb post-default payouts before the reserve — the code comments treat the reserve as the sole funding source (`"already backed by default recovery reserve"`, line 104), and `_transferFundedClaim` explicitly ring-fences the reserve against other claims, which confirms the reserve is intended to be exhausted only by recovery claims.

### Impact Explanation
Each post-default claim steals `amount` of underlying from the aggregate recovery pool belonging to all default-epoch receipt holders and active-LP recovery accounting. Once the reserve is consumed below a defaulted claimant's `claimBasis * defaultRecoveryPrice`, that claim reverts permanently — the recovery is frozen forever because no new funds can ever enter (pool is closed, `epochDuration = 0`, `defaulted` blocks `startEpoch`). Loss is quantified as `min(postDefaultClaims paid, defaultRecoveryReserve)` stolen/frozen recovery.

### Likelihood Explanation
Requires a borrower default (possible without attacker action — honest borrower simply fails to fund a stopEpoch pull) followed by `finalizeDefault` by the honest owner/manager, then a single tranche holder calling two public functions. No privileged-role misbehavior is needed; the request/claim path is intentionally enabled post-finalization. Any tranche holder, including one who bought tranches cheaply after default news, can execute it, and the first movers win a race against default-epoch claimants.

### Recommendation
Fund post-default receipts separately from `defaultRecoveryReserve`: either revert post-finalization `requestWithdraw` when `defaultRecoveryFinalized` (the pool is closed and no new funding source exists), or route `_claimPostDefaultWithdrawRequest` through `_transferFundedClaim` and require new borrower funding to be collected before those requests become claimable. Alternatively, haircut post-default claims by `defaultRecoveryPrice` when adding them so the reserve stays solvent — but this still dilutes earlier claimants unless their basis is also registered, so separating the funding paths (as in the Astaria fix separating listing vs. liquidation paths) is cleaner.

### Proof of Concept
Foundry fork sketch against a live IdleCDOEpochVariant + IdleCreditVault deployment (modeled on `test/foundry/IdleCreditVault.t.sol` and `IdleCreditVaultWriteOffEscrow.t.sol`):

```solidity
function testPostDefaultRequestDrainsRecoveryReserve() external {
    // Setup: AA and BB LPs deposit; owner/manager starts an epoch.
    // 1. Warp past epochEndDate. Borrower does NOT approve repayment.
    vm.prank(manager);
    cdo.stopEpoch(newApr, 0);          // borrower pull fails -> _handleBorrowerDefault, defaulted = true

    // 2. Owner finalizes recovery with partial recovery, e.g. 50% of basis.
    uint256 recovered = totalBasis / 2;
    deal(underlying, recoverySource, recovered);
    vm.prank(recoverySource);
    IERC20(underlying).approve(strategy, recovered);
    vm.prank(owner);
    cdo.finalizeDefault(recovered, recoverySource);   // reserve = recovered, price = 0.5e18

    // 3. Attacker (any tranche holder, e.g. BB holder) requests withdraw post-finalization.
    vm.prank(attacker);
    cdo.requestWithdraw(0, BBTranche);                // postDefaultRequests[attacker] = haircut amount
    vm.prank(attacker);
    cdo.claimWithdrawRequest();                        // _transferDefaultRecovery pays 1:1 from reserve

    // 4. Victim with a default-epoch pending receipt claims last.
    vm.prank(victim);
    vm.expectRevert();                                 // defaultRecoveryReserve underflow / insolvent reserve
    cdo.claimWithdrawRequest();
}
```

Key assertions: `IdleCreditVault(strategy).defaultRecoveryReserve()` decreases by the attacker's full claim while `defaultRecoveryPrice` was computed only over pre-finalization `totalBasis`; the victim's `_claimDefaultedWithdrawRequest` reverts, leaving their recovery permanently unclaimable.