### Title
An unprivileged instant withdraw request permanently bricks `stopEpoch` on programmable-borrower vaults - ([File: contracts/IdleCDOEpochVariant.sol](contracts/IdleCDOEpochVariant.sol))

### Summary
`_stopEpoch` unconditionally calls `_checkProgrammableBorrowerMode`, which reverts whenever `isProgrammableBorrower` is true and `_pendingInstant() != 0`. If any `instantWithdrawsRequests`/`pendingInstantWithdraws` balance is outstanding, every `stopEpoch`/`stopEpochWithDuration` call reverts, permanently freezing the pool mid-epoch — the same bug class as the RFPSimpleStrategy report, where one configuration flag (`useRegistryAnchor`) leaves a required value unset and makes the core function revert on every call. Here the "flag combination" is `isProgrammableBorrower == true` together with a nonzero pending instant withdrawal.

### Finding Description
- `_checkProgrammableBorrowerMode` enforces `isProgrammableBorrower && (!isInterestMinted || _pendingInstant() != 0)` → revert (`IdleCDOEpochVariant.sol:105-107`).
- It is invoked at the top of `_stopEpoch` before any recovery path (`IdleCDOEpochVariant.sol:333`).
- `getInstantWithdrawFunds` is explicitly disabled for programmable borrowers (`IdleCDOEpochVariant.sol:556-560`), and `_claimDefaultedInstantWithdrawRequest` / `claimInstantWithdrawRequest` are only usable in flows that are themselves blocked or insufficient to drain `pendingInstantWithdraws` once the epoch is running and funded claims were never funded (`IdleCreditVault.sol:374,387`).
- `requestInstantWithdraw` mints a receipt and increments `pendingInstantWithdraws` with no programmable-borrower guard visible in the function (`IdleCreditVault.sol:356-375`). If this entry point remains reachable for a KYC'd lender in a programmable vault (or a request survives from before the flag flip), the attacker seeds the fatal state.

Once `pendingInstantWithdraws != 0` in a running programmable-borrower epoch, `stopEpoch` reverts deterministically and forever: `isEpochRunning` stays true, deposits/withdrawals stay paused (`_beforeUnpause`, `requestWithdraw` gating), and there is no privileged call that can clear the counter — `getInstantWithdrawFunds` is disabled for this mode and `setIsDepositDuringEpochDisabled`-style toggles cannot help.

### Impact Explanation
Permanent freezing of all pool funds: lenders' tranche tokens and withdraw receipts can never be settled because the epoch can never be stopped, `epochDuration` can never be reset, and borrower principal is never recalled. Total loss equals pool NAV for the attacker cost of one instant withdraw request.

### Likelihood Explanation
Requires a programmable-borrower deployment and the ability to leave a nonzero `pendingInstantWithdraws` while an epoch runs. The defense is only as strong as the gating of `requestInstantWithdraw`/`requestInstantWithdraw` CDO wrapper in this mode — the code checks the *consequence* (`_pendingInstant() != 0` reverts `stopEpoch`) rather than the *source*, so any path that creates the state is fatal. The structurally identical APR0 invariant (`prepareStopEpochWithApr0` reverting when `apr0TotalPrincipal != 0 && unscaledApr != 0`, `IdleCreditVault.sol:505-508`) is documented as intended and is manager-recoverable, so the programmable-borrower variant is the stronger finding — but note I could not fully verify every entry point that can set `pendingInstantWithdraws` under `isProgrammableBorrower`, so reachability of the seeding step should be confirmed on a fork before relying on this.

### Recommendation
Either make `getInstantWithdrawFunds` (and the claim path) functional for programmable borrowers, or revert `requestInstantWithdraw`/`requestInstantWithdraw` directly when `isProgrammableBorrower` is set so the forbidden state is unreachable; alternatively let `_stopEpoch` force-clear or fund pending instant claims instead of reverting.

### Proof of Concept
```solidity
// Foundry fork test sketch
// 1. Deploy IdleCDOEpochVariant with programmable borrower + isInterestMinted
// 2. KYC'd attacker deposits AA, epoch starts via startEpoch
// 3. During the window where instant requests are accepted, attacker calls
//    cdoEpoch.requestInstantWithdraw(...) so strategy.pendingInstantWithdraws() > 0
// 4. Warp past epochEndDate; fund borrower; manager calls cdoEpoch.stopEpoch(0, 0)
//    -> reverts NotAllowed inside _checkProgrammableBorrowerMode (line 106)
// 5. Repeat stopEpoch / stopEpochWithDuration indefinitely: always reverts.
//    claimInstantWithdrawRequest cannot clear pendingInstantWithdraws because
//    the receipt was never funded, and getInstantWithdrawFunds is disabled for PB.
//    -> isEpochRunning permanently true; all lender funds frozen.
```