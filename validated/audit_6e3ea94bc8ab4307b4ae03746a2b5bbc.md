### Title
Instant-withdraw receipts requested after `getInstantWithdrawFunds` are paid immediately from funds reserved for earlier claimants - (`contracts/strategies/idle/IdleCreditVault.sol` / `contracts/IdleCDOEpochVariant.sol`)

### Summary
`claimInstantWithdrawRequest` pays the *entire* `instantWithdrawsRequests[user]` balance whenever `allowInstantWithdraw` is true, without checking whether the specific receipt was actually funded via `collectInstantWithdrawFunds`. Because `allowInstantWithdraw` stays `true` for the rest of the epoch once `getInstantWithdrawFunds` succeeds, a KYC'd lender can create a **new, unfunded** instant receipt in the same epoch and immediately claim it, draining underlying that was collected to back *other users'* already-funded instant claims — the same "withdraw collateral while ignoring the outstanding liability" flaw as the Platypus `emergencyWithdraw` bug.

### Finding Description
In `IdleCDOEpochVariant.requestWithdraw`, when the APR has dropped (`lastEpochApr > unscaledApr + instantWithdrawAprDelta`), the CDO calls `IdleCreditVault.requestInstantWithdraw(_underlyings, msg.sender)`, which mints a 1:1 strategy-token receipt and increments both `instantWithdrawsRequests[user]` and `pendingInstantWithdraws` (IdleCreditVault.sol:356-375).

`getInstantWithdrawFunds` pulls `pendingInstantWithdraws` from the borrower, calls `collectInstantWithdrawFunds` (which decrements `pendingInstantWithdraws` to ~0 and moves the cash into the strategy), and sets `allowInstantWithdraw = true` (IdleCDOEpochVariant.sol:558-574). `allowInstantWithdraw` is only reset at the *next* `stopEpoch` (line 486), and the CDO's `claimInstantWithdrawRequest` checks only that flag (line 977).

`IdleCreditVault.claimInstantWithdrawRequest` then burns `instantWithdrawsRequests[user]` in full and calls `_transferFundedClaim(user, amount)` (IdleCreditVault.sol:387-392) — there is no per-receipt funded/unfunded tracking analogous to `withdrawsRequestsByEpoch`/`lossRecoveryPriceByEpoch` for normal claims, and no check that `pendingInstantWithdraws == 0` for the receipts being burned.

Attack sequence (running epoch, fixed-APR non-AYS pool, after manager calls `getInstantWithdrawFunds`):

1. Attacker (KYC-passed lender) deposits via `depositDuringEpoch` (or already holds tranches from the buffer).
2. The APR-drop condition that triggered the original instant withdrawals is still true, so `requestWithdraw(amount, tranche)` takes the instant path, minting the attacker a receipt and bumping `instantWithdrawsRequests[attacker]` and `pendingInstantWithdraws`.
3. Attacker immediately calls `claimInstantWithdrawRequest()`. `allowInstantWithdraw` is still `true`, so the strategy burns the attacker's receipt and transfers `amount` underlying — paid out of the cash collected in step `getInstantWithdrawFunds` that was earmarked for earlier requesters who have not yet claimed.
4. When earlier requesters claim, the strategy balance is short by the attacker's payout and `_transferFundedClaim` reverts; their funded receipts are frozen. At `stopEpoch`, the re-grown `pendingInstantWithdraws` must be funded *again* by the borrower for a liability whose cash was already consumed, so the deficit is socialized across the pool.

### Impact Explanation
The attacker's unfunded receipt is paid 1:1 from underlying reserved for other claimants. Impact equals the attacker's instant-withdraw amount, capped by the funded instant pool balance held in the strategy at that moment (potentially the entire prefunded instant-withdraw pool). This is direct theft / permanent freezing of other users' funded claims — the broken invariant is "one receipt, one funded payout": the strategy treats a receipt as backed by collected borrower funds when in fact that receipt was created after the collection.

### Likelihood Explanation
Requires: a fixed-APR (non-AYS, non-programmable) vault, an APR decrease large enough to enable instant withdrawals, the manager having called `getInstantWithdrawFunds`, and the attacker holding or acquiring tranche tokens (depositDuringEpoch is available unless disabled). Attacker needs no privilege beyond KYC. Profit is bounded by the tranche value they burn — the gain is exiting instantly at par against a deficit left in the pool, i.e. the attacker receives cash that belongs to earlier claimants while their own receipt leaves `pendingInstantWithdraws` permanently unfunded; if tranche price is above the recovered pool ratio, the exit is profitable at other LPs' expense. Uncertainty: the attacker's net gain depends on pool insolvency materializing (victim claims reverting), and on whether the deposit/route cost (tranche price vs payout) is favorable.

### Recommendation
Gate claims on funding, not on the global flag: e.g. in `claimInstantWithdrawRequest` only allow claiming up to the balance that was already collected (track a per-user funded amount, or snapshot `instantWithdrawsRequestsByEpoch` at collection time), or have `requestInstantWithdraw` revert while `allowInstantWithdraw` is already true / while a previous collection for the epoch is pending claims. Alternatively, set `allowInstantWithdraw = false` whenever `pendingInstantWithdraws != 0`.

### Proof of Concept
Foundry fork PoC outline (following `test/foundry/IdleCreditVault.t.sol` harness):

```solidity
// setup: fixed-APR pool, AYS disabled, deposits during epoch enabled, KYC'd attacker
// 1. user1 deposits AA in buffer; manager drops APR so instant path activates
// 2. epoch starts; user1 requestWithdraw -> instant receipt created
// 3. warp past instantWithdrawDeadline; manager calls getInstantWithdrawFunds()
//    -> allowInstantWithdraw = true, strategy holds user1's funds
// 4. attacker depositDuringEpoch(amount, AATranche)
// 5. attacker requestWithdraw(amount, AATranche) -> new UNFUNDED instant receipt
// 6. attacker claimInstantWithdrawRequest() -> succeeds, paid from user1's funds
// 7. user1 claimInstantWithdrawRequest() -> reverts (insufficient strategy balance)
assertEq(underlying.balanceOf(attacker), attackerDeposit); // attacker out at par
vm.expectRevert(); cdoEpoch.claimWithdrawRequest(); // user1 frozen
```

Note: I did not fully verify step 5's gating (whether `allowAAWithdrawRequest` remains true mid-epoch for the instant path and whether `depositDuringEpoch` is reachable in every instant-enabled configuration) within the available context; if `requestWithdraw` is blocked mid-epoch the attack reduces to a buffer-phase variant where the attacker pre-positions a second request before funding completes.