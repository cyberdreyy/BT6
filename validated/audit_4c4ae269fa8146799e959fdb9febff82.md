### Title
Permanent `stopEpoch` deadlock when APR0 withdraw receipts exist while `unscaledApr != 0` - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
Analogous to CVE-2019-3554 (a specific connection type crashes the accept handler, DoS-ing the service), `prepareStopEpochWithApr0` in `IdleCreditVault` hard-reverts on a specific vault state: any outstanding APR0 withdraw principal while the pool's `unscaledApr` is non-zero. Because `apr0TotalPrincipal` can only be cleared *inside* the same function that reverts, the vault deadlocks permanently — no epoch can ever be stopped again.

### Finding Description
In `prepareStopEpochWithApr0` (IdleCreditVault.sol:490-541), the function early-returns only when `apr0TotalPrincipal == 0`. Otherwise it reverts with `NotAllowed` whenever `unscaledApr != 0` (lines 505-508). `apr0TotalPrincipal` is only reset to zero at the end of this same function (line 540); it is incremented by `_requestWithdrawApr0` (line 576) when a user calls `requestWithdraw` during an APR0 epoch, and user-side settlement in `_settleApr0`/`delete apr0Users[_user]` (lines 346-348) never decrements the global counter. User claims also cannot run because `_claimFundedWithdrawRequest` requires `epochNumber` to advance (line 326), which only `stopEpoch` does. So once `apr0TotalPrincipal > 0` meets `unscaledApr != 0`, every subsequent `stopEpoch` reverts forever.

### Impact Explanation
Permanent freezing of all vault funds: no `stopEpoch` means no interest settlement, no withdraw-request funding (`collectWithdrawFunds` is only callable via the CDO during stop), no epoch advancement, and therefore no claims for any pending withdraw receipt. All lender principal and pending withdraw payouts are locked indefinitely.

### Likelihood Explanation
The attacker is any KYC-passing tranche holder: they call `requestWithdraw` while `unscaledApr == 0` (a normal, permitted action), creating an APR0 receipt. If the honest borrower/manager subsequently runs an epoch with a non-zero `unscaledApr` (e.g., APR is renegotiated per epoch — the vault supports both modes since `unscaledApr` is checked dynamically), the very next `stopEpoch` reverts and can never succeed again. The only precondition is an APR configuration change while an APR0 receipt is open; the attacker cannot influence APR directly, so likelihood is moderate but the attack itself costs only a withdraw request.

### Recommendation
Do not revert on the mixed state. Either (a) settle/clear `apr0TotalPrincipal` before the `unscaledApr` check (paying zero interest for the stale bucket if APR is now non-zero), or (b) track APR0 principal per-epoch and let `prepareStopEpochWithApr0` zero-out buckets from epochs whose APR no longer matches, so a config change cannot deadlock the epoch state machine.

### Proof of Concept
Foundry fork scenario:
1. Vault running with `unscaledApr == 0`; attacker (KYC'd tranche holder) calls `cdoEpoch.requestWithdraw(amount, tranche)` → `apr0TotalPrincipal > 0`.
2. Honest manager calls `startEpoch` / configures `unscaledApr = X > 0` for the next epoch (a legitimate operation).
3. Warp past `epochEndDate`; borrower funds interest + `pendingWithdraws`; manager calls `stopEpoch` → `prepareStopEpochWithApr0` reverts `NotAllowed`.
4. Retry `stopEpoch` with any parameters → still reverts; `apr0TotalPrincipal` can never be cleared; `claimWithdrawRequest` also reverts since `epochNumber` never advances. All funds permanently frozen.

Note: the exact mechanism by which `unscaledApr` transitions from 0 to non-zero (per-epoch parameter vs. persistent config) was not fully verifiable within the available context; if it is immutable post-initialization, the freeze requires a one-time config change by the honest manager, which still fits the threat model but lowers likelihood.