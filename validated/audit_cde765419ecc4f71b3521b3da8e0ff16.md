### Title
Claimed instant-withdraw receipts stay in the per-epoch aggregate and are double-counted at default finalization, diluting all recovery claimants - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`claimInstantWithdrawRequest` clears the user's balance (`instantWithdrawsRequests[_user]`) and burns the receipt, but never clears `instantWithdrawsRequestsByEpoch[_user][epoch]` and never decrements the epoch-level aggregate `instantWithdrawClaimsByEpoch[epoch]`. Those aggregates are exactly what `defaultPendingClaimBasis()` and `_defaultPrefundedInstantReserve()` feed into `finalizeDefaultRecovery`. If the borrower defaults in the same epoch while part of the instant queue is still unfunded, already-paid-out claims are counted a second time as outstanding basis, corrupting the recovery price for every other claimant — the same "one user's exit corrupts the epoch-level total" bug class as the StakingVault `_rewardPoolShares[poolId][cycleId] -= shares` report.

### Finding Description
Funding/collection only decrements `pendingInstantWithdraws` (`collectInstantWithdrawFunds`, line 401); the epoch aggregates are only ever decremented inside the *defaulted* claim path (`instantWithdrawClaimsByEpoch[defaultEpoch] -= claimBasis`, line 853). On a normal funded claim, `claimInstantWithdrawRequest` (lines 387–392) zeroes the per-user counter but leaves:

- `instantWithdrawsRequestsByEpoch[user][epoch]` = stale nonzero basis
- `instantWithdrawClaimsByEpoch[epoch]` = still includes the paid amount

At default finalization (same `epochNumber`, `pendingInstantWithdraws != 0`):

- `defaultPendingClaimBasis()` adds the inflated `instantWithdrawClaimsByEpoch[epochNumber]` to the basis (lines 644–649) → `recoveryPrice = reserveAmount / totalBasis` is pushed down.
- `_defaultPrefundedInstantReserve()` computes `instantBasis - pendingInstant` (lines 716–723), which counts already-paid underlying as still-held reserve → `reserveAmount` and `recoveryPrice` are further mispriced.

Broken invariant: epoch-level claim totals must equal the sum of outstanding per-user receipts. One user's claim leaves the epoch total unchanged while the funds are gone — directly analogous to `unstake()` deleting the epoch's total shares instead of the user's shares.

### Impact Explanation
An unprivileged lender (KYC'd tranche holder) who requests an instant withdrawal, gets it funded, and claims before a same-epoch borrower default causes their paid amount `A` to be double-counted. All remaining defaulted-epoch and post-default claimants are diluted by roughly `A / totalBasis` of the recovery reserve, and because `_transferDefaultRecovery` decrements `defaultRecoveryReserve` (line 915), the phantom basis drains the reserve early — later claimants' claims revert (underflow / failed transfer), i.e., permanent freezing of unclaimed recovery. Additionally, the victim with a stale receipt who calls `claimInstantWithdrawRequest` again hits `instantWithdrawsRequests[_user] -= claimBasis` underflow (line 848) and reverts.

### Likelihood Explanation
Requires: instant-withdraw mode enabled, an epoch with partial instant funding (`pendingInstantWithdraws != 0`), at least one funded claim in that epoch, and a borrower default finalized in the same epoch. Borrower default and manager finalization are honest privileged events, not attacker-controlled, which lowers likelihood — but the attacker's part (request + claim) is fully permissionless, and partial instant funding is a normal operating state.

### Recommendation
In `claimInstantWithdrawRequest`, clear the per-epoch receipt and decrement the epoch aggregate for the user's request epochs (e.g., iterate/clear `instantWithdrawsRequestsByEpoch[_user][*]` and subtract from `instantWithdrawClaimsByEpoch[epoch]`), mirroring how `_claimDefaultedInstantWithdrawRequest` already does at lines 847–853. The same audit should verify `withdrawsRequestsByEpoch` is cleared on funded normal claims (line 344 leaves it stale), which currently only self-DoSes but is fragile.

### Proof of Concept
Foundry fork sketch against `test/foundry/IdleCreditVault.t.sol` harness:

```solidity
// epoch N running, instant mode
// attacker and victim both requestInstantWithdraw via cdoEpoch.requestInstantWithdraw
uint256 a = 100 * ONE_SCALE; uint256 v = 100 * ONE_SCALE;
cdoEpoch.requestInstantWithdraw(a, AAtranche);            // attacker
vm.prank(victim); cdoEpoch.requestInstantWithdraw(v, AAtranche);

// borrower/manager partially funds: only 'a' collected
deal(underlying, borrower, a);
// ... trigger getInstantWithdrawFunds/collectInstantWithdrawFunds for 'a'
cdoEpoch.claimInstantWithdrawRequest();                   // attacker paid 'a' at par

assertEq(strategy.instantWithdrawClaimsByEpoch(strategy.epochNumber()), a + v); // stale: still includes 'a'

// borrower defaults in same epoch; manager finalizes recovery
// -> defaultPendingClaimBasis() includes paid 'a' -> recoveryPrice mispriced
// victim's defaulted claim receives less than (v * realReserve / realBasis),
// or reverts once reserve is drained by over-priced earlier claims.
```

Note: exact CDO entry points (`getInstantWithdrawFunds`, default trigger in `IdleCDOEpochVariant`) were not fully read in this pass; the strategy-side corruption of `instantWithdrawClaimsByEpoch` and its consumption at lines 644–649, 716–723 is confirmed in `IdleCreditVault.sol`.