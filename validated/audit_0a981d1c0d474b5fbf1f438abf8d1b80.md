### Title
Unfunded instant-withdraw receipts are paid from other users' funded claims due to aggregate receipt accounting - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
The Tomcat bug is a race between two completion paths where a response is delivered to the wrong party. The credit-vault analog lives in `claimInstantWithdrawRequest`: instant-withdraw receipts are tracked only as an aggregate per user (`instantWithdrawsRequests[_user]`), while funding status is tracked only globally (`pendingInstantWithdraws`). A claim burns the entire aggregate receipt and transfers the full amount, even if part of it was requested after the last `collectInstantWithdrawFunds` pull and is still unfunded. The unfunded portion is paid out of underlying already held by the strategy that is earmarked for other users' matured claims.

### Finding Description
In `requestInstantWithdraw` (`IdleCreditVault.sol:356-375`) each request increases `instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch[_user][currentEpoch]` and `pendingInstantWithdraws`. Funding arrives asynchronously through `collectInstantWithdrawFunds` (`IdleCreditVault.sol:398-403`), which decreases `pendingInstantWithdraws` and pulls underlying from the CDO into the strategy contract.

`claimInstantWithdrawRequest` (`IdleCreditVault.sol:380-393`) then does:

```
uint256 amount = instantWithdrawsRequests[_user];
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);
```

It pays the *entire* aggregate receipt with no check that every component was actually funded. The funded/unfunded split exists only in the global `pendingInstantWithdraws` counter, which is never consulted on the claim path (it is only read during default finalization via `_defaultPrefundedInstantReserve`). `_transferFundedClaim` protects only `defaultRecoveryReserve`, not the general strategy balance that backs other users' already-funded normal withdraw claims, settled APR0 claims, or other users' funded instant receipts.

Race sequence (attacker = any KYC-passing lender, epoch running, `allowInstantWithdraw` on):

1. Attacker calls `requestInstantWithdraw(A)` — `pendingInstantWithdraws += A`.
2. Honest manager/epoch flow calls `collectInstantWithdrawFunds(A)` — strategy balance `+= A`, `pendingInstantWithdraws = 0`. Funds are now held for the attacker.
3. Attacker front-runs the next collection and calls `requestInstantWithdraw(B)` — `instantWithdrawsRequests[user] = A + B`, `pendingInstantWithdraws = B`, no new funds collected.
4. In the same running epoch the attacker calls `claimInstantWithdrawRequest`, which burns `A + B` receipts and transfers `A + B` underlying. `A` was funded; `B` is paid out of underlying held for other users' funded claims (e.g. matured `withdrawsRequests` payouts awaiting `claimWithdrawRequest`).

This mirrors the external bug exactly: two completion paths (funding collection vs. claim) race, and the party gets a "response" (payout) funded by value intended for a different user.

### Impact Explanation
Direct theft: the attacker extracts `B` underlying that belongs to other users holding funded-but-unclaimed receipts, leaving those users' later claims to revert on insufficient balance (permanent loss / insolvency of the strategy reserve, quantified as `B`).

### Likelihood Explanation
Requires `allowInstantWithdraw` enabled and other users' funded claims sitting in the strategy balance — both normal operating conditions between a `stopEpoch`/`collectInstantWithdrawFunds` and user claims. The attacker only needs to place their second request before the protocol collects its funding, which is within their control since request and claim are callable in the same running epoch.

### Recommendation
Track the funded portion per user (or per epoch) — e.g. only clear `instantWithdrawsRequestsByEpoch` entries whose epoch's `instantWithdrawClaimsByEpoch` was fully collected — or decrement and enforce `pendingInstantWithdraws`-style funding checks inside `claimInstantWithdrawRequest` so unfunded receipts cannot be claimed early.

### Proof of Concept
```solidity
// Foundry fork PoC sketch
// Setup: pool with allowInstantWithdraw = true, victim V with a matured
// funded withdraw claim of C underlying sitting in the strategy balance.

// 1. Epoch running. Attacker requests instant withdraw of A.
vm.prank(attacker);
cdoEpoch.requestInstantWithdraw(A);            // pendingInstantWithdraws = A

// 2. Honest flow funds it: strategy pulls A underlying from CDO.
vm.prank(manager);
cdoEpoch.startEpoch();                          // collectInstantWithdrawFunds(A)

// 3. Attacker requests B (unfunded) and immediately claims.
vm.startPrank(attacker);
cdoEpoch.requestInstantWithdraw(B);            // pendingInstantWithdraws = B
cdoEpoch.claimInstantWithdrawRequest();        // transfers A + B
vm.stopPrank();

// Strategy balance was only A + C; claim of A + B succeeds by spending
// B of the victim's funded claim C.
assertEq(underlying.balanceOf(attacker), attackerBalPre + A + B);

// 4. Victim's funded claim now reverts / underpays (insolvency of B).
vm.prank(victim);
vm.expectRevert();                              // insufficient strategy balance
cdoEpoch.claimWithdrawRequest();
```

Note: I could not fully verify the exact CDO-side gating order (whether `claimInstantWithdrawRequest` is callable while a same-epoch request is still in `pendingInstantWithdraws`, or whether an additional per-epoch check exists in `IdleCDOEpochVariant` that blocks step 3→4 in one transaction). If the CDO already blocks claims until all receipts in the aggregate are funded, this reduces to a cross-epoch variant of the same missing funded/unfunded split rather than the single-epoch race above.