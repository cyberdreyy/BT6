### Title
Stale per-epoch instant-withdraw basis freezes new instant receipts after default finalization - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`claimInstantWithdrawRequest` resets only the aggregate `instantWithdrawsRequests[_user]` but never clears the per-epoch basis `instantWithdrawsRequestsByEpoch[_user][epoch]` nor decrements `instantWithdrawClaimsByEpoch[epoch]`. The code assumes the per-epoch basis equals the outstanding receipt — the same class of assumption as the kernel bug (a value's encoding/state is trusted to hold an invariant it does not actually hold). If a default is finalized in the same epoch in which a user both claimed an earlier funded instant receipt and opened a new one, the stale basis makes the defaulted-claim path underflow and permanently revert, freezing the user's new receipt and corrupting the recovery reserve accounting.

### Finding Description
- `requestInstantWithdraw` records both `instantWithdrawsRequests[_user] += _amount` and `instantWithdrawsRequestsByEpoch[_user][epochNumber] += _amount` plus `instantWithdrawClaimsByEpoch[epochNumber] += _amount` (`IdleCreditVault.sol:366-374`).
- `claimInstantWithdrawRequest` burns the receipt and zeroes `instantWithdrawsRequests[_user]` only; the two per-epoch counters are left stale (`IdleCreditVault.sol:387-392`).
- On default finalization in the same epoch, `_claimDefaultedInstantWithdrawRequest` computes `claimBasis = instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` (stale: includes the already-claimed amount) and executes `instantWithdrawsRequests[_user] -= claimBasis` (`IdleCreditVault.sol:843-848`). Since the aggregate only holds the new (smaller) request, the subtraction underflows and reverts.
- Because `claimInstantWithdrawRequest` always runs `_claimDefaultedInstantWithdrawRequest` first once `defaultRecoveryFinalized && defaultInstantWithdrawsFinalized` (`IdleCreditVault.sol:382-386`), every subsequent claim for that user reverts permanently.
- Additionally, `defaultPendingClaimBasis`/`_defaultPrefundedInstantReserve` (`IdleCreditVault.sol:644-648`, `716-723`) treat `instantWithdrawClaimsByEpoch[epochNumber] - pendingInstantWithdraws` as already-held prefunded reserve. The already-paid-out (claimed) portion is therefore counted as recovery reserve that physically left the contract, inflating `defaultRecoveryPrice`/`defaultRecoveryReserve` in `finalizeDefaultRecovery` (`IdleCreditVault.sol:685-699`) and leaving the last recovery claimants with insufficient balance — `_transferDefaultRecovery` reverts when the reserve is exhausted.

### Impact Explanation
Two direct impacts: (1) permanent freezing of the affected user's newly requested instant receipt (their strategy-token receipt is burned-locked behind an always-reverting claim path), and (2) phantom recovery reserve that overstates `defaultRecoveryPrice`, so earlier recovery claimants are paid from tokens that were already withdrawn, causing later claimants' `_transferDefaultRecovery` transfers to revert — permanent loss/freezing of their recovery share. No honest-party action can repair either, since per-epoch counters are monotonic and `epochNumber`/`defaultRecoveryEpoch` are fixed at finalization.

### Likelihood Explanation
Requires the same-epoch sequence: request instant withdraw → funded (prefunded/instant liquidity mode, where `claimInstantWithdrawRequest` has no epoch-wait gate) → claim → new `requestInstantWithdraw` → default finalized while `epochNumber` is unchanged. This only needs an unprivileged tranche holder and normal epoch/default sequencing by honest roles; no privileged misbehavior is required.

### Recommendation
On a successful normal instant claim, clear the per-epoch basis: in `claimInstantWithdrawRequest`, decrement `instantWithdrawsRequestsByEpoch[_user][epochAtRequest]` and `instantWithdrawClaimsByEpoch[epoch]` by the claimed amount (track the request epoch at request time, e.g. a `lastInstantWithdrawRequest` mapping), or make `_claimDefaultedInstantWithdrawRequest` cap `claimBasis` at `instantWithdrawsRequests[_user]` and never treat claimed amounts as prefunded reserve in `_defaultPrefundedInstantReserve`.

### Proof of Concept
```solidity
// Foundry fork PoC sketch (prefunded/instant mode, same epoch N):
// 1. user requests instant withdraw X at epoch N
cdo.requestInstantWithdraw(X, user);            // byEpoch[user][N]=X, claimsByEpoch[N]=X
// 2. epoch-funded liquidity is present; user claims X
cdo.claimInstantWithdrawRequest(user);          // aggregate=0, byEpoch/claimsByEpoch STILL X
// 3. user requests Y < X in the same epoch N
cdo.requestInstantWithdraw(Y, user);            // aggregate=Y, byEpoch[user][N]=X+Y
// 4. borrower defaults; manager/owner finalize default in epoch N
cdo.finalizeDefaultRecovery(recovered, source); // pendingInstantWithdraws != 0 -> instant finalized
// 5. user claims -> reverts forever
vm.expectRevert();                              // underflow: Y - (X + Y)
cdo.claimInstantWithdrawRequest(user);
// 6. defaultRecoveryReserve was inflated by X (paid out to user earlier),
//    so the last defaulted-epoch claimant's _transferDefaultRecovery reverts.
```