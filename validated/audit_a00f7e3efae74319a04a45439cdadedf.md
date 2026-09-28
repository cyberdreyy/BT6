### Title
Settled APR0 withdraw receipts escape `stopEpochWithDuration` loss haircuts because `_claimLossAdjustedWithdrawRequest` only inspects open `apr0Users.principal` - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
Use-after-free analog: a user's loss-adjusted receipt is "freed" (moved from `Apr0UserData.principal` to `settledPrincipal` by `_settleApr0`) while the epoch that owns its haircut (`lossRecoveryPriceByEpoch`) is only reachable via `apr0Users.principalEpoch`. Once the pointer is cleared, the stale haircut can never be applied and the receipt is paid at par through the funded-claim path, over-drawing the strategy's funded underlyings.

### Finding Description
`requestWithdraw` routes APR=0 requests into `_requestWithdrawApr0`, which records `apr0Users[_user].principal` and `principalEpoch` but never writes `withdrawsRequestsByEpoch` (IdleCreditVault.sol:285-294, 567-577).

When a borrower shortfall is realized via `collectWithdrawFunds`, the funded loss price is stored at `lossRecoveryPriceByEpoch[epochNumber]` and `pendingWithdraws` is zeroed (IdleCreditVault.sol:411-430).

On the next `claimWithdrawRequest`, `_claimLossAdjustedWithdrawRequest` derives `lossEpoch` from `lastWithdrawRequest[_user]` and calls `_withdrawClaimAmountsForEpoch`, which counts an APR0 receipt only if `apr0Users[_user].principal != 0 && principalEpoch == _claimEpoch` (IdleCreditVault.sol:789-801, 862-881).

However `_settleApr0` — executed inside `_claimFundedWithdrawRequest` and inside `_requestWithdrawApr0` — moves `principal` into `settledPrincipal`/`settledInterest` and zeroes `principal` and `principalEpoch` once `epochNumber` has advanced past the request epoch (IdleCreditVault.sol:545-565, 326-348).

Concretely:
1. In a buffer phase with `unscaledApr == 0`, attacker calls `cdoEpoch.requestWithdraw` → `apr0Users.principal = X`, `principalEpoch = N`, `lastWithdrawRequest = N`.
2. Honest manager calls `stopEpoch`; borrower under-funds, so `collectWithdrawFunds` sets `lossRecoveryPriceByEpoch[N] = p < 1e18` and funds only `p * pendingBasis` underlyings.
3. Next epoch runs; at its `stopEpoch`, `epochNumber` becomes `N+1`. Attacker's APR0 entry is now settleable (`_reqEpoch < epochNumber`).
4. Attacker calls `cdoEpoch.claimWithdrawRequest()` → `IdleCreditVault.claimWithdrawRequest`:
   - `_claimLossAdjustedWithdrawRequest`: `lossEpoch = N`, `lossRecoveryPrice = p`, but `_withdrawClaimAmountsForEpoch` returns `claimBasis = 0` because `principal` is checked before `_settleApr0` runs — actually `principal` is still open at this point, so basis is found only if `principalEpoch == N`, which holds. The haircut *is* applied if the user claims before settlement. The escape happens on the second ordering:
   - If the user first triggers `_settleApr0` via a second `requestWithdraw` (guard at lines 263-271 checks `apr0Users.principal != 0 && principalEpoch == lossEpoch`; a *settled* APR0 receipt has `principal == 0` and `withdrawsRequestsByEpoch[N] == 0`, so the guard passes even though `lossRecoveryPriceByEpoch[N] != 0`), then `principal`/`principalEpoch` are zeroed and `lastWithdrawRequest` is overwritten with the new epoch `M`.
   - On the later claim, `_claimLossAdjustedWithdrawRequest` reads `lossEpoch = M`, finds `lossRecoveryPriceByEpoch[M] == 0`, and returns 0. The epoch-N loss receipt is now orphaned — no storage slot references it.
   - `_claimFundedWithdrawRequest` then pays `settledPrincipal + settledInterest` at par (line 339-349).

The same bypass applies even without a new request if settlement happens first (e.g., the epoch-N receipt settles when `epochNumber` passes N before the user claims): `_withdrawClaimAmountsForEpoch` no longer sees `principal`, `claimBasis = 0`, `lastWithdrawRequest` is left stale, and the funded path pays the settled amounts at par.

### Impact Explanation
Direct theft / insolvency. The strategy was funded with only `pendingBasis * lossRecoveryPrice` underlyings for epoch-N receipts, but the attacker's settled APR0 receipt is paid at 100 cents on the dollar. The excess comes out of the strategy's underlying balance, which is the backing for other funded receipts and, post-default, the recovery reserve guard in `_transferFundedClaim`. Quantified loss: `X * (1 - lossRecoveryPrice)` per receipt, which for a near-total loss (`p → 0`) approaches the full receipt principal. Broken invariant: loss waterfall — pending receipts must share the realized stop-epoch loss pro rata.

### Likelihood Explanation
Requires `unscaledApr == 0` (APR0 mode, a supported vault configuration), a realized `stopEpochWithDuration`-style partial-loss funding of pending receipts (borrower under-payment is an honest-manager/borrower sequence), and the attacker merely delaying a claim or making a second request — all unprivileged lender actions. No privileged misbehavior needed.

### Recommendation
Persist the loss-adjusted basis across settlement: when `_settleApr0` moves principal for an epoch with `lossRecoveryPriceByEpoch[_reqEpoch] != 0`, either apply the haircut into `settledPrincipal`/`settledInterest` at settlement time, or record the settled amounts per-epoch so `_claimLossAdjustedWithdrawRequest` can still find them. Also extend the `requestWithdraw` guard (lines 263-271) to block new requests while the user holds any settled receipt originating from a loss epoch, and make `_claimFundedWithdrawRequest` apply `lossRecoveryPriceByEpoch[principalEpoch]` to settled APR0 amounts rather than paying them at par unconditionally.

### Proof of Concept
Foundry fork PoC outline (APR0 mode):
```solidity
// setup: cdoEpoch with unscaledApr == 0, user1 KYC'd lender
idleCDO.depositAA(100_000e6);            // user1 deposits
cdoEpoch.startEpoch();                   // epoch 1 runs
cdoEpoch.stopEpoch(0, interest);         // stop, buffer of epoch 2 begins (epochNumber = 1)

cdoEpoch.requestWithdraw(amount, AA);    // buffer-phase APR0 request, principalEpoch = 1

// stopEpoch of epoch 1's pending receipts: borrower under-funds -> loss
deal(underlying, borrower, fundedAmount); // fundedAmount < pendingWithdraws
cdoEpoch.startEpoch();                   // or stopEpochWithDuration path that calls collectWithdrawFunds
// -> lossRecoveryPriceByEpoch[1] = p < 1e18, strategy holds only p * basis

// wait one more epoch so _settleApr0 can fire
cdoEpoch.stopEpoch(0, interest2);        // epochNumber advances past 1
cdoEpoch.startEpoch();

// attacker re-requests (guard passes: apr0Users.principal == 0 case after settle)
// then claim
cdoEpoch.claimWithdrawRequest();
// expected per code: paid settledPrincipal + settledInterest at PAR
// correct payout: (settledPrincipal + settledInterest) * p / 1e18
assertApproxEqAbs(underlying.balanceOf(user1), parAmount); // bug: exceeds funded p * basis
```
The assertion demonstrating the bug is that the claim returns the full par amount while the strategy was only funded `p * basis` for that epoch, leaving later claimants (or the recovery reserve) short by `(1 - p) * claimBasis`.