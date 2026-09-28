### Title
Stale per-epoch instant-withdraw basis inflates default recovery price, stranding recovery reserve — ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
`instantWithdrawsRequestsByEpoch[user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]` are written on every `requestInstantWithdraw` but are only ever cleared inside the *defaulted* claim path `_claimDefaultedInstantWithdrawRequest`. Instant receipts that were already funded and claimed in the same epoch — i.e. "dead" claims, exactly like the dead tasks skipped in one phase but not the transition phase in the kernel bug — remain in the epoch aggregate. When a borrower default is finalized in that epoch, `defaultPendingClaimBasis()` and `_defaultPrefundedInstantReserve()` count those already-paid claims as live basis and as reserve backing, inflating `defaultRecoveryPrice` and corrupting `pendingInstantWithdraws` arithmetic, so the recovery reserve is exhausted before all claims are paid and some claims permanently revert.

### Finding Description
In `requestInstantWithdraw`, both per-user aggregate and per-epoch mappings are incremented (`IdleCreditVault.sol:366-374`). On the normal claim path `claimInstantWithdrawRequest` only zeroes `instantWithdrawsRequests[_user]` (`:391`) — the per-epoch basis is never decremented. `collectInstantWithdrawFunds` decrements only `pendingInstantWithdraws` (`:401`).

A user can open multiple instant-withdraw cycles within a single epoch: `getInstantWithdrawFunds` can be called repeatedly once `instantWithdrawDeadline` has passed (`IdleCDOEpochVariant.sol:558-574`), and `allowInstantWithdraw` is re-enabled after each funding. Each cycle adds to `instantWithdrawClaimsByEpoch[epochNumber]`.

On default finalization:
- `defaultPendingClaimBasis()` adds `instantWithdrawClaimsByEpoch[epochNumber]` in full when `pendingInstantWithdraws != 0` (`:644-649`) — including basis whose funds were already paid out.
- `_defaultPrefundedInstantReserve()` computes `instantBasis - pendingInstant` as "already-held" reserve (`:716-723`) — counting underlyings that left the strategy long ago, so `recoveryPrice = reserveAmount * 1e18 / totalBasis` is computed against a phantom reserve.
- `_claimDefaultedInstantWithdrawRequest` does `instantWithdrawsRequests[_user] -= claimBasis` (`:848`) where `claimBasis` includes the already-claimed amount, so it underflows and reverts, or pays at an inflated price until the reserve is gone.

### Impact Explanation
Insolvency of `defaultRecoveryReserve` and permanent freezing/theft of unclaimed yield. Every unit of already-paid instant basis inflates `defaultRecoveryPrice`, so early recovery claimants (active LPs via tranche claims, defaulted receipt holders) are paid at a ratio the actual reserve cannot support; later claimants' `_transferDefaultRecovery` calls fail on insufficient balance. Additionally, a user with a genuine pending instant receipt in the default epoch cannot claim at all if their stale epoch basis exceeds `instantWithdrawsRequests[_user]` — their claim reverts permanently (`:848`), freezing their share of the recovery. Loss magnitude equals the total stale instant basis accumulated in the default epoch.

### Likelihood Explanation
Requires: an epoch where instant withdrawals were funded at least once and then a new instant request exists when the borrower defaults in that same epoch. Borrower defaults are an expected protocol path (handled by `_handleBorrowerDefault`/`finalizeDefault`). An unprivileged KYC'd lender can create the stale basis deliberately: request instant withdraw, wait for `getInstantWithdrawFunds`, claim, request again, and keep the second request pending into the default. Honest owner/manager calls supply the transitions; the attacker only sequences around them. All existing guards (`_onlyIdleCDO`, `_transferFundedClaim` reserve guard, default flags) do not touch the stale epoch aggregate.

### Recommendation
Decrement `instantWithdrawsRequestsByEpoch[_user][epochNumber]` and `instantWithdrawClaimsByEpoch[epochNumber]` inside `claimInstantWithdrawRequest` when the claim is paid, so already-settled receipts leave the epoch aggregate. Alternatively track a per-epoch funded counter and subtract it in `defaultPendingClaimBasis`/`_defaultPrefundedInstantReserve`. Add a finalization-time invariant check that `instantWithdrawClaimsByEpoch[epochNumber]` equals the sum of outstanding per-user epoch receipts.

### Proof of Concept
Foundry fork PoC sketch (contracts only; attacker = KYC'd AA lender):

```solidity
// Setup: epoch running, instant withdraws enabled via getInstantWithdrawFunds.
// Epoch N, instantWithdrawDeadline passed.
uint256 first = 100e6;
cdo.requestInstantWithdraw(first, AA);            // attacker
vm.prank(manager); vault.getInstantWithdrawFunds(); // borrower funds 100
cdo.claimInstantWithdrawRequest(AA);              // paid; ByEpoch[N] and claimsByEpoch[N] still = 100

uint256 second = 50e6;
cdo.requestInstantWithdraw(second, AA);           // pendingInstantWithdraws = 50, claimsByEpoch[N] = 150

// Borrower defaults before funding the second request
// -> _handleBorrowerDefault; then finalizeDefault(recovered, source)

// Observed:
// defaultPendingClaimBasis() includes 150 instead of 50
// _defaultPrefundedInstantReserve() = 150 - 50 = 100 counted as held reserve, but 0 such funds exist
// recoveryPrice inflated; reserve drains early; later claimants' transfers revert
// and attacker's own _claimDefaultedInstantWithdrawRequest reverts at
// instantWithdrawsRequests[user] (50) -= claimBasis (150), freezing 50.
```

Note: I verified the write/clear asymmetry in `IdleCreditVault.sol` (`:371-374`, `:387-392`, `:644-723`, `:842-856`); I did not re-verify the CDO-side gating that permits a second instant request in the same epoch beyond `getInstantWithdrawFunds` being callable whenever `isEpochRunning && timestamp >= instantWithdrawDeadline`, which contains no once-per-epoch flag in the code read.