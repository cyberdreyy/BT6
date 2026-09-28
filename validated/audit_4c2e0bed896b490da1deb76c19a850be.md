### Title
Claimed instant-withdraw receipts are still counted in `defaultPendingClaimBasis`/`_defaultPrefundedInstantReserve`, inflating `defaultRecoveryPrice` and draining the recovery reserve - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`claimInstantWithdrawRequest` pays a user's instant-withdraw receipt and zeroes `instantWithdrawsRequests[_user]`, but it never decrements the per-epoch aggregate `instantWithdrawClaimsByEpoch[currentEpoch]` used by default finalization. The stale aggregate is the analog of the kernel bug's wrong "valid entry length": finalization reads a larger claim basis than is actually outstanding, and `_defaultPrefundedInstantReserve` then credits already-paid-out funds as strategy-held reserve — the equivalent of copying `payload_len` bytes into a smaller buffer. The result is a `defaultRecoveryPrice` and `defaultRecoveryReserve` computed against money that has already left the strategy, so earlier claimants are overpaid and later claimants' claims revert or are permanently unpaid.

### Finding Description
`requestInstantWithdraw` increments three counters per request: `instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch[_user][epoch]`, and the epoch aggregate `instantWithdrawClaimsByEpoch[epoch]`, plus `pendingInstantWithdraws` (lines 366-374). `pendingInstantWithdraws` is reduced when the CDO pulls cash via `collectInstantWithdrawFunds` (line 401), and the funded claim path `claimInstantWithdrawRequest` burns the user receipt and transfers underlying via `_transferFundedClaim` — but it clears neither `instantWithdrawsRequestsByEpoch` nor `instantWithdrawClaimsByEpoch` (lines 387-392).

At default finalization (`finalizeDefaultRecovery`):

- `defaultPendingClaimBasis()` adds `instantWithdrawClaimsByEpoch[epochNumber]` whenever `pendingInstantWithdraws != 0` (lines 644-649). This aggregate still contains receipts that were already funded *and already claimed* — counted once too many, like the `num_devices - 1` / wrong entry-length miscalculation.
- `_defaultPrefundedInstantReserve()` computes `instantBasis - pendingInstant` (lines 716-723) and treats that difference as underlying still held by the strategy. But the claimed portion was transferred out in `claimInstantWithdrawRequest`, so it is not in `balanceOf(address(this))`.

`reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve` is therefore inflated by the already-paid amount, and `recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis` is computed against a denominator that also includes the ghost basis (lines 679-692). Claims then pay `claimBasis * defaultRecoveryPrice / RECOVERY_FULL` from `_transferDefaultRecovery`, which decrements `defaultRecoveryReserve` (lines 842-855, 897+).

### Impact Explanation
Broken invariant: recovery reserve solvency — "one receipt, one payout, backed by actual held funds." A user holding an instant-withdraw receipt in the defaulted epoch can call `claimInstantWithdrawRequest` (via the CDO) and be paid `claimBasis * inflatedPrice`, extracting underlying beyond their fair pro-rata share. Once the real balance is exhausted, `defaultRecoveryReserve` underflows or the ERC20 transfer reverts for every later claimant (normal withdraw receipts via `_claimDefaultedWithdrawRequest`, other instant receipts via `_claimDefaultedInstantWithdrawRequest`, and `DefaultDistributor` claims), permanently freezing their unclaimed recovery. Loss is quantifiable: up to the full amount of the double-counted, already-claimed instant receipts.

### Likelihood Explanation
Requires a specific but unprivileged sequencing:

1. Epoch running, instant withdrawals enabled; attacker and a victim each call `requestInstantWithdraw` in the same epoch (attacker = instant receipt holder, an allowed unprivileged role).
2. The CDO collects only part of the instant queue via `collectInstantWithdrawFunds` (e.g., only enough for the attacker's receipt) — an honest manager/borrower action leaving `pendingInstantWithdraws == victimAmount`.
3. Attacker claims early via `claimInstantWithdrawRequest`, removing real underlying while `instantWithdrawClaimsByEpoch[epoch]` keeps the full A+B.
4. Borrower defaults (repays nothing) and `finalizeDefault`/`finalizeDefaultRecovery` runs with `pendingInstantWithdraws != 0`, triggering the inflated basis + phantom reserve.

No privileged misbehavior is needed; the attacker only needs to hold an instant receipt and claim before finalization. Existing guards don't stop it: `defaultRecoveryFinalized` gating, `_onlyIdleCDO`, and the `claimBasis >= pending ? 0 : pending - claimBasis` clamp in `_claimDefaultedInstantWithdrawRequest` all operate on the already-corrupted aggregates.

### Recommendation
In `claimInstantWithdrawRequest` (and any funded-claim path), decrement `instantWithdrawsRequestsByEpoch[_user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]` for the claimed amount, so `defaultPendingClaimBasis` and `_defaultPrefundedInstantReserve` only see genuinely outstanding receipts. Alternatively, subtract already-claimed amounts when computing `instantBasis`/`prefundedReserve` in `_defaultPrefundedInstantReserve` — i.e., compute the "valid entry length" from live receipts, not the lifetime aggregate.

### Proof of Concept
Reproducible Foundry fork PoC sketch (mirroring `IdleCDOEpochQueue.t.sol` helpers `_stopCurrentEpochWithApr`, `_depositWithUser`, `_requestWithdrawWithUser`):

```solidity
function testClaimedInstantReceiptInflatesRecovery() external {
    // Epoch running with instant withdrawals enabled (as in testClaimInstantWithdrawRequest).
    // attacker and victim each requestInstantWithdraw(X) via CDO in epoch N.
    // Manager/CDO calls collectInstantWithdrawFunds(X) once — only attacker's share funded.
    // attacker -> cdo.claimInstantWithdrawRequest(): underlying X leaves strategy,
    //   instantWithdrawClaimsByEpoch[N] still == 2*X, pendingInstantWithdraws == X.
    // Borrower defaults; owner calls finalizeDefault with a partial recovery R.
    // Assertions:
    //   strategy.defaultPendingClaimBasis() == pendingWithdraws + 2*X   (should be X)
    //   defaultRecoveryReserve > actual underlying held by strategy (includes phantom X)
    //   attacker claims at inflated defaultRecoveryPrice; victim's later
    //   claimInstantWithdrawRequest/claimWithdrawRequest reverts or pays ~0,
    //   though defaultRecoveryReserve accounting promised a pro-rata share.
}
```

Note on confidence: the missing decrement of `instantWithdrawsRequestsByEpoch`/`instantWithdrawClaimsByEpoch` in `claimInstantWithdrawRequest` (lines 380-393) versus their consumption in `defaultPendingClaimBasis`/`_defaultPrefundedInstantReserve` (lines 644-649, 716-723) is verified directly in the code; the exact CDO-side sequence for partial instant funding followed by default should be confirmed by executing the PoC, since `claimInstantWithdrawRequest` is reachable only through the CDO's instant-claim path.