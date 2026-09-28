### Title
Instant-withdraw receipts from non-default epochs are excluded from default recovery and become permanently unclaimable - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The HDF5 OOB-read bug class (reading outside the correct index/allocation) maps to per-epoch receipt accounting in `IdleCreditVault`: default recovery reads instant-withdraw basis only at the *current* `epochNumber` index (`defaultPendingClaimBasis`, line 647) and later only at `defaultRecoveryEpoch` (`_claimDefaultedInstantWithdrawRequest`, line 844), while the aggregate `pendingInstantWithdraws` and per-user `instantWithdrawsRequests[_user]` can carry receipts recorded under *earlier* epoch indexes. Receipts indexed out of the default epoch are never counted in the recovery basis and can never be cleared.

### Finding Description
`requestInstantWithdraw` records receipts per-epoch: `instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount` and `instantWithdrawClaimsByEpoch[currentEpoch] += _amount` (lines 367-374). There is no guarantee that `collectInstantWithdrawFunds` funds every request within the same epoch — `pendingInstantWithdraws` simply remains non-zero and the per-epoch entries persist under their original epoch key.

At default finalization, `defaultPendingClaimBasis()` returns `pendingWithdraws + instantWithdrawClaimsByEpoch[epochNumber]` — only the *current* epoch's instant bucket is included as recovery basis (lines 644-649). Symmetrically, `_defaultPrefundedInstantReserve` only reads `instantWithdrawClaimsByEpoch[epochNumber]` (line 719). After finalization, `_claimDefaultedInstantWithdrawRequest` clears only `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` (line 844), and the funded-claim path `claimInstantWithdrawRequest` pays `instantWithdrawsRequests[_user]` in full via `_transferFundedClaim`, which must not spend `defaultRecoveryReserve` — meaning an unfunded pre-default receipt has no underlying backing to pay out.

Concrete sequence (attacker = any KYC-passed lender, honest manager/owner):
1. Epoch N running, instant withdrawals enabled. Attacker calls `requestInstantWithdraw`; receipt stored under epoch index N; `pendingInstantWithdraws` increases.
2. Borrower/CDO never sends instant funds; `stopEpoch` ends epoch N with `pendingInstantWithdraws` still > 0 and `instantWithdrawClaimsByEpoch[N]` still populated.
3. Epoch N+1 runs; borrower defaults; `finalizeDefault` → `finalizeDefaultRecovery` runs at `epochNumber == N+1`. The attacker's basis in `instantWithdrawClaimsByEpoch[N]` is read at the wrong index (N+1) and excluded from `totalBasis`, while `pendingInstantWithdraws != 0` still sets `defaultInstantWithdrawsFinalized = true`.
4. Attacker calls `claimInstantWithdrawRequest`: `_claimDefaultedInstantWithdrawRequest` reads slot `[defaultRecoveryEpoch = N+1]` → 0; funded path then tries to pay the full `instantWithdrawsRequests[_user]` out of funded underlyings that don't exist for this receipt → either the transfer drains other claimants' reserve/funded cash or reverts, permanently freezing the receipt.

### Impact Explanation
Two-sided impact: (a) pre-default unfunded instant receipts are permanently unclaimable — a permanent freezing of user funds; (b) because their basis was excluded from `totalBasis` yet `pendingInstantWithdraws` still counted them, the computed `defaultRecoveryPrice` is overstated for all other claimants and `_claimDefaultedInstantWithdrawRequest` may subtract a `claimBasis` larger than the funded remainder, corrupting `pendingInstantWithdraws` and `instantWithdrawClaimsByEpoch[defaultEpoch]` accounting. Quantified loss: the full unfunded pre-default instant receipt amount.

### Likelihood Explanation
Requires a borrower's instant funding to lapse across an epoch boundary followed by a default — both are normal, non-attacker-controlled protocol states. The attacker only needs to hold a tranche/instant receipt, which any KYC'd lender can obtain. No privileged misbehavior is required.

### Recommendation
Track instant-claim basis inclusively: either fold `instantWithdrawClaimsByEpoch` entries from all prior epochs into `defaultPendingClaimBasis` (e.g., maintain a running unfunded total), or force settlement/clearing of stale instant receipts at `stopEpoch`/`startEpoch`, and make `_claimDefaultedInstantWithdrawRequest` iterate or aggregate all epochs with non-zero user basis rather than only `defaultRecoveryEpoch`.

### Proof of Concept
A Foundry fork PoC (sketch): deposit via `cdoEpoch`, enable instant withdrawals via `setInstantWithdrawParams`, `startEpoch`, call `requestInstantWithdraw`, warp past `epochEndDate`, `stopEpoch` without funding the instant bucket, `startEpoch` again, force borrower default (`stopEpoch(0,0)` with borrower balance 0), `finalizeDefault(recovered, manager)`, then `claimInstantWithdrawRequest` — assert the epoch-N receipt is neither paid at `defaultRecoveryPrice` nor claimable, and `instantWithdrawsRequests[user]` remains non-zero.

Caveat: verification was limited to the indexed code; whether `IdleCDOEpochVariant` guarantees instant requests are always funded within their request epoch could not be fully confirmed — if it does, the precondition collapses and this reduces to a non-issue.