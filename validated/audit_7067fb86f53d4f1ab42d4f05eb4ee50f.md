### Title
Unfunded instant-withdraw receipts from earlier epochs share the aggregate `instantWithdrawsRequests` entry and escape the default haircut, claiming at par from the recovery reserve - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
The XWiki bug class — two logically distinct objects collapsing into one shared entry, so one overwrites/escapes the accounting of the other — maps onto `IdleCreditVault`'s instant-withdraw bookkeeping. Instant receipts are tracked twice: per-epoch (`instantWithdrawsRequestsByEpoch[user][epoch]`, `instantWithdrawClaimsByEpoch[epoch]`) and in a single per-user aggregate (`instantWithdrawsRequests[user]`). Default finalization prices the recovery reserve using only the *current* epoch key (`defaultPendingClaimBasis` reads `instantWithdrawClaimsByEpoch[epochNumber]`), while `claimInstantWithdrawRequest` pays the full cross-epoch aggregate at par. A receipt minted in an earlier epoch that was never fully funded therefore collides with the current-epoch bucket: it is invisible to the haircut basis yet still paid out 1:1.

### Finding Description
- `requestInstantWithdraw` increments both `instantWithdrawsRequests[_user] += _amount` (aggregate, line ~366) and `instantWithdrawsRequestsByEpoch[_user][currentEpoch]` / `instantWithdrawClaimsByEpoch[currentEpoch]` (lines ~371-372).
- `pendingInstantWithdraws` is a global aggregate; `collectInstantWithdrawFunds` only decrements it. If the CDO collects less than requested in epoch N, the unfunded remainder persists indefinitely — the code comments acknowledge this ("`pendingInstantWithdraws` is the still-unfunded remainder", lines ~636-640, ~715-721).
- On a later default in epoch M > N, `defaultPendingClaimBasis()` adds only `instantWithdrawClaimsByEpoch[epochNumber]` (= epoch M) to the recovery basis, and `_defaultPrefundedInstantReserve()` likewise only credits `instantWithdrawClaimsByEpoch[epochNumber] - pendingInstantWithdraws`. The stale epoch-N receipt inside `instantWithdrawsRequests[user]` is excluded from `totalBasis`, so `defaultRecoveryPrice`/`defaultRecoveryReserve` are computed as if it did not exist.
- `_claimDefaultedInstantWithdrawRequest` clears only `instantWithdrawsRequestsByEpoch[user][defaultRecoveryEpoch]` — it subtracts that from the aggregate but leaves the epoch-N portion untouched. Afterwards `claimInstantWithdrawRequest` burns `instantWithdrawsRequests[user]` (including the stale amount) and calls `_transferFundedClaim`, paying it fully from strategy-held underlyings — the same pot backing `defaultRecoveryReserve` for haircutted claimants.

### Impact Explanation
Direct theft / insolvency of the recovery reserve. The attacker claims `staleBasis` at par while the reserve was priced only for `instantWithdrawClaimsByEpoch[defaultEpoch]`; every other defaulted receipt holder's pro-rata recovery is diluted by the full stale amount. Broken invariant: "one receipt, one haircut" — all receipts outstanding at default should share `defaultRecoveryPrice`, but epoch-aliasing between the aggregate ledger and the per-epoch ledger lets the stale receipt skip the haircut entirely.

### Likelihood Explanation
Requires an instant-withdraw receipt that remains partially unfunded across an epoch boundary (acknowledged possible: `startEpoch` may move less cash than the instant queue needed), followed by a borrower default — an honest-manager sequence, not attacker-controlled. The attacker only needs to be an unprivileged receipt holder who delays claiming. No guard stops it: `_claimDefaultedInstantWithdrawRequest` is keyed to `defaultRecoveryEpoch` only, `defaultInstantWithdrawsFinalized` does not distinguish epochs, and `requestWithdraw`'s epoch-collision guard (lines ~263-271) does not cover the instant path at all. Uncertainty: whether the CDO ever leaves `pendingInstantWithdraws` non-zero across a successful `stopEpoch` could not be fully confirmed from the indexed code; if stopEpoch always force-funds the instant bucket, the stale-receipt window closes and this reduces to a non-issue.

### Recommendation
Track pending instant basis per epoch globally (not just per current epoch) or make `defaultPendingClaimBasis`/`_defaultPrefundedInstantReserve` sum all outstanding epochs (e.g., carry `pendingInstantWithdraws`-backed basis forward), and have `claimInstantWithdrawRequest` iterate/`clear` all unfunded per-epoch receipts at `defaultRecoveryPrice` instead of paying the aggregate at par. Alternatively, prevent requests from outliving their epoch: require `instantWithdrawClaimsByEpoch[epochNumber]` to fully cover `pendingInstantWithdraws` before a new epoch starts.

### Proof of Concept
Foundry fork sketch (mainnet pool / local harness in `test/foundry/IdleCreditVault.t.sol` style):

```solidity
// 1. Epoch N: deposit, enable instant withdrawals, startEpoch.
// 2. attacker.requestInstantWithdraw(A) -> instantWithdrawsRequestsByEpoch[attacker][N]=A,
//    pendingInstantWithdraws=A.
// 3. Owner/manager calls collectInstantWithdrawFunds(A/2): only half funded;
//    pendingInstantWithdraws=A/2 persists; attacker does not claim.
// 4. Epochs roll; in epoch M borrower defaults (stopEpoch(0,0), _checkDefault).
// 5. finalizeDefault(recovered, src): totalBasis excludes the A/2 stale instant basis
//    because instantWithdrawClaimsByEpoch[M] == 0 for attacker.
// 6. attacker calls claimInstantWithdrawRequest:
//    _claimDefaultedInstantWithdrawRequest clears epoch M (0) -> no-op;
//    _burn + _transferFundedClaim pay full A (or remaining A/2) at par.
// Assert: defaultRecoveryReserve decreased by more than A/2 * defaultRecoveryPrice,
// i.e. attacker received > haircut; other claimants' reserve shortfall == stale amount.
```