### Title
Instant-withdraw receipts recorded under a prior epoch are silently dropped from default-recovery accounting but still settled at par / frozen (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The ModSecurity bug is an overwrite-instead-of-append flaw: the multipart parser silently drops bytes so downstream inspection sees a different body than the backend. The analog here is per-epoch receipt accounting that silently drops instant-withdraw basis recorded under an earlier epoch: `defaultPendingClaimBasis()` and `_defaultPrefundedInstantReserve()` only consult `instantWithdrawClaimsByEpoch[epochNumber]`, and `_claimDefaultedInstantWithdrawRequest()` only clears `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]`. Any unfunded instant receipt keyed to an older epoch is invisible to the recovery path, yet remains in `instantWithdrawsRequests[_user]` and keeps `pendingInstantWithdraws != 0`.

### Finding Description
`requestInstantWithdraw` keys each receipt to the current `epochNumber` (lines 367-372) and adds it to `instantWithdrawsRequests[_user]` and `pendingInstantWithdraws`. `getInstantWithdrawFunds`/`collectInstantWithdrawFunds` can leave a partial, unfunded remainder (`pendingInstantWithdraws > 0`, line 401) while the epoch machine continues and `deposit()` bumps `epochNumber` (line 610). A user can then make a second instant request in the new epoch, creating claims under two different epoch keys.

When the borrower defaults and `finalizeDefaultRecovery` runs:
- `defaultPendingClaimBasis()` adds only `instantWithdrawClaimsByEpoch[epochNumber]` — the older epoch's instant basis is stripped from the haircut denominator, exactly like the dropped line breaks (lines 644-648).
- `_defaultPrefundedInstantReserve()` likewise only compares the current-epoch `instantBasis` against `pendingInstantWithdraws` (lines 716-723), so it counts the old unfunded remainder as "prefunded" backing that does not exist, or excludes it entirely.
- `defaultInstantWithdrawsFinalized` is set true merely because `pendingInstantWithdraws != 0` (line 696).

Later, `claimInstantWithdrawRequest` calls `_claimDefaultedInstantWithdrawRequest`, which clears only `instantWithdrawsRequestsByEpoch[_user][defaultEpoch]` (line 844). The remainder — `instantWithdrawsRequests[_user]`, which still includes the stale-epoch unfunded receipt — is then burned and paid **at par** via `_transferFundedClaim` (lines 387-392).

### Impact Explanation
Two outcomes depending on vault balance:
- **Theft**: if the strategy holds funded underlyings beyond `defaultRecoveryReserve`, the stale unfunded receipt is paid 1:1 through `_transferFundedClaim` instead of receiving the `defaultRecoveryPrice` haircut. The attacker (any KYC'd tranche holder) withdraws full value for a receipt that was never funded nor included in the recovery basis, directly draining assets belonging to funded withdraw claimants and active LPs.
- **Permanent freeze**: if `balance - reserve < amount`, the guard at line 904 reverts and the user's receipt — plus everyone else's funded claims behind the same accounting — is frozen, since `instantWithdrawsRequests[_user]` can never be cleared.

### Likelihood Explanation
Requires an unprivileged user to hold an instant-withdraw receipt that remains partially unfunded across an `epochNumber` increment (achievable when `collectInstantWithdrawFunds` covers only part of the queue, as exercised by the mixed funded/unfunded test at `test/foundry/IdleCreditVault.t.sol:4501`), followed by a borrower default and `finalizeDefault`. The attacker controls only their own `requestWithdraw`/`claimInstantWithdrawRequest` calls; manager and owner act honestly. No existing guard stops it: the `defaultRecoveryFinalized` request guard checks `instantWithdrawsRequests[_user] != 0` only for new *normal* requests, and nothing ties `instantWithdrawClaimsByEpoch` entries across epochs.

### Recommendation
Make instant-receipt recovery epoch-agnostic like the normal-withdraw path: iterate or accumulate all outstanding `instantWithdrawsRequestsByEpoch` entries when computing `defaultPendingClaimBasis` and `_defaultPrefundedInstantReserve` (e.g., track a global `instantWithdrawClaimsTotal`), and have `_claimDefaultedInstantWithdrawRequest` haircut every uncleared per-epoch entry, not just `defaultRecoveryEpoch`. Alternatively, forbid new `requestInstantWithdraw` while `pendingInstantWithdraws != 0` so each epoch's bucket is fully resolved before another opens.

### Proof of Concept
Foundry fork sketch (pattern follows `test/foundry/IdleCreditVault.t.sol` instant-withdraw tests):

```solidity
// Setup: depositAA for attacker, start epoch 0, stop with lower APR so
// requestWithdraw routes to requestInstantWithdraw.
uint256 req1 = cdoEpoch.requestWithdraw(amt/2, address(AAtranche)); // epoch 0 receipt

// startEpoch(1); warp past instantWithdrawDelay; call getInstantWithdrawFunds
// with borrower funded for only PART of pendingInstantWithdraws -> remainder stays.
// pendingInstantWithdraws > 0, instantWithdrawClaimsByEpoch[0] = req1.

// Attacker makes second instant request in epoch 1:
uint256 req2 = cdoEpoch.requestWithdraw(rest, address(AAtranche));
// instantWithdrawsRequests[user] = req1 + req2
// instantWithdrawClaimsByEpoch[1] = req2 only  <-- req1 silently dropped

// Borrower defaults; finalizeDefault(recovered, source):
// basis = pendingWithdraws + instantWithdrawClaimsByEpoch[1]  (req1 missing)
// prefundedReserve computed from epoch-1 basis only.
// defaultRecoveryPrice is therefore overstated OR reserve understated.

// attacker.claimInstantWithdrawRequest():
// _claimDefaultedInstantWithdrawRequest clears only epoch-1 basis (req2),
// then burns and pays instantWithdrawsRequests[user] remainder (incl. req1)
// AT PAR via _transferFundedClaim -> steals funded underlyings,
// or reverts at balance-reserve guard -> permanent freeze.
```

Assert `underlying.balanceOf(attacker)` exceeds `req2 * defaultRecoveryPrice / 1e18 + req1 * defaultRecoveryPrice / 1e18`, i.e., `req1` was paid unhaircut despite never entering the recovery basis.