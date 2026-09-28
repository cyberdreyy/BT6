### Title
Post-default instant-withdraw requests are written into the finalized default epoch's stale receipt bucket, letting a new claimant drain the recovery reserve earmarked for defaulted-epoch claimants - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.requestInstantWithdraw` records every receipt under the *current* `epochNumber` but never checks `defaultRecoveryFinalized`. After a default is finalized, `epochNumber` stays equal to `defaultRecoveryEpoch` (only `stopEpoch`/`deposit` during a running epoch increments it), so a fresh instant-withdraw request mutates the already-finalized bucket `instantWithdrawsRequestsByEpoch[user][defaultRecoveryEpoch]` and the aggregate `instantWithdrawClaimsByEpoch[defaultRecoveryEpoch]`. This is the direct analog of the VAS bug: a saved reference (the per-epoch receipt bucket finalized at `finalizeDefaultRecovery`) is not invalidated when the epoch's accounting is "closed", and a later write through a different path (`requestInstantWithdraw` vs. `munmap`) resurrects the stale slot, producing an invalid claim during "migration" (recovery-reserve distribution).

### Finding Description
- At `requestInstantWithdraw` (lines 356-375) there is `_ensureDefaultRecoveryInitialized()` but no `defaultRecoveryFinalized` gate, unlike `requestWithdraw` (lines 247-257) which routes post-default requests into the separate `postDefaultRequests` map.
- A post-default instant request therefore does:
  - `instantWithdrawsRequests[user] += amount`
  - `instantWithdrawsRequestsByEpoch[user][epochNumber] += amount` where `epochNumber == defaultRecoveryEpoch`
  - `instantWithdrawClaimsByEpoch[epochNumber] += amount`
  - `pendingInstantWithdraws += amount`
- On `claimInstantWithdrawRequest` (lines 380-393), if `defaultRecoveryFinalized && defaultInstantWithdrawsFinalized`, `_claimDefaultedInstantWithdrawRequest` (lines 842-856) reads `instantWithdrawsRequestsByEpoch[user][defaultRecoveryEpoch]` — which now includes the *new* post-default receipt — treats it as defaulted-epoch basis, applies `defaultRecoveryPrice`, and pays out of `defaultRecoveryReserve` via `_transferDefaultRecovery`.
- The reserve `defaultRecoveryReserve` was sized at `finalizeDefaultRecovery` (line 691) to exactly cover the finalized basis. A post-default claim consumes reserve that was earmarked for legitimate defaulted-epoch claimants. Since `_transferDefaultRecovery` does `defaultRecoveryReserve -= amount` and the reserve is fixed, earlier attacker claims cause later honest claims to revert on underflow / insufficient balance — permanently freezing their recovery.
- If `defaultInstantWithdrawsFinalized == false` (instant bucket was zero at finalization), the post-default request falls through to the par-funded path: `_transferFundedClaim` pays `instantWithdrawsRequests[user]` at full par. The request was never funded by the borrower (no `collectInstantWithdrawFunds` will run post-default), so either it pays 1:1 from residual strategy funds (theft — a receipt minted at post-haircut `virtualPrice` redeems at par), or the `balance - reserve < amount` guard at line 904 reverts and the claim is frozen.
- The burned receipt tokens were minted 1:1 against tranche tokens already haircut by the lowered `virtualPrice`, so the attacker risks little principal while extracting reserve/par value.

### Impact Explanation
Direct theft and permanent freezing of the default-recovery reserve. Quantified: an attacker requesting an instant withdraw of `X` post-default extracts `X * defaultRecoveryPrice / 1e18` (or `X` at par in the `defaultInstantWithdrawsFinalized == false` branch) from a fixed reserve sized only for pre-finalization claims, causing an equal shortfall for the last honest defaulted-epoch claimants whose `_transferDefaultRecovery`/`_transferFundedClaim` then reverts.

### Likelihood Explanation
Requires only an unprivileged tranche holder calling the CDO's instant-withdraw entry point after `finalizeDefaultRecovery` — no privileged cooperation. The preconditions (default occurred, instant withdrawals enabled, attacker holds or buys post-haircut tranche tokens) are realistic. The main uncertainty I could not fully verify in the available iterations is whether `IdleCDOEpochVariant`'s instant-withdraw entry (`requestInstantWithdraw`/`getInstantWithdrawFunds`) remains callable once `defaulted()` is true; however, `requestWithdraw` explicitly implements a post-default path, indicating the CDO keeps accepting requests post-default, and the asymmetric missing guard on the instant path is itself the defect.

### Recommendation
In `requestInstantWithdraw`, when `defaultRecoveryFinalized` is true, either revert or route the request into a post-default bucket analogous to `postDefaultRequests` (never write into `instantWithdrawsRequestsByEpoch[defaultRecoveryEpoch]`/`instantWithdrawClaimsByEpoch`). Additionally snapshot/close `instantWithdrawClaimsByEpoch` at finalization so post-finalization epochs cannot alias the defaulted epoch key.

### Proof of Concept
Foundry fork PoC outline (post-default phase, instant mode):

```solidity
// Assume: borrower defaulted, manager called finalizeDefault / finalizeDefaultRecovery,
// defaultRecoveryFinalized == true, defaultInstantWithdrawsFinalized == true,
// defaultRecoveryReserve = R (sized for honest claimant H's basis B), price p < 1e18.

address attacker = makeAddr('attacker');
// 1. Attacker acquires AA tranche tokens post-default (cheap, post-haircut price).
deal(address(AAtranche), attacker, T);

// 2. Attacker requests instant withdraw via the CDO (epochNumber still == defaultRecoveryEpoch).
vm.prank(attacker);
cdoEpoch.requestInstantWithdraw(x, address(AAtranche));
// instantWithdrawsRequestsByEpoch[attacker][defaultRecoveryEpoch] == receipt

// 3. Attacker claims before honest claimant H.
vm.prank(attacker);
cdoEpoch.claimInstantWithdrawRequest();
// -> _claimDefaultedInstantWithdrawRequest pays x * p / 1e18 out of defaultRecoveryReserve

// 4. Honest defaulted-epoch claimant H claims -> _transferDefaultRecovery underflows / reverts.
vm.prank(honest);
vm.expectRevert(); // defaultRecoveryReserve exhausted early
cdoEpoch.claimInstantWithdrawRequest();
```

If `defaultInstantWithdrawsFinalized == false` instead, step 3 pays the attacker at par from `_transferFundedClaim`, either draining residual funds or reverting against the reserve guard — both break solvency/one-receipt-one-payout.