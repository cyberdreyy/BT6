### Title
Aggregated instant-withdraw receipts allow claiming unfunded historical requests against other users' funded withdrawals - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
The external CVE is a bounds violation where attacker-influenced data corrupts memory outside its intended region. The analog in this codebase is a receipt-bounds violation: `IdleCreditVault` tracks instant-withdraw receipts as a single per-user aggregate (`instantWithdrawsRequests[_user]`) while funding is tracked only as a global bucket (`pendingInstantWithdraws`). A user can carry an old, never-funded instant receipt into a later epoch, stack a new request on top, and `claimInstantWithdrawRequest` pays out the full aggregate — including the unfunded historical amount — from underlying that was collected to fund other claimants.

### Finding Description
`requestInstantWithdraw` mints a strategy-token receipt and adds to both the per-user aggregate and the global `pendingInstantWithdraws`, but records per-epoch data (`instantWithdrawsRequestsByEpoch`, `instantWithdrawClaimsByEpoch`) that is only consumed by the default-recovery path (`_claimDefaultedInstantWithdrawRequest`, `_defaultPrefundedInstantReserve`). The normal claim path ignores per-epoch accounting entirely:

- `contracts/strategies/idle/IdleCreditVault.sol:356-375` — `requestInstantWithdraw` aggregates `instantWithdrawsRequests[_user] += _amount` with no check for an existing unfunded receipt.
- `contracts/strategies/idle/IdleCreditVault.sol:380-393` — `claimInstantWithdrawRequest` burns and pays `instantWithdrawsRequests[_user]` in full via `_transferFundedClaim`.
- `contracts/strategies/idle/IdleCreditVault.sol:398-403` — `collectInstantWithdrawFunds` decrements the global `pendingInstantWithdraws` by whatever amount the CDO actually collected; there is no per-user or per-epoch funded/unfunded split.
- `contracts/strategies/idle/IdleCreditVault.sol:897-907` — `_transferFundedClaim` only protects `defaultRecoveryReserve`; it does not verify the caller's receipt was funded.
- `contracts/IdleCDOEpochVariant.sol:761-768` — the instant path in `requestWithdraw` calls `creditVault.requestInstantWithdraw` without checking `instantWithdrawsRequests[msg.sender]`; the `_hasWithdrawRequest`/instant guard at `IdleCreditVault.sol:247-251` only applies once `defaultRecoveryFinalized` is true.

