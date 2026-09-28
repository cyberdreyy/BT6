### Title
`stopEpoch` disables `allowInstantWithdraw` and permanently locks already-funded instant-withdraw receipts inside `IdleCreditVault` — ([contracts/IdleCDOEpochVariant.sol](contracts/IdleCDOEpochVariant.sol))

### Summary
The kernel bug is a lock primitive (`pci_bus_lock()`) that secures every device on the bus except the bridge (`bus->self`) that is actually issuing the reset — the entity whose state is already committed is left unprotected while everything downstream is locked. The analog in `IdleCDOEpochVariant._stopEpoch` is the `allowInstantWithdraw` flag: on a normal (non-`requestingAllFunds`) stop it is unconditionally cleared, locking the claim path for *all* instant withdrawals — including receipts whose underlying funds were already pulled from the borrower and delivered to `IdleCreditVault` earlier in the same epoch. The epoch is "locked" correctly, but the already-funded receipts (the bridge itself) are locked out with no recovery path.

### Finding Description
`getInstantWithdrawFunds()` pulls the pending instant-withdraw amount from the borrower via `getFundsFromBorrower`, forwards it to the strategy with `collectInstantWithdrawFunds`, and sets `allowInstantWithdraw = true` (`IdleCDOEpochVariant.sol:558-574`). From that point the funds irrevocably live in `IdleCreditVault` backing outstanding receipts.

At epoch end, `_stopEpoch`'s success path executes `allowInstantWithdraw = _isRequestingAllFunds` (`IdleCDOEpochVariant.sol:486`). For an ordinary stop this is `false`. `claimInstantWithdrawRequest()` is gated solely by that flag (`IdleCDOEpochVariant.sol:975-979`), so any user who had a funded instant receipt but did not claim before `stopEpoch` is now permanently blocked:

- the flag is only re-enabled by `getInstantWithdrawFunds` (requires `isEpochRunning` and funded pending requests) or by a close-pool stop;
- the user already burned their tranche tokens in `requestWithdraw` (line 767), so they cannot re-enter via `requestWithdraw` in the next buffer;
- the underlying remains in `IdleCreditVault`, absorbed into strategy value — a forced donation to remaining depositors.

The default path deliberately preserves `allowInstantWithdraw` for funded receipts (`_handleBorrowerDefault`, lines 502-504 comment and 577-599), proving the intended invariant is "funded instant receipts stay claimable" — the ordinary stop path violates exactly that invariant for the still-unclaimed funded set.

### Impact Explanation
Permanent freezing / effective theft of user funds. A lender who requested an instant withdrawal and whose request was funded can lose 100% of the requested underlying (principal plus epoch interest as computed at request time) simply by claiming after `epochEndDate` instead of before it. The funds are not returned; they are stranded in the strategy and accrue to other NAV holders. Loss = the full funded `pendingInstantWithdraws` amount attributable to unclaimed receipts.

### Likelihood Explanation
Requires only that a user doesn't claim between `instantWithdrawDeadline` and `epochEndDate` — a timing gap the protocol itself creates, since claims can only happen inside that window and there is no warning that the window closes at `stopEpoch`. Any user distracted, gas-limited, or simply claiming late hits this deterministically whenever instant withdraws were triggered (APR decrease beyond `instantWithdrawAprDelta`). No privileged collusion needed; the honest manager's routine `stopEpoch` call is the trigger.

### Recommendation
On the stop path, only clear `allowInstantWithdraw` when no funded-but-unclaimed instant receipts remain (e.g., keep it set while `IdleCreditVault(strategy)` reports outstanding funded instant claims), or add a sweep so residual funded receipts remain claimable post-stop — mirroring the default path's existing policy of preserving already-fulfilled claims.

### Proof of Concept
Foundry fork PoC outline:

```solidity
// 1. Deposit as lender (KYC'd), manager sets epoch params, startEpoch.
idleCDO.depositAA(amount);
vm.prank(manager); cdoEpoch.setEpochParams(30 days, 5 days);
vm.prank(manager); cdoEpoch.startEpoch();

// 2. stopEpoch once to establish lastEpochApr, then lower APR so
//    instant withdraw triggers; user requestWithdraw in buffer -> instant receipt.
vm.prank(manager); cdoEpoch.stopEpochWithDuration(highApr, interest, 30 days, 0);
IdleCreditVault(strategy).setApr(lowApr); // apr delta > instantWithdrawAprDelta
vm.prank(user); cdoEpoch.requestWithdraw(0, AAtranche); // instant path, tokens burned

// 3. manager startEpoch; warp past instantWithdrawDeadline;
//    getInstantWithdrawFunds succeeds -> allowInstantWithdraw = true, funds in strategy.
vm.prank(manager); cdoEpoch.startEpoch();
vm.warp(cdoEpoch.instantWithdrawDeadline() + 1);
vm.prank(manager); cdoEpoch.getInstantWithdrawFunds();
assertTrue(cdoEpoch.allowInstantWithdraw());

// 4. Warp to epochEndDate, manager stops epoch normally.
vm.warp(cdoEpoch.epochEndDate() + 1);
vm.prank(manager); cdoEpoch.stopEpoch(newApr, interest);
assertFalse(cdoEpoch.allowInstantWithdraw());

// 5. User's funded receipt is now unclaimable forever.
vm.prank(user);
vm.expectRevert();
cdoEpoch.claimInstantWithdrawRequest();
```

Uncertainty note: the repository index did not expose `IdleCreditVault`'s internal `claimInstantWithdrawRequest`/`pendingInstantWithdraws` bookkeeping, so the exact stranded-funds accounting on the strategy side could not be line-verified; the CDO-side flag gate and the absence of any alternate claim path for the burned-tranche holder are confirmed in `IdleCDOEpochVariant.sol:486,767,975-979`.