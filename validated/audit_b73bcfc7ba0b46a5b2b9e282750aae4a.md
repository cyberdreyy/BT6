### Title
Instant-withdraw receipts are paid from shared vault reserves without verifying the CDO actually funded them - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.claimInstantWithdrawRequest` honors a receipt (burns `instantWithdrawsRequests[_user]` strategy tokens and transfers underlying) based solely on the receipt existing — the same "authentic but unproven backing" failure as the Verus bridge. The function never checks that `collectInstantWithdrawFunds` actually pulled the corresponding underlying from the CDO, or that `pendingInstantWithdraws` covering the claim was funded. `_transferFundedClaim` only guards the default-recovery reserve; it will happily pay out of any underlying sitting in the strategy, which is the backing for active LP deposits and other pending claims.

### Finding Description
At `requestInstantWithdraw`, the vault burns CDO-held principal tokens and mints the user a 1:1 receipt, incrementing `instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch`, `instantWithdrawClaimsByEpoch`, and `pendingInstantWithdraws` (lines 356–375). Funding is a separate step: `collectInstantWithdrawFunds` decrements `pendingInstantWithdraws` and pulls tokens from the CDO (lines 398–403). The claim path (lines 380–393) does not tie the payout to either of these:

```solidity
uint256 amount = instantWithdrawsRequests[_user];
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);
```

`_transferFundedClaim` (lines 897–907) checks only `balance - defaultRecoveryReserve >= _amount`. There is no per-receipt or per-epoch funding marker analogous to `lossRecoveryPriceByEpoch` or `withdrawsRequestsByEpoch` gating on the normal path. A receipt created for an instant request that was never funded is indistinguishable from a funded one — the vault verifies the receipt is real, never that it is backed. Funding verification is delegated entirely to the caller-side sequencing inside `IdleCDOEpochVariant` (collect-then-claim ordering), which is exactly the trust-boundary assumption the Verus exploit abused: a valid receipt inside a validly-authenticated flow, paying out value that was never locked.

The same pattern exists in `_claimFundedWithdrawRequest` (lines 319–350): once `epochNumber > lastWithdrawRequest[_user]` (or `epochEndDate() == 0` for a closed pool), the full `withdrawsRequests[_user]` plus APR0 buckets are paid at par from the strategy balance, with no check that `collectWithdrawFunds` funded that basis. `collectWithdrawFunds` with `_amount < pendingBasis` records `lossRecoveryPriceByEpoch[epochNumber]`, but only claims routed through `_claimLossAdjustedWithdrawRequest` consult it — and only when `lastWithdrawRequest` still points at the loss epoch. Any path where a receipt's epoch marker is cleared or bypassed (e.g., the pool-close early return in `requestWithdraw` lines 259–280, which skips `pendingWithdraws += _amount` entirely while still minting the receipt) produces an unfunded receipt that the funded-claim path will pay at par out of shared reserves.

### Impact Explanation
If any CDO-side flow (pool close, default finalization ordering, instant-withdraw liquidity shortfall, or an epoch transition edge) reaches `claimInstantWithdrawRequest`/`claimWithdrawRequest` for a receipt whose funds were never collected, the vault pays out of underlying that belongs to active tranche holders or other claimants — direct theft/insolvency up to the full strategy underlying balance, not just the receipt amount. Note: I was unable to fully trace the CDO-side gating in `IdleCDOEpochVariant.sol` (e.g., whether `claimInstantWithdrawRequest` is reachable only after `collectInstantWithdrawFunds`), so exploitability hinges on a caller-path that reaches the claim without funding — which must be confirmed with the Foundry PoC.

### Likelihood Explanation
The vault-level invariant "one receipt, one funded payout" is not enforced in the vault itself; it is enforced only implicitly by call ordering in the CDO. The codebase already contains multiple receipt states (funded, loss-adjusted via `lossRecoveryPriceByEpoch`, defaulted via `defaultRecoveryPrice`, post-default via `postDefaultRequests`, instant via `instantWithdrawsRequestsByEpoch`) and several paths that mint receipts without incrementing the funding counters (`isClosed` early-return in `requestWithdraw`, closed-pool immediate claims). Each such path is a candidate for creating an authentic-but-unbacked receipt. The closed-pool branch is the most suspicious: a `requestWithdraw` when `epochEndDate() == 0` mints a full receipt and records it under `withdrawsRequests`/`lastWithdrawRequest` while never adding to `pendingWithdraws`, so nothing ever obligates funding for it — yet `_claimFundedWithdrawRequest` will pay it at par from vault reserves.

### Recommendation
Enforce economic backing at the vault, not just receipt authenticity:
- Track funded-but-unclaimed amounts explicitly (e.g., `fundedWithdrawBasis`) incremented in `collectWithdrawFunds`/`collectInstantWithdrawFunds` and decremented on claim; revert in `_transferFundedClaim` if the claim exceeds the funded balance attributable to receipts.
- In `claimInstantWithdrawRequest`, require the request's epoch basis to have been collected (e.g., check `instantWithdrawClaimsByEpoch` coverage or a per-epoch funded flag) before paying.
- In the `isClosed` branch of `requestWithdraw`, either skip minting a receipt that can never be funded, or pay it atomically from already-recalled funds rather than recording it in `withdrawsRequests`.

### Proof of Concept
Foundry fork PoC sketch:
1. Deploy/attach to an `IdleCDOEpochVariantPrefunded` + `IdleCreditVault` instance; KYC'd user deposits and receives AA tranche tokens.
2. Drive the pool to a successful close (`stopEpoch(0, 1)` sentinel / healthy close) so `epochEndDate() == 0` and all borrower funds are recalled to the CDO.
3. As the user, call `cdoEpoch.requestWithdraw(...)` — the strategy mints the user a receipt, sets `lastWithdrawRequest`/`withdrawsRequests`, but skips `pendingWithdraws` because `isClosed` is true (lines 259–294). No `collectWithdrawFunds` will ever fund this receipt.
4. Call `cdoEpoch.claimWithdrawRequest()` — `epochEndDate() == 0` bypasses the epoch-wait check (line 326), and `_transferFundedClaim` pays the full basis at par out of underlying held by the strategy, draining reserves belonging to other receipt holders / the recovery reserve boundary.
5. Assert `underlying.balanceOf(attacker)` increased by the claim basis while the corresponding funds were never collected from the CDO, and that remaining claimants' balances are now underfunded.

If step 3–4 revert due to CDO-side sequencing I could not inspect, the fallback test targets `claimInstantWithdrawRequest`: request an instant withdrawal, ensure the CDO collects only a partial amount (liquidity shortfall so `pendingInstantWithdraws > 0`), then verify whether a claim still pays the full `instantWithdrawsRequests` from vault reserves.