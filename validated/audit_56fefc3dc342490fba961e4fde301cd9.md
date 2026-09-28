### Title
Loss-haircutted withdraw receipt is repaid at par after a later request overwrites `lastWithdrawRequest` - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
The CVE's bug class is paired state that must stay consistent but is set under one condition and consumed under another (`is_root` vs `NI_FLAG_DIR` → allocated resource is never freed). The analog in `IdleCreditVault` is `lastWithdrawRequest[_user]` vs `withdrawsRequestsByEpoch[_user][epoch]`. A withdraw receipt haircutted by `stopEpochWithDuration(_lossAmount)` is tracked per-epoch via `lossRecoveryPriceByEpoch`, but the loss-adjusted claim path is only reached when `lastWithdrawRequest[_user]` still equals the loss epoch. Any later withdraw request overwrites `lastWithdrawRequest`, so the loss-adjusted path is skipped and the haircutted receipt falls through to `_claimFundedWithdrawRequest`, which pays the full aggregate `withdrawsRequests[_user]` at par.

### Finding Description
`requestWithdraw` unconditionally sets `lastWithdrawRequest[_user] = currentEpoch` (line 282) and accumulates `withdrawsRequestsByEpoch[_user][currentEpoch]` / `withdrawsRequests[_user]` (lines 288-294). A user may hold multiple requests across epochs — the code comments explicitly document re-requesting before claiming (`IdleCreditVault.sol:323-324`).

On a lossy stop, `collectWithdrawFunds` records `lossRecoveryPriceByEpoch[epochNumber] = _amount * RECOVERY_FULL / pendingBasis`, zeroes `pendingWithdraws`, and pulls only the haircutted amount from the CDO (lines 411-430). Crucially, per-user `withdrawsRequestsByEpoch` entries are **not** cleared at this point.

At claim time, `claimWithdrawRequest` calls `_claimLossAdjustedWithdrawRequest`, which reads `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` (lines 789-795). If the user made a second (even dust-sized) request in a later epoch, `lastWithdrawRequest` points at that epoch, `lossRecoveryPriceByEpoch` is 0 there, and the function returns 0 — the loss-epoch receipt is never cleared via `_clearWithdrawClaimForEpoch`. `_claimFundedWithdrawRequest` then passes its gate once `epochNumber > lastWithdrawRequest[_user]` (line 326) and pays `withdrawsRequests[_user]` — which still includes the full un-haircutted loss-epoch basis — at par via `_transferFundedClaim` (lines 338-349).

The invariant broken is one-receipt-one-haircutted-payout: only `claimBasis * lossRecoveryPrice` was ever funded by the borrower for that receipt, but the user extracts the full `claimBasis`. The delta is paid from underlyings backing other claimants (funded claims, and — once `defaultRecoveryReserve != 0` — up to the point where `_transferFundedClaim`'s reserve guard reverts, freezing subsequent claims).

### Impact Explanation
Direct theft plus insolvency. An attacker who requested `X` in the loss epoch and is owed `X * lossRecoveryPrice / 1e18` instead receives `X` (plus the later dust request). The stolen amount `X * (1 - lossRecoveryPrice)` is drawn from the strategy's funded underlying pool, leaving other users' funded or recovery claims undercollateralized or permanently unpayable (claims revert in `_transferFundedClaim` at lines 900-905 once the balance is exhausted). Loss scales with the attacker's share of the haircutted epoch's pending basis.

### Likelihood Explanation
Requires: (a) a `stopEpochWithDuration` with `_lossAmount > 0` creating a `lossRecoveryPriceByEpoch` entry — a manager action within the honest-actor model, i.e. a normal loss-realization flow; (b) the attacker holds a receipt in that epoch (any tranche-token holder can `requestWithdraw`); (c) the attacker submits a second dust request in a following epoch and waits one more epoch. No privileged collusion, oracle manipulation, or timing edge is needed — re-requesting is documented, intended behavior, so the trigger is trivially available to any unprivileged user whenever a loss stop occurs.

### Recommendation
Do not key the loss-adjusted claim solely off `lastWithdrawRequest`. Iterate (or track) all of the user's request epochs that have a nonzero `lossRecoveryPriceByEpoch`, clearing each via `_clearWithdrawClaimForEpoch` before falling through to the funded path; alternatively, record a per-user/per-epoch flag when a receipt is haircutted so `_claimFundedWithdrawRequest` cannot pay it at par.

### Proof of Concept
Foundry fork PoC outline (mirroring `test/foundry/IdleCreditVault.t.sol` setup):

```solidity
// Setup: depositAA for attacker (large) and victim; startEpoch.
// Epoch N:
cdoEpoch.requestWithdraw(0, address(AAtranche));            // attacker, large X
// borrower realizes loss: manager calls
cdoEpoch.stopEpochWithDuration(apr, 0, duration, lossAmt);  // sets lossRecoveryPriceByEpoch[N] < 1e18
// Buffer: attacker requests again (dust) -> lastWithdrawRequest[attacker] = N+1
cdoEpoch.requestWithdraw(0, address(AAtranche));
// fund borrower, warp, stopEpoch -> epochNumber = N+2, new request funded at par
// Attack:
cdoEpoch.claimWithdrawRequest();
// assert attacker received X + dust (par) instead of X*lossRecoveryPrice + dust,
// and/or victim's later claimWithdrawRequest() reverts in _transferFundedClaim.
```

Uncertainty: I did not fully read the top of `requestWithdraw` (lines ~240-270) to confirm no per-user single-request guard exists, but the in-code comment at lines 323-324 explicitly documents that a user may "request another withdraw" before claiming, supporting reachability.