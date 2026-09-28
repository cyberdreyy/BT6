Let me look deeper at the instant withdraw path and default finalization state.### Title
Post-default instant withdraw requests are attributed to `defaultRecoveryEpoch`, letting a user mint a fresh receipt and instantly drain the recovery reserve - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The CVE class is a use-after-free: a freed/stale slot (`instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]`, whose logical lifetime ends at default finalization) is reused for brand-new requests. `IdleCreditVault.requestInstantWithdraw` has no `defaultRecoveryFinalized` branch — unlike `requestWithdraw`, which explicitly routes post-default requests into `postDefaultRequests` (lines 247-257). After `finalizeDefaultRecovery` fixes `defaultRecoveryEpoch` and isolates `defaultRecoveryReserve`, an attacker can call `requestInstantWithdraw`, which writes `instantWithdrawsRequestsByEpoch[_user][epochNumber] += _amount` (line 371). Because `epochNumber` only increments at `stopEpoch` (line 282 comment) and default finalization does not advance it, `epochNumber == defaultRecoveryEpoch`, so the fresh receipt is indexed by the already-"closed" default epoch. A subsequent `claimInstantWithdrawRequest` hits `_claimDefaultedInstantWithdrawRequest` (lines 842-856), which pays `claimBasis * defaultRecoveryPrice / RECOVERY_FULL` directly from `defaultRecoveryReserve` — before the funded-claim path ever runs.

### Finding Description
- `requestWithdraw` guards the post-default case and parks new requests in `postDefaultRequests` so they cannot pollute defaulted-epoch accounting (lines 247-258). `requestInstantWithdraw` (lines 356-375) has no equivalent: it mints a receipt, increments `instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch[_user][epochNumber]`, `instantWithdrawClaimsByEpoch[epochNumber]`, and `pendingInstantWithdraws`.
- `claimInstantWithdrawRequest` (lines 380-392) first calls `_claimDefaultedInstantWithdrawRequest` when `defaultRecoveryFinalized && defaultInstantWithdrawsFinalized`, which reads `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` and pays from the reserve at the finalized haircut price, then continues to the funded path which pays `instantWithdrawsRequests[_user]` — but that remainder was zeroed by the `instantWithdrawsRequests[_user] -= claimBasis` in the defaulted path (line 848), so the burn at line 389 succeeds with a consistent balance.
- Net effect: attacker converts tranche tokens → minted receipt → immediate payout of `amount * defaultRecoveryPrice` from `defaultRecoveryReserve`, consuming `defaultRecoveryReserve -= _amount` (line 915) that is earmarked for legitimate defaulted-epoch claimants. It is a "freed" index (the default epoch bucket) being written to after its lifetime ended.
- Secondary corruption: `pendingInstantWithdraws = claimBasis >= pending ? 0 : pending - claimBasis` (line 852) zeroes the global pending counter, and `instantWithdrawClaimsByEpoch[defaultEpoch]` is decremented, so honest users' claims can later underflow or the reserve check `_transferFundedClaim` (lines 897-906) can revert, permanently freezing real claimants' recovery.

### Impact Explanation
Direct theft of the default recovery reserve proportional to `defaultRecoveryPrice` per unit of tranche tokens burned, plus potential permanent freezing of honest claimants once the reserve is drained or accounting underflows. Loss is bounded by `defaultRecoveryReserve` and the attacker's tranche holdings, but the attacker can hold a large tranche position (any KYC-passing lender qualifies).

### Likelihood Explanation
Requires the pool to have defaulted and `finalizeDefaultRecovery`/`defaultInstantWithdrawsFinalized` to have run while `epochNumber` still equals `defaultRecoveryEpoch` (no intervening `stopEpoch` — defaults typically halt the epoch machine, so this window persists indefinitely) and instant withdrawals remain enabled on the CDO path. I was not able to fully verify the CDO-side gating in `IdleCDOEpochVariant.requestInstantWithdraw` / `claimInstantWithdrawRequest` (the grep did not return the function bodies before the iteration limit); if those functions revert post-default, the attack is blocked and this finding degrades to a defense-in-depth gap. The missing `defaultRecoveryFinalized` guard in `requestInstantWithdraw` — clearly present as an explicit branch in `requestWithdraw` (line 247) — strongly suggests the asymmetry is unintended.

### Recommendation
Mirror the `requestWithdraw` post-default handling in `requestInstantWithdraw`: when `defaultRecoveryFinalized`, revert or route new instant requests into a post-default bucket that never writes `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` / `instantWithdrawClaimsByEpoch[defaultRecoveryEpoch]`. Alternatively, in `_claimDefaultedInstantWithdrawRequest`, snapshot the per-epoch basis at finalization and only honor receipts whose request timestamp/flag predates `defaultRecoveryFinalized`.

### Proof of Concept
Foundry fork PoC (sketch):

```solidity
// setup: attacker holds AA tranche tokens in an epoch-based IdleCreditVault
// 1. borrower defaults; owner/manager finalize default recovery:
//    -> defaultRecoveryFinalized = true, defaultInstantWithdrawsFinalized = true
//    -> defaultRecoveryEpoch = epochNumber (unchanged), defaultRecoveryPrice set
//    -> defaultRecoveryReserve funded

uint256 amount = attackerTrancheBalance;
vm.startPrank(attacker);
// 2. fresh instant withdraw AFTER finalization — no guard blocks this
cdoEpoch.requestInstantWithdraw(amount, address(AAtranche));
//    writes instantWithdrawsRequestsByEpoch[attacker][defaultRecoveryEpoch] = amount

// 3. claim immediately: pays amount * defaultRecoveryPrice / RECOVERY_FULL
//    straight out of defaultRecoveryReserve
cdoEpoch.claimInstantWithdrawRequest();
vm.stopPrank();

// assert attacker received underlying and defaultRecoveryReserve decreased
// assert honest defaulted-epoch claimant now reverts or is underpaid
```

Caveat: the PoC depends on `IdleCDOEpochVariant.requestInstantWithdraw`/`claimInstantWithdrawRequest` not reverting when the vault is in the finalized-default state — that gating could not be confirmed within the available search iterations.