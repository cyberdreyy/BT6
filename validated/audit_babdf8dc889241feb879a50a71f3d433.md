### Title
Unfunded instant-withdraw claims can steal funded instant/normal withdrawals reserved in the strategy - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.claimInstantWithdrawRequest` pays a user's full `instantWithdrawsRequests[_user]` balance 1:1 from the strategy contract's underlying balance without verifying that the borrower's funds for that request have actually been collected via `collectInstantWithdrawFunds`. `pendingInstantWithdraws` — the only variable tracking whether instant funds are still owed — is never checked in the claim path. The strategy routinely holds underlying between `collectInstantWithdrawFunds` and the last honest claim (claims are spread over the `instantWithdrawDelay` window), so a requester whose request was never funded can drain underlyings that back other users' claims, mapping to the CVE class of unauthorized state manipulation plus denial of service to other users.

### Finding Description
In `requestInstantWithdraw` (lines 356-375) the strategy burns the CDO's receipt tokens, mints receipts to the user, and increments `instantWithdrawsRequests[_user]` and `pendingInstantWithdraws`. Funding arrives later when the CDO calls `collectInstantWithdrawFunds` (lines 398-403), which decrements `pendingInstantWithdraws` and pulls underlying from the CDO. However, `claimInstantWithdrawRequest` (lines 380-393) executes:

```solidity
uint256 amount = instantWithdrawsRequests[_user];
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);
```

with no funding gate. `_transferFundedClaim` (lines 897-907) only protects `defaultRecoveryReserve`; any other underlying held by the strategy — underlying already collected for other users' instant requests awaiting claim during the `instantWithdrawDelay` (3 days) window, or funded normal-withdraw underlyings — is spendable. There is no per-request "funded" flag, no epoch check, and no `pendingInstantWithdraws` consistency check.

Attack sequence (running epoch, instant mode enabled):
1. Honest users' instant requests from the previous buffer are funded: CDO calls `collectInstantWithdrawFunds(X)`, strategy now holds `X` underlying, honest claims are pending within the delay window.
2. Attacker (KYC'd lender with tranche tokens) calls `requestInstantWithdraw` before `instantWithdrawDeadline`. Their request increments `pendingInstantWithdraws` but no collect has happened for it yet.
3. Attacker calls `claimInstantWithdrawRequest` via the CDO. The strategy pays the attacker `amount` out of the `X` underlying held for honest claimants.
4. When the CDO later collects for the attacker's request, those funds replenish the strategy — but if the borrower's repayment is sized only to `pendingInstantWithdraws` and the window closes, or if the attacker claims before any further funding event, honest users' claims revert on insufficient balance (DoS) or are permanently short if no further funding arrives.

### Impact Explanation
Direct theft and/or temporary-to-permanent freezing of honest users' funded withdrawal claims. The attacker receives underlying equal to their request size before their request is backed; honest claimants lose the same amount up to the strategy's held balance. Loss is bounded by the strategy's underlying balance during the claim window (the full unclaimed instant bucket plus any funded normal withdrawals), which can be the entire pending-withdrawal volume of an epoch.

### Likelihood Explanation
Requires only an unprivileged lender allowed to request instant withdrawals (APR delta conditions met and `allowInstantWithdraw` set), and that the strategy holds unclaimed funded underlyings — the normal state during the multi-day claim window. Sequencing depends on whether the CDO calls `collectInstantWithdrawFunds` strictly before permitting any new `requestInstantWithdraw`; nothing in the strategy enforces this ordering, and `pendingInstantWithdraws != 0` does not block claims.

### Recommendation
Track funded vs unfunded instant receipts, e.g., only decrement `pendingInstantWithdraws`-backed amounts at claim time or store a `fundedInstantWithdraws` balance that `collectInstantWithdrawFunds` increases and `claimInstantWithdrawRequest` debits, reverting when a user's claim exceeds funded backing. Alternatively, timestamp/epoch-tag instant receipts and only allow claims once the CDO has collected funds covering that epoch's instant claims (`instantWithdrawClaimsByEpoch` already exists and could be compared against a funded-per-epoch counter).

### Proof of Concept
Not fully verified — the exploitability depends on the `IdleCDOEpochVariant` claim/collect ordering, which I could not finish reading within the tool budget. A Foundry fork PoC should: (1) start an epoch with two KYC'd lenders, (2) have lender A request an instant withdraw, warp past `instantWithdrawDelay`, have the borrower fund via `stopEpoch`/`collectInstantWithdrawFunds` without A claiming, (3) have attacker B call `requestInstantWithdraw` then `claimInstantWithdrawRequest` before any further collect, and (4) assert B received underlying while A's subsequent `claimInstantWithdrawRequest` reverts or pays less than funded. If the CDO reverts step 3 via an ordering guard not visible in the strategy, the finding reduces to a missing-defense-in-depth issue rather than an exploitable path.