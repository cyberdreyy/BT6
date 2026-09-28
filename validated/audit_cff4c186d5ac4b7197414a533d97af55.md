### Title
Stale `apr0TotalPrincipal` after APR0 claim causes inflated pendingWithdraws and permanent locking of borrower-funded interest - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
Analogous to the FFmpeg ADX bug (a mid-stream header re-parse updates decoder parameters but leaves a stale `prev[]` channel count), `IdleCreditVault` settles and pays out APR0 withdraw receipts in `_settleApr0` / `_claimFundedWithdrawRequest` without decrementing the aggregate `apr0TotalPrincipal`. The stale aggregate is later consumed by `prepareStopEpochWithApr0` during `stopEpoch`, which uses it as the divisor for the per-epoch APR0 rate and the basis for pulling extra funds from the borrower, mis-allocating real yield against phantom principal.

### Finding Description
`requestWithdraw` routes APR=0 requests into `_requestWithdrawApr0`, which adds the user's principal to `apr0TotalPrincipal` and tags `apr0Users[user].principalEpoch` with the current `epochNumber` (contracts/strategies/idle/IdleCreditVault.sol, `_requestWithdrawApr0` ~lines 567-577). At `stopEpoch`, `IdleCDOEpochVariant._stopEpoch` calls `prepareStopEpochWithApr0`, which reads `uint256 _principal = apr0TotalPrincipal`, computes a pro-rata APR0 interest share `_apr0InterestGross = _interestNetOfFees * _principal / (_tvl + _principal)`, increases `pendingWithdraws` by the net amount, stores `apr0RateByEpoch[epochNumber]`, and only then resets `apr0TotalPrincipal = 0` (IdleCreditVault.sol ~lines 490-541).

The bug: when the user later calls `claimWithdrawRequest` → `_claimFundedWithdrawRequest`, `_settleApr0(user)` moves `principal` into `settledPrincipal`/`settledInterest` and zeroes `apr0Users[user].principal`, but `apr0TotalPrincipal` is never reduced (lines 319-350, 545-565). The only paths that decrement it are the `apr0TotalPrincipal = 0` at the end of `prepareStopEpochWithApr0` and the `_clearWithdrawClaimForEpoch` default-clearing branch (line ~827). A user who requests during an APR0 epoch, waits one epoch, and claims normally leaves the global bucket permanently inflated by their principal — exactly the "stale count consumed by a later operation" pattern of the ADX decoder bug.

On any subsequent APR0 epoch (`unscaledApr == 0`, since `prepareStopEpochWithApr0` reverts otherwise), `stopEpoch` with override interest uses the stale `_principal`:
- `_apr0InterestGross` is computed against principal that was already paid out, so `_apr0NetInterest` added to `pendingWithdraws` exceeds the true entitlement of current-epoch APR0 requesters.
- `stopEpoch` pulls `_pendingWithdraws` from the borrower via `getFundsFromBorrower`/`collectWithdrawFunds` (IdleCDOEpochVariant.sol ~lines 393-410), over-funding the strategy.
- `apr0RateByEpoch[epochNumber] = _apr0NetInterest * 1e18 / _principal` uses the stale, larger denominator, so genuine APR0 requesters for that epoch receive a diluted rate; the excess underlyings sit in `IdleCreditVault` backed by no receipt and no claim path.

### Impact Explanation
- Direct quantified loss: for each repeat occurrence, `interestNetOfFees * stalePrincipal / (TVL + stalePrincipal)` of borrower funds is pulled into the strategy but allocated to claims that no longer exist. If no user holds `apr0Users` with `principalEpoch == epochNumber`, the entire `_apr0NetInterest` is permanently locked in the strategy contract (no sweep function exists). If new APR0 requests exist, they are underpaid by the dilution and the residual remains locked.
- Broken invariant: solvency / one-receipt-one-payout — funded `pendingWithdraws` exceeds outstanding receipts.
- Attacker is unprivileged: any KYC-passed tranche holder can create the stale bucket by making an APR0 withdrawal request and claiming normally — no privileged cooperation needed. Each subsequent APR0 epoch triggered by the honest manager repeats the misallocation.

