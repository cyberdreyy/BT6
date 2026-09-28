### Title
Loss-adjusted withdrawal haircut applies only to `lastWithdrawRequest` epoch, letting older pending receipts exit at par - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`stopEpochWithDuration(_lossAmount)` funds pending withdrawals pro-rata over the aggregate `pendingWithdraws` basis and stores a single `lossRecoveryPriceByEpoch[epochNumber]` (IdleCreditVault.sol:411-421). But `_claimLossAdjustedWithdrawRequest` only looks up `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` and `_clearWithdrawClaimForEpoch` only deducts the *claim epoch's* `withdrawsRequestsByEpoch` from the aggregate `withdrawsRequests` (IdleCreditVault.sol:789-837). Analogous to `copy_user_gigantic_page()` receiving an unaligned fault address instead of the huge-page-aligned one, the claim is scoped to the user's most recent request epoch while the loss basis spans all pending epochs. A lender holding receipts from two consecutive epochs gets the older receipt paid at par from a reserve that was haircut pro-rata, over-draining the funded pool and leaving later claimants short.

### Finding Description
- A user can hold pending normal withdraw receipts in two different epochs. Requests made during epoch N's buffer map to `epochNumber = N`; if the user does not claim and requests again during epoch N+1's buffer, `withdrawsRequestsByEpoch[user][N]` and `[N+1]` are both nonzero while `lastWithdrawRequest[user] = N+1` (requestWithdraw, IdleCreditVault.sol:282-294).
- When the manager (honest) stops epoch N+1 with a realized loss, `collectWithdrawFunds` collects only `pendingToFund = pendingBasis * lossRecoveryPrice / RECOVERY_FULL` where `pendingBasis` includes *both* epochs' receipts (IdleCreditVault.sol:413-421), and `previewLossAdjustedWithdrawFunds` documents that "all pending receipts share their aggregate portion of the loss pro rata" (IdleCreditVault.sol:435-437).
- On claim, `_claimLossAdjustedWithdrawRequest` applies the haircut only to `withdrawsRequestsByEpoch[user][N+1]` (the `lastWithdrawRequest` epoch) and `_clearWithdrawClaimForEpoch` subtracts only that epoch's amount from `withdrawsRequests[user]` while resetting `lastWithdrawRequest[user] = 0` (lines 815-835).
- `_claimFundedWithdrawRequest` then sees `epochNumber > lastWithdrawRequest == 0`, treats the remaining epoch-N basis as an old funded receipt, and pays it **at par** via `_transferFundedClaim` (lines 319-349) — even though the reserve was only funded at `lossRecoveryPrice` for that basis.

### Impact Explanation
Direct insolvency/theft from other pending withdrawers. Let `A` = attacker's epoch-N receipt basis, `B` = attacker's epoch-N+1 basis, and `p = lossRecoveryPrice < RECOVERY_FULL`. The strategy receives `p·(A+B+other)` for all pending receipts. The attacker claims `A` (par) + `B·p` + their share, extracting `A·(1-p)` more than their pro-rata entitlement. That excess comes out of the same funded pool, so other users' loss-adjusted claims (`_claimLossAdjustedWithdrawRequest`) either underpay proportionally or revert on insufficient balance — a permanent loss equal to the escaped haircut. With a 50% recovery price and a large epoch-N receipt, roughly half of that receipt's value is stolen from co-pending lenders.

### Likelihood Explanation
Requires a KYC-passing lender making withdraw requests in two consecutive epochs without claiming the first (receipts must wait one epoch, so this occurs naturally), followed by a `stopEpochWithDuration` with nonzero `_lossAmount` — i.e., an honest manager recording a realized loss, which is the exact scenario this loss machinery exists for. No privileged misbehavior, timing luck, or external manipulation is needed; the accounting path is deterministic once the two-epoch pending position exists. The existing guard at IdleCreditVault.sol:263-271 only blocks new requests when `lossRecoveryPriceByEpoch[lastWithdrawRequest]` is already set, so it never sees the older epoch.

### Recommendation
Attribute the loss across every epoch that contributed to `pendingWithdraws`, not just `lastWithdrawRequest`. Options: store per-epoch pending bases (e.g., `pendingWithdrawsByEpoch`) and apply `lossRecoveryPrice` to each outstanding epoch's receipts, or iterate all epochs with nonzero `withdrawsRequestsByEpoch[user]` inside `_claimLossAdjustedWithdrawRequest`/`_clearWithdrawClaimForEpoch` so the entire pending basis is haircut. Alternatively, prevent holding pending receipts in more than one epoch (force claim or roll-forward of the older receipt before a new request, mirroring the existing post-default guard).

### Proof of Concept
Foundry fork PoC (schematic):

```solidity
// Setup: lender U (KYC'd) deposits into AA; other lenders O1..Ok also pending.
// Epoch N buffer:
cdoEpoch.requestWithdraw(amountU1, address(AAtranche)); // basis A in epoch N
// startEpoch N, stopEpoch N with _lossAmount = 0 -> no lossRecoveryPrice
// Epoch N+1 buffer:
cdoEpoch.requestWithdraw(amountU2, address(AAtranche)); // basis B in epoch N+1
// O1 also has pending receipt C
// startEpoch N+1; at end, manager calls:
cdoEpoch.stopEpochWithDuration(newApr, interest, duration, lossAmount); // p < 1
// strategy.collectWithdrawFunds receives p*(A+B+C) only
// Attacker claim:
uint256 balPre = underlying.balanceOf(U);
cdoEpoch.claimWithdrawRequest(); // pays B*p (loss-adjusted) + A at par (funded path)
assertEq(underlying.balanceOf(U) - balPre, A + B * p / 1e18);
// O1 claim now underpays or reverts:
// expected C*p but only p*(A+B+C) - (A + B*p) = C*p - A*(1-p) remains
assertLt(underlying.balanceOf(strategy), O1_expectedClaim); // insolvent by A*(1-p)
```

Key assertions mirror `test/foundry/IdleCreditVault.t.sol` recovery-claim tests: the attacker payout exceeds `A*p + B*p`, and the strategy's remaining balance is less than the sum of remaining loss-adjusted entitlements.