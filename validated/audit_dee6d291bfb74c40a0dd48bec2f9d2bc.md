### Title
Cross-epoch instant-withdraw receipt escapes default haircut and drains funded-claim liquidity - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
The kernel bug (CVE-2025-38542) leaks a reference when `atrtr_create()` overwrites an existing entry without releasing the old one. The closest analog is `requestInstantWithdraw()` in `IdleCreditVault.sol`: a user can stack instant-withdraw receipts across epochs, but default finalization only looks at `instantWithdrawClaimsByEpoch[epochNumber]` (the *current* epoch). An unfunded instant receipt recorded under an *older* epoch keeps its claim in `instantWithdrawsRequests[_user]` and `pendingInstantWithdraws`, yet is silently dropped from `defaultPendingClaimBasis()`. After `finalizeDefaultRecovery`, that stale-epoch receipt is paid **at par** through `_claimFundedWithdrawRequest`'s sibling `_transferFundedClaim`, spending non-reserve liquidity that belongs to other funded claimants.

### Finding Description
`requestInstantWithdraw` records the receipt under the *current* epoch only (`instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount`, line 371) while `pendingInstantWithdraws` (line 374) is a global aggregate. If `collectInstantWithdrawFunds` only partially funds the queue, the remainder stays pending into later epochs, still attributed to the old epoch.

At default finalization, `defaultPendingClaimBasis()` (lines 644-649) adds only `instantWithdrawClaimsByEpoch[epochNumber]` — the default-epoch entry — while `_defaultPrefundedInstantReserve()` (lines 716-723) treats `instantBasis - pendingInstant` as the prefunded part. An old-epoch unfunded instant receipt:

- is excluded from `totalBasis`, so `recoveryPrice` is computed over a shrunken denominator;
- is not cleared by `_claimDefaultedInstantWithdrawRequest` (line 844 only reads `[defaultEpoch]`);
- survives in `instantWithdrawsRequests[_user]` and is paid 1:1 at line 387-392 via `_transferFundedClaim`, which only protects `defaultRecoveryReserve` (lines 900-905), not other users' funded-claim liquidity.

The old reference is never "released" (decremented from the recovery basis or haircut); it is claimed at par as if fully funded. This mirrors the refcount leak: a new epoch attribution is stored while the stale aggregate claim remains unreleased.

### Impact Explanation
An unprivileged lender (KYC'd tranche holder) with an unfunded instant receipt from a pre-default epoch can, after `finalizeDefaultRecovery`, call `claimInstantWithdrawRequest` via the CDO and receive 100% of their receipt with zero haircut, while every other defaulted claimant only gets `defaultRecoveryPrice`. The payout comes from the strategy's non-reserve underlying balance — i.e., funds collected for *other* users' funded withdraw/instant claims — causing direct theft and/or permanent freezing of those claimants' receipts when the balance runs dry. Loss is bounded by the stale unfunded instant amount times `(1 - recoveryPrice)`; with a near-total default it approaches the full stale receipt size.

### Likelihood Explanation
Requires: (1) instant withdrawals enabled, (2) an epoch where `getInstantFunds`/`collectInstantWithdrawFunds` only partially covers `pendingInstantWithdraws` so an unfunded receipt crosses an epoch boundary — achievable whenever the CDO's available liquidity is smaller than the instant queue, and (3) a subsequent borrower default with `pendingInstantWithdraws != 0` (so `defaultInstantWithdrawsFinalized` is set but the old-epoch entry is ignored). All attacker steps are unprivileged user actions; default is triggered by honest manager/borrower sequencing. No existing guard catches it: the `requestWithdraw` stale-epoch guard (lines 263-271) has no counterpart on the instant path, and `_transferFundedClaim`'s reserve check only shields `defaultRecoveryReserve`.

### Recommendation
Track unfunded instant claims per epoch and roll them forward, or in `finalizeDefaultRecovery` compute the instant basis as `instantWithdrawClaimsByEpoch[epochNumber] + pendingInstantWithdraws`-consistent aggregate (e.g., sum of *all* outstanding instant receipts, not just the current epoch's), so every unfunded receipt — regardless of request epoch — enters `totalBasis` and is haircut. Correspondingly, `_claimDefaultedInstantWithdrawRequest` should clear all epochs' unfunded instant basis for the user, not only `defaultRecoveryEpoch`.

### Proof of Concept
Foundry fork sketch against the deployed vault stack:

1. Deposit into AA tranche as `user` (KYC'd) so `isWalletAllowed` passes; epoch is running.
2. `cdoEpoch.requestInstantWithdraw(X)` (instant path) → `instantWithdrawsRequestsByEpoch[user][N] = X`, `pendingInstantWithdraws = X`.
3. Manager calls `getInstantWithdrawFunds`/`collectInstantWithdrawFunds` with `amount < X` (only partial liquidity available) → `pendingInstantWithdraws = X - funded`, but the epoch attribution stays on `N`.
4. `stopEpoch` / `startEpoch` advance `epochNumber` to `N+1`. Do **not** claim.
5. Borrower defaults in epoch `N+1` (`_handleBorrowerDefault`/`finalizeDefault`); manager calls `finalizeDefaultRecovery(recovered, source)` with `recovered << basis`. `defaultPendingClaimBasis()` = `pendingWithdraws + instantWithdrawClaimsByEpoch[N+1]` — the `N`-epoch instant claim `X - funded` is missing from `totalBasis`; `defaultInstantWithdrawsFinalized = true`.
6. `user` calls `claimInstantWithdrawRequest()` on the CDO: `_claimDefaultedInstantWithdrawRequest` reads `[user][N+1] == 0` and returns; the funded path pays `instantWithdrawsRequests[user] = X` **at par** from non-reserve balance.
7. Assert: `user` received `X` underlying (0% haircut) while `defaultRecoveryPrice < RECOVERY_FULL`; subsequent `claimWithdrawRequest`/`claimInstantWithdrawRequest` by another funded claimant reverts in `_transferFundedClaim` (insufficient non-reserve balance) → their receipt is permanently frozen.

Expected assertion: `underlying.balanceOf(user) - pre == X` and `defaultRecoveryReserve` untouched, while second claimant's claim reverts with `NotAllowed`.