### Title
Post-default instant-withdraw receipts are paid at par from the funded reserve without ever being funded - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.requestInstantWithdraw` does not check `defaultRecoveryFinalized`, unlike `requestWithdraw` which explicitly routes post-default requests through the haircut-aware `postDefaultRequests` path. After `finalizeDefaultRecovery`, an attacker can still open an instant-withdraw request, get a receipt minted 1:1, and immediately claim it at par through `claimInstantWithdrawRequest` — the claim falls through `_claimDefaultedInstantWithdrawRequest` (receipt is keyed to the *current* epoch, not `defaultRecoveryEpoch`) into the funded-claim path that pays the full `instantWithdrawsRequests[_user]` aggregate. The analog to CVE-2025-6555's use-after-free: a receipt minted against an already-finalized (dead) epoch bucket is still honored as if it were live, drawing on reserve/funded underlying it was never backed by.

### Finding Description
In `contracts/strategies/idle/IdleCreditVault.sol:356-375`, `requestInstantWithdraw` burns CDO strategy tokens, mints a user receipt, and increments `instantWithdrawsRequests`, `instantWithdrawsRequestsByEpoch[currentEpoch]`, `instantWithdrawClaimsByEpoch`, and `pendingInstantWithdraws` — with no `defaultRecoveryFinalized` branch. Contrast with `requestWithdraw` at lines 247-257, which after finalization requires a clean state and routes into `postDefaultRequests` paid from the default-recovery reserve.

In `claimInstantWithdrawRequest` (lines 380-392), after `_claimDefaultedInstantWithdrawRequest` clears only the `defaultRecoveryEpoch` bucket, the remaining `instantWithdrawsRequests[_user]` — which now includes the attacker's post-default receipt — is burned and paid in full via `_transferFundedClaim`. Since the pool is defaulted there is no later `stopEpoch`/`collectInstantWithdrawFunds` to fund `pendingInstantWithdraws`; the payout comes from vault-held underlying that backs the default-recovery reserve and other users' matured receipts. The epoch keying also means `instantWithdrawsRequestsByEpoch[user][newEpoch]` is never haircunted, escaping the recovery-price loss applied to genuine defaulted-epoch claimants.

### Impact Explanation
Direct theft: the attacker converts defaulted-epoch strategy tokens (worth `defaultRecoveryPrice < RECOVERY_FULL` to honest holders) into a full par payout. Each round-trip drains underlying reserved for defaulted-epoch claimants and `postDefaultRequests`, breaking the one-receipt-one-funded-payout and loss-waterfall invariants; honest claimants are left with an underfunded reserve (insolvency of the recovery bucket). Quantified loss = up to the full recovery reserve balance, limited by attacker strategy-token holdings.

### Likelihood Explanation
Requires only an unprivileged tranche/strategy-token holder acting after default finalization — a fully permissionless, deterministic sequence (requestInstantWithdraw → claimInstantWithdrawRequest), no privileged role, no timing race. The only uncertainty is whether the CDO-side entry point gates instant requests after default (`allowInstantWithdraw` flag / `defaulted` check in `IdleCDOEpochVariant`), which the searches suggest exists as a flag but could not be fully confirmed within the available iterations; the vault function itself performs no post-default validation, so any CDO path that reaches it is exploitable.

### Recommendation
Mirror `requestWithdraw` in `requestInstantWithdraw`: after `defaultRecoveryFinalized`, either revert, or route the amount into `postDefaultRequests`-style recovery accounting (mint receipt, record basis, exclude from `pendingInstantWithdraws`/`instantWithdrawClaimsByEpoch`). Alternatively, in `claimInstantWithdrawRequest`, settle post-default instant receipts against `defaultRecoveryPrice` via `_transferDefaultRecovery` instead of `_transferFundedClaim`.

### Proof of Concept
Foundry fork PoC sketch (mirroring `test/foundry/IdleCreditVault.t.sol` helpers):

```solidity
// 1. Deposit AA, run epoch 0, borrower defaults; owner/manager calls
//    cdoEpoch.finalizeDefault() / strategy.finalizeDefaultRecovery()
//    so defaultRecoveryFinalized == true and reserve is funded.
// 2. Attacker (fresh KYC'd lender) holds strategy-token receipt value.
vm.startPrank(attacker);
// 3. Open instant withdraw AFTER finalization — should revert but does not.
cdoEpoch.requestInstantWithdraw(attackerAmount, address(AAtranche));
// 4. Claim immediately: defaulted-epoch bucket misses, funded path pays par.
cdoEpoch.claimInstantWithdrawRequest();
// 5. Assert attacker received full `attackerAmount` underlying while
//    honest defaulted-epoch claimants only recover defaultRecoveryPrice.
assertGt(underlying.balanceOf(attacker), attackerAmount * defaultRecoveryPrice / RECOVERY_FULL);
vm.stopPrank();
// 6. Show reserve shortfall: subsequent honest claimWithdrawRequest/
//    _claimPostDefaultWithdrawRequest reverts on insufficient balance
//    or pays less than claimBasis * defaultRecoveryPrice / RECOVERY_FULL.
```

Caveat: exact post-default gating in `IdleCDOEpochVariant.requestInstantWithdraw` (allow-flag / `defaulted` revert) could not be fully verified within the iteration limit; if the CDO hard-reverts all instant requests after default, severity drops to defense-in-depth. The missing guard in the vault is confirmed.