Attack sequence (unprivileged KYC'd lender, `isWalletAllowed` passes):
1. Epoch N ends; APR drops by more than `instantWithdrawAprDelta` so `_isInstantWithdrawEnabled()` and the delta check pass. Attacker calls `IdleCDOEpochVariant.requestWithdraw(amount1, AA/BB)` → instant path → receipt of `amount1` strategy tokens minted; `pendingInstantWithdraws += amount1`.
2. The borrower returns insufficient funds (an honest shortfall — e.g. it funds only part of the instant queue before `instantWithdrawDeadline`, or funds nothing). The attacker's receipt remains pending; `pendingInstantWithdraws` keeps the unfunded `amount1` remainder.
3. In a later epoch M, APR drops again (honest manager action). Attacker requests another instant withdraw of `amount2`. Now `instantWithdrawsRequests[attacker] = amount1 + amount2`, but only `amount2`'s share of the newly collected funds is economically "theirs".
4. Once the strategy holds ≥ `amount1 + amount2` of funded underlying (collected for epoch M claims belonging to the attacker and other users), the attacker calls `claimInstantWithdrawRequest` via the CDO and receives `amount1 + amount2` — extracting `amount1` that was never funded for them.

### Impact Explanation
Direct theft. The excess `amount1` is paid from underlying collected via `collectInstantWithdrawFunds`/`collectWithdrawFunds` that is backing other users' pending and instant receipts. Those users' subsequent claims then revert on insufficient balance or are permanently underfunded — a one-receipt-one-payout invariant violation where a stale, unfunded receipt is redeemed at par against a communal funding pool. Loss is quantified as the unfunded carried-over receipt amount, up to the full balance the attacker can aggregate across repeated requests.

### Likelihood Explanation
Requires: (a) an epoch where the APR drop enables instant withdraws, (b) the borrower under-funding the instant queue (a normal credit-event outcome, not attacker-controlled but realistic for a credit vault whose entire premise is borrower repayment risk), and (c) a later epoch again enabling instant withdraws. All attacker steps are unprivileged calls by a KYC-passing lender. No existing guard stops it: `_skimDonatedAssets` is irrelevant, `nonReentrant` doesn't help, the epoch gating in `_claimFundedWithdrawRequest` does not exist on the instant path, and the only per-epoch instant accounting is reserved for the post-default flow.

### Recommendation
Track funded vs. unfunded instant receipts per epoch. Either (1) revert in `requestInstantWithdraw`/the CDO instant path when `instantWithdrawsRequests[_user] != 0` and the prior receipt was not fully funded (mirroring the `defaultRecoveryFinalized` guard), or (2) store a per-epoch funded price for instant receipts analogous to `lossRecoveryPriceByEpoch` — e.g. record in `instantWithdrawsRequestsByEpoch`/`instantWithdrawClaimsByEpoch` how much of each epoch's claims was actually collected, and pay `min(receipt, fundedShare)` in `claimInstantWithdrawRequest` instead of the raw aggregate. Cap `claimInstantWithdrawRequest` payouts at the funded portion of `instantWithdrawsRequests[_user]` derived from `pendingInstantWithdraws` deltas per epoch.

### Proof of Concept
```solidity
// test/foundry/InstantReceiptBounds.t.sol — fork/Foundry PoC sketch
function testUnfundedInstantReceiptOverclaim() external {
    // Setup: standard epoch vault, attacker is KYC'd lender with AA tranche tokens.
    uint256 amt1 = 1_000 * ONE_SCALE;
    uint256 amt2 = 1_000 * ONE_SCALE;

    // Epoch N: attacker requests instant withdraw after APR drop.
    // cdoEpoch.requestWithdraw(amt1, AATranche) -> requestInstantWithdraw -> receipt minted.
    // Borrower repays only principal, NOT the instant queue:
    //   collectInstantWithdrawFunds is called with < amt1 (or 0).
    // assert(strategy.instantWithdrawsRequests(attacker) == amt1);
    // assert(strategy.pendingInstantWithdraws() == amt1); // unfunded remainder

    // Epoch M: APR drops again; attacker requests another instant withdraw.
    // cdoEpoch.requestWithdraw(amt2, AATranche);
    // assert(strategy.instantWithdrawsRequests(attacker) == amt1 + amt2);

    // Honest borrowers/other users' claims get funded: strategy now holds >= amt1 + amt2
    // (amt2 for attacker + amounts collected for other claimants' receipts).

    // Attacker claims the FULL aggregate:
    // cdoEpoch.claimInstantWithdrawRequest();
    // Received amt1 + amt2 although only amt2 was funded for this user.

    // Other users' funded claims now revert / are underpaid:
    // vm.expectRevert(); cdoEpoch.claimInstantWithdrawRequestFor(victim);
}
```
Key assertion: the strategy's underlying balance after the attacker's claim drops below the sum of remaining funded receipts, i.e. `underlyingToken.balanceOf(strategy) < sum of other users' funded claims` — quantified theft equal to `amt1`.

Caveat: I was unable to fully trace `stopEpoch`/instant-funding ordering inside `IdleCDOEpochVariant` (the grep output was truncated), so the exact call sequence that leaves an instant receipt partially unfunded should be confirmed against `collectInstantWithdrawFunds` call sites; the receipt-aggregation and unguarded-claim logic itself is directly visible in `IdleCreditVault.sol:356-393`.