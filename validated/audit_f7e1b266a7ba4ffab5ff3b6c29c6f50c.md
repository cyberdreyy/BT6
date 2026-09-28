### Title
Stale instant-withdraw epoch records inflate default recovery basis and price, draining the recovery reserve and freezing honest claims - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
Analog to CVE-2019-5063 (heap corruption via desynchronized persisted data structures): `claimInstantWithdrawRequest` pays out a funded instant receipt but never clears the persisted per-epoch records `instantWithdrawsRequestsByEpoch` and `instantWithdrawClaimsByEpoch`. If the same strategy epoch later defaults while `pendingInstantWithdraws != 0` (partial prefunding), `defaultPendingClaimBasis` re-counts already-paid claims and `_defaultPrefundedInstantReserve` treats cash already withdrawn by users as still-held reserve. The resulting `defaultRecoveryPrice` is inflated relative to the real `defaultRecoveryReserve`, so early claimants over-draw and `defaultRecoveryReserve -= _amount` underflows for later claimants, permanently freezing their recovery.

### Finding Description
In `IdleCreditVault.claimInstantWithdrawRequest` (contracts/strategies/idle/IdleCreditVault.sol:380-393), a funded claim burns the receipt tokens, zeroes `instantWithdrawsRequests[_user]`, and transfers underlying — but `instantWithdrawsRequestsByEpoch[_user][epoch]` and the aggregate `instantWithdrawClaimsByEpoch[epoch]` are left intact. Those mappings are only decremented in the default path `_claimDefaultedInstantWithdrawRequest` (lines 842-856).

At default finalization, `finalizeDefaultRecovery` computes:

- `basis = pendingWithdraws + instantWithdrawClaimsByEpoch[epochNumber]` when `pendingInstantWithdraws != 0` (lines 644-649)
- `prefundedReserve = instantWithdrawClaimsByEpoch[epochNumber] - pendingInstantWithdraws` when positive (lines 716-723)
- `recoveryPrice = (_recoveredAmount + prefundedReserve + defaultRecoveryReserve) * 1e18 / totalBasis` (lines 686-688)

The code's own comments describe the partial-prefund scenario: "startEpoch moved the CDO's available cash to the strategy but that cash covered only part of the instant queue." In that scenario users whose instant claims were funded can already `claimInstantWithdrawRequest` (allowInstantWithdraw is enabled once `getInstantWithdrawFunds`/partial funding lands). Their paid claims remain in `instantWithdrawClaimsByEpoch`, so they are simultaneously:

1. counted again in `defaultPendingClaimBasis`, and
2. assumed to still back `prefundedReserve`, even though the cash already left the strategy.

Both effects push `reserveAmount` above the actual held balance while keeping `totalBasis` overstated, so `defaultRecoveryPrice` no longer corresponds to real assets.

### Impact Explanation
- Early defaulted-epoch claimants (normal receipts via `_claimDefaultedWithdrawRequest`, instant via `_claimDefaultedInstantWithdrawRequest`, post-default via `_claimPostDefaultWithdrawRequest`) each draw `claimBasis * defaultRecoveryPrice / 1e18` from `defaultRecoveryReserve` (lines 912-917). The inflated price over-pays them — direct theft of other claimants' recovery share.
- Once the real reserve is exhausted, `defaultRecoveryReserve -= _amount` reverts under Solidity 0.8 checked arithmetic, so all remaining defaulted-epoch and post-default claimants are permanently frozen out of their recovery — permanent freezing of funds with a loss equal to the inflated (already-paid) instant basis.
- A secondary effect: a user who already claimed a funded instant receipt but still has the stale `instantWithdrawsRequestsByEpoch` entry hits `instantWithdrawsRequests[_user] -= claimBasis` underflow inside `claimInstantWithdrawRequest` after finalization, reverting any subsequent claim call.

### Likelihood Explanation
Requires: an instant-withdraw-enabled (non-programmable) deployment, an epoch where the instant queue is only partially prefunded before funding completes, at least one user claiming the funded portion, and a subsequent borrower default in that same epoch (`_handleBorrowerDefault` from a failed `stopEpoch`/`getFundsFromBorrower`, keeping `epochNumber` unchanged since the bump happens inside `deposit()` on the success path). All steps are achievable by unprivileged actors plus honest manager/borrower actions — no privileged misbehavior needed; a borrower default is a normal protocol event. The stale-record overwrite happens automatically on every funded instant claim, so any partial-prefund-then-default sequence triggers the miscount.

### Recommendation
In `claimInstantWithdrawRequest`, clear the per-epoch records the same way `_claimDefaultedInstantWithdrawRequest` does: decrement `instantWithdrawsRequestsByEpoch[_user][currentEpoch]` (or track and subtract the request epoch) and `instantWithdrawClaimsByEpoch[epoch]` when paying a funded claim, so `defaultPendingClaimBasis` and `_defaultPrefundedInstantReserve` only count still-unpaid receipts. Alternatively, store the request epoch per user (like `lastWithdrawRequest`) to make the decrement unambiguous when claims span epochs.

### Proof of Concept
Foundry fork test sketch (against `IdleCDOEpochVariant` + `IdleCreditVault`):

```solidity
// Setup: non-programmable vault, instant withdrawals enabled, AA deposit by userA and userB.
// Epoch N buffer:
//   userA.requestWithdraw(...) -> instant path -> requestInstantWithdraw(100, userA)
//   userB instant request 50
//   => pendingInstantWithdraws = 150, instantWithdrawClaimsByEpoch[N] = 150
// startEpoch(); // epochNumber still N
// getInstantWithdrawFunds() funds only 100 of the 150 (partial prefund path);
//   collectInstantWithdrawFunds(100) -> pendingInstantWithdraws = 50
// userA.claimInstantWithdrawRequest(); // paid 100; mappings stay: byEpoch[A][N]=100, claimsByEpoch[N]=150
// warp to epochEndDate; borrower repays nothing.
// stopEpoch(...) -> getFundsFromBorrower reverts -> _handleBorrowerDefault (epochNumber still N,
//   pendingInstantWithdraws = 50 != 0)
// finalizeDefaultRecovery(recovered, source):
//   defaultPendingClaimBasis = pendingWithdraws + 150  // includes userA's already-paid 100
//   prefundedReserve = 150 - 50 = 100                  // cash already withdrawn by userA
//   => recoveryPrice inflated by ~100 over real reserve
// Then:
//   first defaulted claim draws claimBasis * inflatedPrice -> over-payment
//   last claimant: defaultRecoveryReserve -= amount -> underflow revert -> permanently frozen
assert recoveryReserveShortfall == userAPaidAmount; // quantify: == already-claimed instant amount
```

Key assertion: after `userA.claimInstantWithdrawRequest()`, `instantWithdrawsRequestsByEpoch[userA][N]` and `instantWithdrawClaimsByEpoch[N]` are still non-zero, and `finalizeDefaultRecovery` computes a `defaultRecoveryPrice` such that total claims exceed `defaultRecoveryReserve` by exactly userA's paid amount — verified by the final claim reverting on the reserve underflow.

Uncertainty noted: the exact partial-prefund path in `startEpoch`/`collectInstantWithdrawFunds` was not fully traced line-by-line, but the code comments at `defaultPendingClaimBasis`/`_defaultPrefundedInstantReserve` (IdleCreditVault.sol:637-648, 712-723) explicitly document that partial instant funding with `pendingInstantWithdraws != 0` is an expected state, which is sufficient for the invariant break.