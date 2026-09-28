### Title
Pre-default instant-withdraw receipts escape the recovery haircut and drain prefunded current-epoch claims at par - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.finalizeDefaultRecovery` computes the default-epoch claim basis with `defaultPendingClaimBasis()`, which counts instant-withdraw receipts only via `instantWithdrawClaimsByEpoch[epochNumber]` — the *current* epoch. Instant receipts requested in an earlier epoch but never collected remain in the aggregate `instantWithdrawsRequests[_user]` and `pendingInstantWithdraws`, yet are excluded from the haircut basis. After finalization, `claimInstantWithdrawRequest` pays that leftover aggregate **at par** through `_transferFundedClaim`, which only protects `defaultRecoveryReserve`. The strategy's actually-held instant funds can exceed the amount credited into the reserve (`_defaultPrefundedInstantReserve` returns `instantBasis - pendingInstantWithdraws` using an *aggregate* pending counter), so a stale-epoch receipt holder withdraws unfunded/haircuttable value at 100 cents, directly reducing what defaulted-epoch claimants recover.

### Finding Description
Analog of CVE-2019-13729 (use-after-free → stale-object reuse) mapped to the stale-epoch claim surface:

- `requestInstantWithdraw` records `instantWithdrawsRequestsByEpoch[user][epochNumber]` and bumps the global `pendingInstantWithdraws` (lines 356–375). Receipts are never expiry-cleaned per epoch.
- On default, `defaultPendingClaimBasis` (lines 644–649) adds only `instantWithdrawClaimsByEpoch[epochNumber]` — receipts booked under a prior epoch are silently dropped from the recovery basis.
- `_defaultPrefundedInstantReserve` (lines 716–723) subtracts the *aggregate* `pendingInstantWithdraws` (all epochs) from the *current-epoch* `instantBasis`. When old unfunded receipts inflate the aggregate, `prefundedReserve` is understated or zero, so underlying already held by the strategy is not folded into `defaultRecoveryReserve` even though it partially backs current-epoch claimants.
- After `defaultRecoveryFinalized`, `claimInstantWithdrawRequest` (lines 380–393) clears only `instantWithdrawsRequestsByEpoch[user][defaultRecoveryEpoch]` and then pays the whole remaining `instantWithdrawsRequests[_user]` at par via `_transferFundedClaim`, which merely enforces `balance - reserve >= amount` (lines 897–907). The stale-epoch holder therefore redeems 1:1, consuming prefunded cash that finalization implicitly allocated to haircut claimants.

Broken invariant: one receipt — one (haircut-adjusted) payout; loss socialization applied uniformly across pending receipts.

### Impact Explanation
Direct theft from default-recovery claimants, quantified as `min(staleInstantReceipts, heldInstantFunds − prefundedReserveCredited)`. The exploiter is a plain unprivileged tranche holder who made an instant-withdraw request before the default epoch. Every wei they extract at par is a wei removed from the pool `defaultRecoveryPrice` was computed against, so defaulted-epoch normal/instant receipt holders and post-default claimants recover less than the finalized ratio. No privileged misbehavior is required — only the honest `finalizeDefaultRecovery` call sequencing around the attacker's earlier `requestInstantWithdraw`.

### Likelihood Explanation
Requires a borrower default with outstanding cross-epoch instant receipts and partial instant funding — plausible configurations (instant withdrawals are a supported mode and funding is best-effort via `collectInstantWithdrawFunds`). The attacker needs no timing privileges; they simply keep an unclaimed instant receipt across an epoch boundary. Caveat: exploitation magnitude depends on the gap between aggregate `pendingInstantWithdraws` and current-epoch `instantBasis`, and on the CDO not gating `claimInstantWithdrawRequest` post-default (I could not fully confirm the IdleCDOEpochVariant call path within the iteration budget — the strategy-side accounting flaw is nonetheless concrete).

### Recommendation
Track the unfunded/prefunded instant split per epoch, not globally: in `defaultPendingClaimBasis` include `instantWithdrawClaimsByEpoch[e]` for every epoch with an outstanding balance (or maintain a single aggregate instant basis), and compute `_defaultPrefundedInstantReserve` as `heldInstantFunds` (balance attributable to instant receipts) rather than `instantBasis − pendingInstantWithdraws`. Alternatively, fold all stale-epoch instant receipts into the default haircut basis and only pay the remainder of `instantWithdrawsRequests[_user]` at par for epochs proven funded.

### Proof of Concept
```solidity
// Foundry fork PoC (schematic) — contracts/strategies/idle/IdleCreditVault.sol
// Setup: standard epoch variant, instant withdraws enabled.
// Epoch N:
//   1. attacker.requestInstantWithdraw(A) via CDO -> instantWithdrawsRequestsByEpoch[attacker][N] = A
//   2. CDO collects only part of the instant queue: collectInstantWithdrawFunds(f)
//      pendingInstantWithdraws = A - f ; strategy holds f underlying
// Epoch N+1 (new epoch, borrower active):
//   3. honestUser.requestInstantWithdraw(B) -> instantWithdrawClaimsByEpoch[N+1] = B
//   4. borrower defaults mid-epoch; manager calls finalizeDefaultRecovery(R, source)
//      defaultPendingClaimBasis() = pendingWithdraws + B          // A missing!
//      _defaultPrefundedInstantReserve(): instantBasis=B, pending=A-f+B
//        -> B > (A-f+B) is false -> prefundedReserve = 0          // f not reserved
//      defaultRecoveryPrice = (R + reserve + 0) / basis           // f excluded
// Post-finalization:
//   5. attacker calls claimInstantWithdrawRequest(attacker) via CDO
//      _claimDefaultedInstantWithdrawRequest clears only epoch N+1 entry (==0)
//      then pays instantWithdrawsRequests[attacker] = A at par via _transferFundedClaim
//      because balance - reserve = f + ... >= min(A, available)
// Attacker receives up to A at par while all epoch-N+1 claimants take
// defaultRecoveryPrice < 1. Recovered loss ~= leaked par payout.
```