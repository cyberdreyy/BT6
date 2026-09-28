### Title
Unfunded instant-withdraw claims pay out underlyings reserved for funded normal withdraw receipts - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The external bug is an uninitialized read in `dev_read`: the program returns attacker-visible data that was never written for that object. The closest analog in `IdleCreditVault` is `claimInstantWithdrawRequest`, which pays out the raw strategy token balance against an instant receipt without ever checking that the borrower's funding for that instant queue actually arrived via `collectInstantWithdrawFunds`. The function "reads" (spends) underlyings that were transferred into the strategy to back *funded normal withdraw receipts* (`collectWithdrawFunds`), which have no isolation guard — `_transferFundedClaim` only protects `defaultRecoveryReserve`, not the funded-but-unclaimed normal receipts.

### Finding Description
`requestInstantWithdraw` (lines 356-375) mints the user a strategy-token receipt and increases `pendingInstantWithdraws`, but does not pull any underlyings. Funding arrives later when the CDO calls `collectInstantWithdrawFunds` (lines 398-403), which only decrements `pendingInstantWithdraws`.

`claimInstantWithdrawRequest` (lines 380-393) then:

- burns `instantWithdrawsRequests[_user]` receipt tokens,
- zeroes the request,
- calls `_transferFundedClaim(_user, amount)` with no check that `collectInstantWithdrawFunds` was ever called for the current queue.

`_transferFundedClaim` (lines 897-907) only verifies `balance - defaultRecoveryReserve >= amount`. Underlyings that `collectWithdrawFunds` (lines 411-430) pulled into the strategy to back already-funded normal withdraw receipts sit in the same balance and are completely unprotected.

Concrete sequence:

1. Epoch N ends; `stopEpoch`/`collectWithdrawFunds` transfers `pendingWithdraws` underlyings into the strategy to back users' funded receipts (claimable via `claimWithdrawRequest`, which correctly enforces `epochNumber > lastWithdrawRequest`).
2. Manager lowers APR so `lastEpochApr > unscaledApr + instantWithdrawAprDelta`. During the buffer, attacker (a KYC'd tranche holder) calls `IdleCDOEpochVariant.requestWithdraw`, which routes to `creditVault.requestInstantWithdraw` and mints the receipt before any borrower funding exists (funding only happens at the next `startEpoch` via the CDO → `collectInstantWithdrawFunds` path).
3. Attacker immediately calls `claimInstantWithdrawRequest`. The strategy balance currently holds only the funded normal receipts' underlyings; `allowInstantWithdraw` is enabled and no funding check exists, so the attacker is paid out of other users' money.
4. When legitimate users call `claimWithdrawRequest`, the strategy balance is short and their claims revert — permanent loss of funded receipts up to the attacker's instant amount.

This mirrors the CVE's shape exactly: a read path (`claimInstantWithdrawRequest` → `_transferFundedClaim` → `balanceOf`) returns value belonging to a different logical object because the per-queue funding state was never consulted.

### Impact Explanation
Direct theft of underlying tokens equal to the attacker's instant receipt amount, bounded by the strategy balance of funded-but-unclaimed normal withdraw receipts. Victims' `claimWithdrawRequest` calls revert or are underpaid; their receipt tokens were already burned-able value, so the loss is permanent for them. Loss is quantifiable: min(attacker instant amount, strategy balance of funded receipts).

### Likelihood Explanation
Requires (a) an instant-withdraw-enabled vault (`allowInstantWithdraw`, nonzero `instantWithdrawAprDelta`), (b) an APR decrease triggering the instant path, (c) pending funded normal receipts in the strategy at the time of the request/claim. The attacker is an ordinary KYC'd tranche holder, in scope. The window between instant receipt minting and `collectInstantWithdrawFunds` funding is a full buffer period, making the race practical. Not previously mitigated: the normal-claim epoch gating (`epochNumber <= lastWithdrawRequest`) exists only on the normal path; the instant path has no equivalent funding check. Uncertainty: I could not fully confirm the `startEpoch` ordering for `collectInstantWithdrawFunds` inside `IdleCDOEpochVariant`, but even if funding occurs at `startEpoch`, the buffer window before it leaves the strategy balance exposed.

### Recommendation
In `claimInstantWithdrawRequest`, only allow claiming against actually-collected instant funds — e.g., decrement `pendingInstantWithdraws`-style accounting at request time into a separate `fundedInstantWithdraws` counter that `collectInstantWithdrawFunds` increments, and require `amount <= fundedInstantWithdraws - claimedInstantWithdraws` before `_transferFundedClaim`. Alternatively, isolate funded normal receipts into a tracked reserve (like `defaultRecoveryReserve`) that `_transferFundedClaim` excludes from instant payouts.

### Proof of Concept
A Foundry fork test in `test/foundry/IdleCreditVault.t.sol` would: (1) deposit as userA, run `stopEpoch` so `collectWithdrawFunds` funds userA's receipt into the strategy; (2) as attacker, trigger the instant path via `cdoEpoch.requestWithdraw` after lowering APR (`_forceLastEpochAprToZero`-style manipulation used in existing tests), in the same buffer tx call `cdoEpoch.claimInstantWithdrawRequest`; (3) assert attacker received underlying while `creditVault` balance dropped below the funded receipts total; (4) assert `userA`'s `claimWithdrawRequest` reverts on insufficient balance.