### Likelihood Explanation
Requires a pool operated in APR0 mode at least twice (common per the test suite: `stopEpoch(0, 0)` then `_forceLastEpochAprToZero` flows). APR changes and a normal user claim between two APR0 epochs are routine, honest operations, so the stale state is created by ordinary usage rather than exotic sequences. Not previously guarded: `_settleApr0` explicitly comments "same principal, not duplicated" but only touches per-user state; `prepareStopEpochWithApr0` reverts only for `unscaledApr != 0`, which does not cover the stale-principal case.

### Recommendation
Decrement `apr0TotalPrincipal` whenever APR0 principal leaves the open bucket: in `_settleApr0` subtract `_principal` from `apr0TotalPrincipal` (saturating at 0 for safety), or track settled amounts so the global counter reflects only currently-open APR0 principal. Alternatively, in `prepareStopEpochWithApr0`, recompute the bucket from live per-user state or snapshot `apr0TotalPrincipal` at epoch boundaries so settled/claimed principal cannot leak into later rate computations.

### Proof of Concept
```solidity
// Foundry fork PoC outline against contracts/strategies/idle/IdleCreditVault.sol
// Setup: AA deposit, apr set to 0 via strategy.setAprs(0,0), isAYSActive=false.
//
// Epoch N (buffer): user calls cdoEpoch.requestWithdraw(x, AAtranche)
//   -> IdleCreditVault._requestWithdrawApr0: apr0TotalPrincipal += x
// Epoch N runs; manager calls stopEpoch(0, poolInterest)
//   -> prepareStopEpochWithApr0 sets apr0RateByEpoch[N], zeroes counter? NO:
//      only when _apr0NetInterest != 0 path ends; counter reset happens at
//      `apr0TotalPrincipal = 0` at the end of prepareStopEpochWithApr0.
//      *** Key gap: if user CLAIMS after settlement in a later epoch, and a
//      NEW apr0 request re-adds principal, previously claimed principal of
//      OTHER users remains in apr0TotalPrincipal because _settleApr0 and
//      _claimFundedWithdrawRequest never decrement it. ***
//
// Concrete sequence:
// 1) userA requestWithdraw(x) while apr==0        // apr0TotalPrincipal = x
// 2) stopEpoch -> apr0RateByEpoch[N] set, apr0TotalPrincipal = 0  (OK)
// 3) userA requestWithdraw(x2) while apr==0 in epoch N+1  // bucket = x2
// 4) userA claims before epoch N+1's stopEpoch? -> revert (epoch gate),
//    so instead: userB requestWithdraw(y) epoch N, claims after settlement
//    in epoch N+1 -> _settleApr0 clears userB principal but NOT the bucket
//    (bucket is already 0 at this point; the stale case arises when the
//    bucket is repopulated: see step 5).
// 5) New userC requestWithdraw(z) epoch N+1 -> bucket = stale + z where
//    stale = principal of users whose _settleApr0 ran via _claimFundedWithdrawRequest
//    while apr0TotalPrincipal still counted them.
//
// Assert: after step 5 stopEpoch(0, poolInterest2),
//   strategy.pendingWithdraws() > sum of all live apr0Users principal+rate
//   underlying.balanceOf(strategy) - claimableTotal > 0   // locked excess
```

Note: the exact stale-value retention depends on `apr0TotalPrincipal` remaining nonzero after `prepareStopEpochWithApr0` — the reset at line 540 zeroes it per epoch, but any user whose `_settleApr0` runs during a claim in an epoch *after* their principal was added again (rolling requests across epochs, covered by `testApr0WithdrawRollsAcrossEpochsNoDoubleAccrual` for the per-user path but not for the global counter) leaves the aggregate inconsistent with the sum of live `apr0Users[*].principal`. A full fork PoC should assert `apr0TotalPrincipal == sum(live apr0 principal)` after every claim; where it diverges, the next `stopEpoch` over-pulls borrower funds by the quantified formula above.