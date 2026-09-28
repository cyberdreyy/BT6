### Title
Mid-epoch `deposit()` increments `epochNumber`, so APR0 withdraw requests keyed to the old epoch settle against a missing rate and underpay - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The Nouns Builder bug is an index that is never normalized into the expected range: `baseTokenId` starts at `reservedUntilTokenId` (>100), so the first founder's slot is written outside the 0–99 schedule and the founder ends up with fewer assigned IDs than entitled. The same class exists in `IdleCreditVault`: `deposit()` bumps `epochNumber` whenever `isEpochRunning()` is true, even for a deposit that is not part of `stopEpoch`. APR0 withdraw requests index their claim by `apr0Users[user].principalEpoch` and settle against `apr0RateByEpoch[reqEpoch]`, but the per-epoch rate is only written in `prepareStopEpochWithApr0` under the (now shifted) `epochNumber`. The request settles at index N while the rate lands at index N+1 — an off-by-one indexing mismatch that leaves the requester with fewer funds than entitled.

### Finding Description
`deposit()` in `IdleCreditVault.sol:596-617` does:

```solidity
if (IIdleCDOEpochVariant(idleCDO).isEpochRunning()) {
    // deposit done on stopEpoch (before setting the var to false) so we reset the counter
    totEpochDeposits = 0;
    epochNumber += 1;
}
```

Any deposit routed through the CDO while an epoch is running — not only the `stopEpoch` funding deposit the comment assumes — increments `epochNumber`. Meanwhile:

- `_requestWithdrawApr0` stamps `principalEpoch = epochNumber` at request time (`IdleCreditVault.sol:570-573`).
- `prepareStopEpochWithApr0` stores the realized per-epoch APR0 rate at `apr0RateByEpoch[epochNumber]` using the *current* (incremented) counter (`IdleCreditVault.sol:537`).
- `_settleApr0` pays the requester `principal * apr0RateByEpoch[_reqEpoch] / 1e18` once `_reqEpoch < epochNumber` (`IdleCreditVault.sol:545-564`).

If a user requests an APR0 withdraw in epoch N and an unrelated deposit raises `epochNumber` to N+1 before `stopEpoch`, the rate for that epoch is stored under key N+1 (or a later value if multiple deposits occur), while the user settles against `apr0RateByEpoch[N] == 0`. Just like the founder whose ID lands outside the 0–99 schedule, the claim is keyed to a slot that is never populated, so `settledInterest` stays 0 and the user receives only principal — fewer tokens than the pro-rata share `prepareStopEpochWithApr0` actually computed and added to `pendingWithdraws`.

### Impact Explanation
APR0 withdraw requesters lose their entire accrued epoch interest whenever a deposit lands between their request and `stopEpoch`. The `apr0NetInterest` that was added to `pendingWithdraws` is funded by the borrower via `collectWithdrawFunds`/`stopEpochWithDuration` but is never attributable to the victim — it remains stranded in the vault (or is effectively claimable by the aggregate receipt pool), producing permanent loss of unclaimed yield for the requester. Loss is bounded by that epoch's APR0 pro-rata interest.

### Likelihood Explanation
Requires (a) APR0 withdraw requests outstanding (`apr0TotalPrincipal != 0`, only possible while `unscaledApr == 0`), and (b) a `deposit()` call routed through the CDO while `isEpochRunning()` is true. Any lender or borrower-side funding path that calls the CDO's deposit during a running epoch triggers the shift; it does not require a privileged attacker — any user depositing mid-epoch through a CDO path that calls `strategy.deposit()` suffices. No existing guard (the `_onlyIdleCDO` check, the `unscaledApr != 0` revert, or the `_reqEpoch >= epochNumber` early-return) prevents it, because the victim's `_reqEpoch` is strictly less than the inflated `epochNumber`.

### Recommendation
Key the epoch counter to epoch boundaries only. Either gate the `epochNumber += 1` in `deposit()` on an explicit flag set by `stopEpoch` (rather than `isEpochRunning()`), or store the rate in `prepareStopEpochWithApr0` under the epoch index that was live when the APR0 requests were made (e.g., a dedicated `apr0Epoch` captured at request time and bumped only at `stopEpoch`). Alternatively, keep a separate `nextSettleEpoch` so mid-epoch deposits cannot alias the rate index.

### Proof of Concept
Foundry fork sketch (against `IdleCreditVault` + `IdleCDOEpochVariant`):

```solidity
// Epoch N running, unscaledApr == 0.
vm.prank(user);
cdo.requestWithdraw(amount, tranche);        // apr0Users[user].principalEpoch = N

vm.prank(otherLender);                        // or borrower funding path
cdo.depositAA(midEpochAmount);               // -> strategy.deposit() -> epochNumber = N+1

vm.prank(manager);
cdo.stopEpoch(interestOverride);             // apr0RateByEpoch[N+1] = rate; bucket closed

// Later stopEpoch settles user: _reqEpoch (N) < epochNumber (>=N+2)
// apr0RateByEpoch[N] == 0 -> user.settledInterest == 0
// assert: user claim == principal only, while pendingWithdraws included apr0NetInterest
```

Verify `apr0RateByEpoch[N] == 0`, `apr0RateByEpoch[N+1] > 0`, and the user's `claimWithdrawRequest` payout excludes interest.