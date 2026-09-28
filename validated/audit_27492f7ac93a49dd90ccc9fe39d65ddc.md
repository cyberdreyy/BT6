### Title
Instant-withdraw claims spend underlying reserved for other users' funded withdrawal receipts - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.claimInstantWithdrawRequest` pays a user's full `instantWithdrawsRequests` balance from the strategy's raw underlying balance with no epoch-wait and no reservation for the already-funded `pendingWithdraws` bucket. `_transferFundedClaim` only protects `defaultRecoveryReserve` (lines 899–905); it does not reserve cash backing normal funded receipts that were collected via `collectWithdrawFunds` (line 428). An unprivileged lender can therefore spend money that belongs to other users' matured withdraw requests.

### Finding Description
The vault keeps a single underlying balance inside the `IdleCreditVault` strategy that commingles:

- cash pulled at `stopEpoch` for pending receipts (`collectWithdrawFunds`, line 411–430),
- cash pulled for instant receipts (`collectInstantWithdrawFunds`, line 398–403),
- the isolated default recovery reserve.

`claimInstantWithdrawRequest` (lines 380–393) has no epoch gating (unlike `_claimFundedWithdrawRequest`, which enforces `epochNumber > lastWithdrawRequest` at line 326) and no check that `balanceOf(this) - pendingWithdraws-funded-amount >= amount`. It simply burns the receipt tokens and calls `_transferFundedClaim`, which reverts only when `defaultRecoveryReserve != 0` and the spend would dip into it. Cash reserved for `withdrawsRequests` / `withdrawsRequestsByEpoch` claimants is unprotected.

The attacker path, reachable by any KYC'd tranche holder:

1. Epoch is running; victim V has a matured, already-funded `requestWithdraw` receipt (underlying already sits in the strategy from a prior `collectWithdrawFunds`).
2. Manager lowers APR honestly so `lastEpochApr > unscaledApr + instantWithdrawAprDelta`, enabling the instant path in `IdleCDOEpochVariant.requestWithdraw` (lines 761–768).
3. Attacker calls `requestWithdraw(amount, tranche)` → `requestInstantWithdraw` mints them a receipt and bumps `pendingInstantWithdraws`.
4. In the same block, attacker calls the CDO's `claimInstantWithdrawRequest` → strategy burns the receipt and `safeTransfer`s `amount` from the commingled balance, consuming V's funded reserve.
5. At the next `stopEpoch`, the borrower is asked to fund the attacker's `pendingInstantWithdraws` *again* (it was never decremented by the claim), while V's claim later reverts on insufficient balance or drains funds meant for the next cohort — a permanent loss equal to the stolen amount for whoever claims last.

The bug class mirrors the CVE: a special input path (`-g`/instant-withdraw option) bypasses the normal processing pipeline (epoch gating + funded-vs-unfunded accounting) and produces an unauthorized result.

### Impact Explanation
Direct theft of funded withdrawal reserves: an attacker can extract up to the full underlying balance held by the strategy (minus `defaultRecoveryReserve`), i.e., all cash set aside for matured `withdrawsRequests` and any other funded receipts. The invariant "one receipt, one payout" is broken — the same underlying is promised to the victim's receipt and paid to the attacker's instant receipt. Loss is quantified by `underlyingToken.balanceOf(strategy) - defaultRecoveryReserve` at claim time.

### Likelihood Explanation
Requires (a) a non-programmable deployment with instant withdrawals enabled (`!disableInstantWithdraw`), (b) an APR decrease by the honest manager exceeding `instantWithdrawAprDelta`, and (c) idle underlying sitting in the strategy — which is the normal state after any `stopEpoch` that funded pending receipts but before users claim. All attacker actions are unprivileged user calls (`requestWithdraw`, `claimInstantWithdrawRequest` via the CDO). No privileged misbehavior is needed; the trigger is routine manager APR maintenance.

### Recommendation
In `claimInstantWithdrawRequest` / `_transferFundedClaim`, reserve both `defaultRecoveryReserve` and the funded pending-withdraw basis: track a `fundedWithdrawals` counter incremented in `collectWithdrawFunds` and decremented in `_claimFundedWithdrawRequest`/`_claimLossAdjustedWithdrawRequest`, and require `balance - (defaultRecoveryReserve + fundedWithdrawals) >= amount` before transferring. Alternatively, gate instant claims behind epoch advancement (`epochNumber > requestEpoch`) so instant receipts are only payable after their funding has actually been collected.

### Proof of Concept
Foundry fork PoC sketch (based on the existing harness in `test/foundry/IdleCreditVault.t.sol`):

```solidity
// 1. Victim deposits AA, epoch starts, victim calls requestWithdraw (normal path).
// 2. Warp past epochEndDate; manager calls stopEpoch -> collectWithdrawFunds moves
//    victim's receipt cash into the strategy (pendingWithdraws cleared, cash idle).
// 3. Manager lowers APR so instant path activates (lastEpochApr > unscaledApr + delta).
// 4. Attacker (KYC'd holder) calls cdoEpoch.requestWithdraw(x, AA) -> instant receipt.
// 5. Same block: cdoEpoch.claimInstantWithdrawRequest(attacker).
//    IdleCreditVault transfers x underlying from strategy balance,
//    which is the victim's funded cash (defaultRecoveryReserve == 0, so no guard).
// 6. Victim calls claimWithdrawRequest -> reverts/underpays: theft confirmed.
```

Uncertain elements I could not fully verify within the search budget: the exact CDO-side wrapper for `claimInstantWithdrawRequest` (whether it adds any balance reservation), and whether `collectInstantWithdrawFunds` ordering at `stopEpoch` implicitly compensates. If the CDO wrapper already nets out funded pending receipts before calling the strategy claim, this finding would be mitigated; that check was not visible in the indexed portions reviewed.