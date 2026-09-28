### Title
Epoch-key mismatch in loss-adjusted and defaulted withdraw claims lets receipts escape the haircut and claim at par — (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
Analogous to CVE-2022-2663 (a message being matched to the wrong context), `IdleCreditVault` keys haircut/recovery records to `epochNumber` at *finalization* time, but looks them up through `lastWithdrawRequest[_user]` / `withdrawsRequestsByEpoch`, which store the *request-time* epoch. Because `epochNumber` is incremented inside `deposit()` while `isEpochRunning()` is still true (i.e., during the `stopEpoch` funding sequence), any epoch boundary crossed between request and finalization makes the recovery record unmatchable, so the receipt is paid at par through the funded-claim path.

### Finding Description
The relevant code:

- `requestWithdraw` records `lastWithdrawRequest[_user] = currentEpoch` and `withdrawsRequestsByEpoch[_user][currentEpoch]` using the epoch number at request time (`IdleCreditVault.sol:260,282,293`).
- `deposit()` increments `epochNumber += 1` while `isEpochRunning()` is still true — i.e., during the stopEpoch repayment deposit (`IdleCreditVault.sol:607-614`).
- `collectWithdrawFunds` stores the loss haircut under `lossRecoveryPriceByEpoch[epochNumber]` — the epoch number *at collection time* (`IdleCreditVault.sol:411-421`).
- `_claimLossAdjustedWithdrawRequest` looks the haircut up under `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` — the *request* epoch (`IdleCreditVault.sol:789-792`).
- The pre-request guard at `IdleCreditVault.sol:263-270` uses the same `lastWithdrawRequest` key, so it cannot detect a haircut stored under a different epoch key.
- Same pattern on the default path: `defaultRecoveryEpoch = epochNumber` at `finalizeDefaultRecovery` (`IdleCreditVault.sol:693`), while `_claimDefaultedWithdrawRequest` and `_claimDefaultedInstantWithdrawRequest` read `withdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` / `instantWithdrawsRequestsByEpoch[_user][defaultEpoch]` (`IdleCreditVault.sol:773-774, 843-844`), which are populated under the request-time epoch (`IdleCreditVault.sol:293, 371`).

If the value of `epochNumber` used at record time differs from the value used at lookup time — which happens whenever the funding deposit bumps the counter before `collectWithdrawFunds` runs, or when `finalizeDefaultRecovery` executes after an epoch transition — the keyed lookups return 0:

- `_claimLossAdjustedWithdrawRequest` returns 0 (price lookup misses).
- `_claimDefaultedWithdrawRequest` / `_claimDefaultedInstantWithdrawRequest` find `claimBasis == 0` and return.
- `claimWithdrawRequest` then falls through to `_claimFundedWithdrawRequest`, which pays `withdrawsRequests[_user]` (and settled APR0 principal/interest) at par via `_transferFundedClaim` (`IdleCreditVault.sol:319-349`).
- `claimInstantWithdrawRequest` similarly pays the full `instantWithdrawsRequests[_user]` at par (`IdleCreditVault.sol:387-392`).

The gating check `epochNumber <= lastWithdrawRequest[_user]` only enforces a one-epoch wait; it does not bind the claim to the epoch that carries the haircut.

### Impact Explanation
A user whose receipt should be haircutted (loss socialized via `lossRecoveryPriceByEpoch`, or default-haircut via `defaultRecoveryPrice`) instead receives 100% of `claimBasis`. Since only the reduced amount was ever collected into the strategy (`collectWithdrawFunds` pulled only `_amount < pendingBasis`, or the recovery reserve holds only `defaultRecoveryPrice`-scaled funds), the excess payout is drawn from funds belonging to other receipt holders and to the default recovery reserve — direct theft / insolvency for the remaining claimants, who will be unable to withdraw.

### Likelihood Explanation
The trigger only requires an unprivileged lender to place a `requestWithdraw`/`requestInstantWithdraw` in an epoch that later ends with `stopEpochWithDuration(_lossAmount)` or a borrower default, then call `claimWithdrawRequest`/`claimInstantWithdrawRequest`. Whether the keys actually diverge depends on intra-`stopEpoch` ordering of `deposit()` (which increments `epochNumber`) versus `collectWithdrawFunds`, and on the epoch at which the CDO calls `finalizeDefaultRecovery`; I could not fully verify `IdleCDOEpochVariant.stopEpoch*` call ordering within the available context. If `collectWithdrawFunds` executes after the incrementing `deposit()`, the haircut is stored under `N+1` while every receipt is keyed under `N`, making the loss-adjusted path unreachable for *all* receipts — a systematic rather than edge-case failure. If ordering happens to be consistent today, the design is still fragile to any path where finalization lands in a different epoch than the request (e.g., a default finalized during a later buffer after a prior `epochNumber` bump).

### Recommendation
- Key all recovery records by the same epoch identifier stored at request time: record the loss under the epoch of the receipts being haircutted (e.g., `epochNumber - 1` semantics or an explicit `_claimEpoch` argument from the CDO), or store the request epoch together with the recovery record instead of relying on `lastWithdrawRequest`.
- Have `collectWithdrawFunds`/`finalizeDefaultRecovery` accept the target epoch explicitly and revert if no pending basis exists under that key, so a mis-keyed record cannot silently strand receipts.
- Add an invariant check in `claimWithdrawRequest`: if `withdrawsRequestsByEpoch[_user][e] != 0` for any epoch `e` with a nonzero `lossRecoveryPriceByEpoch[e]` or `e == defaultRecoveryEpoch`, force the haircut path before allowing the funded at-par claim.

### Proof of Concept
A Foundry fork PoC (could not be executed in this environment) would:

1. Deposit AA (or BB) as `attacker` and `victim` during epoch N's running phase.
2. Both call `requestWithdraw` in epoch N → receipts keyed under `N`.
3. Manager calls `stopEpochWithDuration` with a partial repayment producing `_amount < pendingWithdraws`, such that `lossRecoveryPriceByEpoch` is written under the post-deposit `epochNumber` (N+1) while `lastWithdrawRequest` remains N.
4. `attacker` calls `claimWithdrawRequest`: `lossRecoveryPriceByEpoch[N] == 0` → `_claimLossAdjustedWithdrawRequest` no-ops → `_claimFundedWithdrawRequest` pays full `withdrawsRequests[attacker]` at par.
5. Assert `victim`'s subsequent claim reverts on insufficient strategy balance or receives less than `claimBasis * lossRecoveryPriceByEpoch[N+1]`, quantifying the stolen share.