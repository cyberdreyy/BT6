### Title
`claimInstantWithdrawRequest` is claimable for unfunded receipts — the `allowInstantWithdraw` flag does not distinguish funded vs newly-requested instant withdrawals - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
`IdleCDOEpochVariant.claimInstantWithdrawRequest` gates claims solely on the `allowInstantWithdraw` boolean, and `IdleCreditVault.claimInstantWithdrawRequest` pays the *entire* `instantWithdrawsRequests[_user]` aggregate with no epoch check. Once instant withdrawals are funded once in an epoch (`allowInstantWithdraw = true`), any additional instant request made afterwards is immediately claimable even though its funds were never collected from the borrower — letting the claimant drain funded normal-withdraw reserves and other users' money held by the strategy.

### Finding Description
In `IdleCDOEpochVariant.getInstantWithdrawFunds`, after `collectInstantWithdrawFunds` succeeds, `allowInstantWithdraw` is set to `true` and stays true for the rest of the epoch (`contracts/IdleCDOEpochVariant.sol:560-574`). The claim path only checks that flag:

```solidity
// contracts/IdleCDOEpochVariant.sol:975-979
function claimInstantWithdrawRequest() external {
    _checkNotAllowed(!allowInstantWithdraw);
    IdleCreditVault(strategy).claimInstantWithdrawRequest(msg.sender);
}
```

The strategy then burns and pays the full per-user aggregate:

```solidity
// contracts/strategies/idle/IdleCreditVault.sol:387-392
uint256 amount = instantWithdrawsRequests[_user];
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);
```

But `requestWithdraw` keeps routing new requests into the instant bucket whenever `lastEpochApr > unscaledApr + instantWithdrawAprDelta` — a condition that persists for the whole epoch (`contracts/IdleCDOEpochVariant.sol:761-770`). `requestInstantWithdraw` mints receipt strategy tokens and increments `instantWithdrawsRequests` and `pendingInstantWithdraws` (`contracts/strategies/idle/IdleCreditVault.sol:356-375`), yet those funds are only collected on the *next* `getInstantWithdrawFunds` call.

So a second instant request made after funding is "claimable" under the single flag even though no underlying backs it — exactly the analog of `bountyIsClaimable` being wrong for tiered bounties claimable in more states than `OPEN`. `_transferFundedClaim` only protects `defaultRecoveryReserve` (`contracts/strategies/idle/IdleCreditVault.sol:897-907`), so the payout is drawn from underlyings the strategy holds for *funded* normal withdraw receipts (`collectWithdrawFunds`, lines 411-430) or other prefunded instant claims. Additionally the claim never decrements `pendingInstantWithdraws`, so the bookkeeping still expects the borrower to fund a receipt that was already paid — a double-count.

### Impact Explanation
An unprivileged KYC'd lender can request an instant withdrawal after instant funding completed, then immediately call `claimInstantWithdrawRequest` and receive underlying tokens that were never collected from the borrower — directly stealing reserve set aside for other users' funded withdraw requests (or later instant claimants), i.e. theft / insolvency up to the strategy's free underlying balance.

### Likelihood Explanation
Requires an epoch where the APR dropped enough to enable instant mode (a normal market event) and `getInstantWithdrawFunds` to have been called. Both conditions are expected operational states; no privileged misbehavior needed.

### Recommendation
Track funded vs unfunded instant receipts per epoch (e.g., pay only `instantWithdrawClaimsByEpoch` that are backed, or snapshot the claimable amount when `collectInstantWithdrawFunds` runs) and/or reset `allowInstantWithdraw` and re-gate claims whenever `pendingInstantWithdraws != 0`. At minimum, `claimInstantWithdrawRequest` should revert while `pendingInstantWithdraws > 0`.

### Proof of Concept
Foundry fork PoC outline (following `test/foundry/IdleCreditVault.t.sol` helpers):
1. Deposit AA, `startEpoch()` with APR set `instantWithdrawAprDelta` below `lastEpochApr`.
2. User A calls `requestWithdraw(x, AATranche)` → routed to `requestInstantWithdraw`.
3. Warp past `instantWithdrawDeadline`; manager calls `getInstantWithdrawFunds` → borrower funds A's receipt, `allowInstantWithdraw = true`, `pendingInstantWithdraws = 0`. User B makes a normal `requestWithdraw`... — or simpler: B also had an instant request funded in step 3.
4. Attacker (A) calls `requestWithdraw(y, AATranche)` again → instant path, `pendingInstantWithdraws = y`, receipt minted, no funds collected.
5. A calls `claimInstantWithdrawRequest()` → flag still true → strategy burns `x + y` receipts and transfers `x + y` underlying, where only `x` was funded.
6. Assert A received `y` more than funded; assert B's subsequent claim reverts on insufficient strategy balance (theft), and `pendingInstantWithdraws` still shows `y` (double-count).

Note: I could not verify whether `allowInstantWithdraw` is reset elsewhere (e.g., at `startEpoch`/`stopEpoch`); if it is only cleared between epochs the attack still works intra-epoch as described, but if a missing reset also lets pre-funding claims slip across epochs, severity is unchanged.