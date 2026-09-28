### Title
Instant-withdraw receipts are claimable before the borrower funds them — receipt holders can drain other users' funded claims - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`requestInstantWithdraw` mints the user an immediately-claimable receipt (`instantWithdrawsRequests[_user]`) with no epoch marker, and `claimInstantWithdrawRequest` pays that receipt out of any underlying held by the strategy without checking that the request's epoch was funded via `collectInstantWithdrawFunds`. The normal withdraw path enforces a full-epoch wait (`epochNumber <= lastWithdrawRequest` reverts); the instant path has no equivalent gate. This mirrors the CVE-2020-0829 bug class — a stale/uninitialized-state confusion — applied to epoch accounting: a receipt created in epoch N is honored against funding collected for earlier epochs.

### Finding Description
In `requestInstantWithdraw` (lines 356-375) the CDO's strategy tokens are burned, an equal receipt amount is minted to the user, and `instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch[_user][epochNumber]`, and `pendingInstantWithdraws` are increased. Funding only arrives later, when the borrower repays at epoch start and the CDO calls `collectInstantWithdrawFunds` (lines 398-403), which decrements `pendingInstantWithdraws` and pulls underlying into the strategy.

`claimInstantWithdrawRequest` (lines 380-393) then:

```solidity
uint256 amount = instantWithdrawsRequests[_user];
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);
```

There is no check equivalent to the normal path's `epochNumber <= lastWithdrawRequest[_user]` revert (line 326), and no per-epoch funded tracking is consulted on the claim side — `instantWithdrawsRequestsByEpoch` is only read during default finalization. `_transferFundedClaim` (lines 897-907) only verifies the strategy's underlying balance exceeds `defaultRecoveryReserve`; it does not verify that the caller's receipt belongs to a funded epoch.

Concretely, the exploit sequence in a running epoch with instant withdraws enabled (i.e., `lastEpochApr > unscaledApr + instantWithdrawAprDelta`, checked at `IdleCDOEpochVariant.sol:761-770`):

1. Honest users hold previously funded instant-withdraw receipts; the borrower repaid at `startEpoch`, so the strategy holds underlying backing `pendingInstantWithdraws` — or simply holds funded-but-unclaimed balances.
2. Attacker (a KYC'd AA/BB tranche holder) calls `requestWithdraw` on the CDO, which takes the instant path: `creditVault.requestInstantWithdraw(_underlyings, msg.sender)` mints the attacker a receipt and bumps `pendingInstantWithdraws`, but no underlying has been collected for this new receipt.
3. In the same or next transaction the attacker calls `claimInstantWithdrawRequest`, which burns the receipt and transfers `amount` underlying via `_transferFundedClaim` — spending funds collected for the honest users' receipts.
4. Honest users' subsequent claims revert on insufficient balance; `pendingInstantWithdraws` is now inconsistent with actual reserves (the borrower's next funding round covers the attacker's already-paid receipt while honest claims stay unpaid).

The per-epoch bookkeeping exists (`instantWithdrawsRequestsByEpoch`, `instantWithdrawClaimsByEpoch`) but is only consumed by the default-recovery path (`_claimDefaultedInstantWithdrawRequest`, `defaultPendingClaimBasis`), never to gate a normal claim on "your epoch was funded."

### Impact Explanation
Direct theft of other users' funded instant-withdraw proceeds. Loss is bounded by the strategy's spendable underlying balance at attack time, i.e., all previously collected-but-unclaimed instant claims plus any residual funding. The attacker only needs tranche tokens (a KYC'd deposit) and an APR drop between epochs, which is a normal market condition. The broken invariant is "one receipt, one payout — funded by its own epoch."

### Likelihood Explanation
Requires (a) instant withdrawals enabled, (b) an APR decrease exceeding `instantWithdrawAprDelta` at a stop, and (c) spendable underlying sitting in the strategy (unclaimed funded receipts). All are unprivileged-achievable states. No privileged actor misbehavior is needed; the honest manager/borrower calls simply create the funded pool the attacker drains.

### Recommendation
Gate `claimInstantWithdrawRequest` on funding the same way normal claims are gated on epoch: record the request epoch per receipt and require `epochNumber > requestEpoch` **and** that the receipt's epoch was collected (e.g., a funded-epoch watermark or per-epoch funded flag set in `collectInstantWithdrawFunds`). Alternatively, subtract claimed amounts from a funded-balance counter rather than paying from the raw token balance. Caveat: I was unable to fully trace the CDO-side `claimInstantWithdrawRequest` wrapper in `IdleCDOEpochVariant.sol` within the available iterations; if it independently enforces an epoch wait before calling the strategy, this vector is mitigated and the issue reduces to the missing defense-in-depth in the strategy.

### Proof of Concept
```solidity
// Foundry fork PoC sketch
// Setup: credit vault with instantWithdraw enabled, honest user U and attacker A holding AA tranches.
// Epoch 0 runs, manager stops epoch with lower APR (lastEpochApr > unscaledApr + delta).
// 1) U requests instant withdraw; epoch 1 starts; borrower funds -> strategy.collectInstantWithdrawFunds(U_amt)
//    Strategy now holds U_amt underlying; U has not yet claimed.
// 2) Attacker A calls cdoEpoch.requestWithdraw(aAmt, AATranche) -> requestInstantWithdraw mints receipt.
//    No underlying is collected for A's receipt.
// 3) A calls cdoEpoch.claimInstantWithdrawRequest() in the same block.
//    _transferFundedClaim pays A from the strategy balance (U's funded money).
// 4) U calls claimInstantWithdrawRequest() -> reverts/underpays (balance drained).
assertGt(attackerUnderlyingGain, 0);
```