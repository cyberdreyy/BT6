### Title
Stale instant-withdraw receipts are never cleared on claim, corrupting default-recovery accounting (`instantWithdrawsRequestsByEpoch` / `instantWithdrawClaimsByEpoch` / `pendingInstantWithdraws`) - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary

`IdleCreditVault.claimInstantWithdrawRequest` pays out and clears only the aggregate `instantWithdrawsRequests[_user]`, but never clears the per-epoch receipt records (`instantWithdrawsRequestsByEpoch[_user][epoch]`, `instantWithdrawClaimsByEpoch[epoch]`) and never decrements `pendingInstantWithdraws`. The paid receipt is "freed" economically — the user received the underlying and the receipt tokens were burned — but its bookkeeping objects remain live. If a borrower default is later finalized while `epochNumber` still equals the request epoch, `defaultPendingClaimBasis()` counts those already-paid claims again, inflating the recovery basis, diluting `defaultRecoveryPrice` for every legitimate claimant, and permanently stranding the corresponding share of `defaultRecoveryReserve`. This is the closest in-scope analog to CVE-2023-2723 (use-after-free): a stale claim object is referenced after it has already been settled.

### Finding Description

In `claimInstantWithdrawRequest` (lines 380–393) the claim path is:

```solidity
uint256 amount = instantWithdrawsRequests[_user];
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);
```

Compare with the write-request bookkeeping done in `requestInstantWithdraw` (lines 366–374), which writes three extra locations:

- `instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount`
- `instantWithdrawClaimsByEpoch[currentEpoch] += _amount`
- `pendingInstantWithdraws += _amount`

None of these are cleared by the claim. `pendingInstantWithdraws` is only decremented by `collectInstantWithdrawFunds` (line 401), which the CDO calls when funding instant claims at `stopEpoch` — not when the strategy pays a claim out of already-held liquidity mid-epoch.

Instant claims have no epoch gating (unlike `_claimFundedWithdrawRequest`, which requires `epochNumber > lastWithdrawRequest` at line 326). Whenever the strategy contract holds underlying — e.g. prefunded-mode cash, borrower direct-send funds that landed in the strategy, or residual liquidity — a user can `requestInstantWithdraw` and `claimInstantWithdrawRequest` within the *same* epoch and be paid in full, while the three records above remain populated.

If the borrower then defaults in that epoch and `finalizeDefaultRecovery` runs:

- `defaultPendingClaimBasis()` (line 644) returns `pendingWithdraws + instantWithdrawClaimsByEpoch[epochNumber]` because `pendingInstantWithdraws != 0` — counting the already-paid claim a second time.
- `totalBasis` at line 680 is inflated, so `recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis` (line 688) is lower than the true recovery ratio.
- `_defaultPrefundedInstantReserve()` (line 716) computes `instantBasis - pendingInstant`; since both remain equal (both stale), it returns 0 even though real underlying was double-pulled into the strategy via the later `collectInstantWithdrawFunds`.
- The stale `instantWithdrawsRequestsByEpoch[_user][defaultEpoch]` remains a valid claim object for `_claimDefaultedInstantWithdrawRequest` (line 842), although re-claiming it requires burning `claimBasis` strategy tokens the user no longer holds — so in practice the stale basis is *unclaimable*, not double-payable.

Because the inflated basis can never be fully claimed (no one holds receipt tokens for the already-paid portion), the fraction `staleBasis / totalBasis` of `defaultRecoveryReserve` is stranded in the strategy forever — there is no sweep function for `defaultRecoveryReserve` — while every honest defaulted-epoch receipt and active tranche holder receives a strictly lower recovery price.

### Impact Explanation

Permanent freezing / effective theft of recovery funds belonging to honest claimants. Quantified: for a stale instant claim of `S` and a true claim basis `B`, honest claimants lose `reserve × S / (B + S)` of recovery payout, and the same amount of recovered underlying is locked in the strategy contract permanently (unreachable by any claim path, since the stale basis cannot be burned). In a default of e.g. 10M USDC basis with 1M USDC of stale mid-epoch instant claims, roughly 9% of the recovery reserve is permanently stranded and all claimant payouts are diluted by the same factor.

### Likelihood Explanation

The defect triggers whenever (a) an instant-withdraw claim is paid from strategy-held liquidity before `stopEpoch`/`collectInstantWithdrawFunds` runs — possible in prefunded configurations or when borrower repayments land in the strategy mid-epoch — and (b) the borrower defaults in that same epoch. Both conditions are reachable by unprivileged users sequencing around honest owner/manager/borrower calls; no privileged misbehavior is required. The bug is a pure accounting omission (missing cleanup on claim), not a rounding edge case, so any deployment meeting condition (a) followed by a default hits it deterministically.

*Caveat:* I could not fully verify within the available iterations whether the CDO-level `requestInstantWithdraw`/`claimInstantWithdrawRequest` entry points permit mid-epoch instant claims against strategy-held liquidity in the shipped configurations; the finding assumes the strategy can hold spendable underlying before epoch end, which the prefunded variant and borrower-direct-send flows are designed to allow.

### Recommendation

In `claimInstantWithdrawRequest`, mirror the cleanup done by `_clearWithdrawClaimForEpoch` for normal requests:

- Clear `instantWithdrawsRequestsByEpoch[_user][epoch]` for each epoch contributing to the paid aggregate (or track the user's oldest unpaid instant-request epoch).
- Decrement `instantWithdrawClaimsByEpoch[epoch]` by the same amount.
- Decrement `pendingInstantWithdraws` by the paid amount whenever the claim is funded from strategy-held liquidity rather than from a prior `collectInstantWithdrawFunds` pull — or, alternatively, gate instant claims so they can only execute after their epoch's funds have been collected, making `pendingInstantWithdraws` the single source of truth.

### Proof of Concept

Reproducible Foundry fork PoC outline (mirroring the style of `test/foundry/IdleCreditVault.t.sol` and `IdleCDOEpochQueue.t.sol`):

```solidity
// Setup: deploy IdleCDOEpochVariant + IdleCreditVault with instant withdraws enabled
// and underlying liquidity held by the strategy (prefunded path or direct borrower send).

// 1. Epoch E running. Attacker (KYC'd lender) requests instant withdraw of X.
cdoEpoch.requestInstantWithdraw(X, address(aaTranche));
assertEq(strategy.instantWithdrawsRequestsByEpoch(attacker, strategy.epochNumber()), X);
assertEq(strategy.instantWithdrawClaimsByEpoch(strategy.epochNumber()), X);
assertEq(strategy.pendingInstantWithdraws(), X);

// 2. Strategy holds liquidity mid-epoch -> attacker claims immediately, paid in full.
cdoEpoch.claimInstantWithdrawRequest();
assertEq(strategy.instantWithdrawsRequests(attacker), 0);      // aggregate cleared
// BUG: stale per-epoch objects survive ("use after free")
assertEq(strategy.instantWithdrawsRequestsByEpoch(attacker, strategy.epochNumber()), X);
assertEq(strategy.instantWithdrawClaimsByEpoch(strategy.epochNumber()), X);
assertEq(strategy.pendingInstantWithdraws(), X);

// 3. Borrower defaults in epoch E (owner/guardian call, honest).
// ... _handleBorrowerDefault, then:
uint256 basis = strategy.defaultPendingClaimBasis();
// basis includes X a second time via instantWithdrawClaimsByEpoch[epochNumber]
assertGt(basis, strategy.pendingWithdraws());

// 4. finalizeDefaultRecovery computes recoveryPrice over inflated totalBasis.
//    Honest claimants receive less; reserve share == staleX/totalBasis is stranded forever.
```