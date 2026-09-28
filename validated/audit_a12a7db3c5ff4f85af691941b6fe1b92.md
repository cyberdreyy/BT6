### Title
Stale-epoch instant-withdraw receipts escape default recovery haircut via epoch-keyed accounting mismatch - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
Analogous to the EROFS bug — where a task reads an index (`z_erofs_gbuf_id()`) and later acts on state indexed by a *different* CPU after migration — `IdleCreditVault` writes instant-withdraw receipts under `instantWithdrawsRequestsByEpoch[user][epochNumber]` / `instantWithdrawClaimsByEpoch[epochNumber]` at request time, but default-recovery finalization reads them back under whatever `epochNumber` is current at finalization. If `epochNumber` advanced between request and finalization, the unfunded receipt is invisible to the recovery accounting yet still payable at par.

### Finding Description
`requestInstantWithdraw` records receipts per-epoch but tracks the unfunded total globally:

```solidity
// contracts/strategies/idle/IdleCreditVault.sol:366-374
instantWithdrawsRequests[_user] += _amount;
uint256 currentEpoch = epochNumber;
instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
instantWithdrawClaimsByEpoch[currentEpoch] += _amount;
pendingInstantWithdraws += _amount;
```

`pendingInstantWithdraws` is only reduced by `collectInstantWithdrawFunds` (line 401), so an instant receipt that the borrower never funded survives across `stopEpoch`/`startEpoch`, which increment `epochNumber` via `deposit` (line 610).

At default finalization, both the claim basis and the prefunded-reserve calculation read **only the current epoch key**:

```solidity
// :644-649
function defaultPendingClaimBasis() public view returns (uint256 basis) {
  basis = pendingWithdraws;
  if (pendingInstantWithdraws != 0) {
    basis += instantWithdrawClaimsByEpoch[epochNumber];
  }
}

// :716-723
uint256 instantBasis = instantWithdrawClaimsByEpoch[epochNumber];
```

A stale (epoch N) unfunded instant receipt when finalization runs in epoch N+1 is therefore:
- excluded from `totalBasis` (not haircutted, not funded by the reserve), while
- `defaultInstantWithdrawsFinalized = pendingInstantWithdraws != 0` is still set true (line 696).

Post-finalization, `_claimDefaultedInstantWithdrawRequest` clears only `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` (the N+1 key, line 844), so the epoch-N balance remains in `instantWithdrawsRequests[_user]` and `claimInstantWithdrawRequest` pays it at par via `_transferFundedClaim` (line 392). The reserve guard at lines 900-904 only protects `defaultRecoveryReserve`; any non-reserve underlyings held by the strategy are drained at 100% while haircutted claimants get `defaultRecoveryPrice`. If no non-reserve balance exists, the claim reverts forever — permanent freezing of the receipt.

### Impact Explanation
Broken invariant: loss waterfall / "one receipt one haircut". A holder of an unfunded instant receipt from a prior epoch either (a) is paid at par from non-reserve strategy funds — direct theft relative to defaulted claimants who receive only `defaultRecoveryPrice` — or (b) is permanently frozen in `claimInstantWithdrawRequest` despite `pendingInstantWithdraws` still counting the receipt as a live liability. Quantified loss equals the stale receipt amount times `(1 - defaultRecoveryPrice)` in case (a), or the full claim in case (b).

### Likelihood Explanation
Requires: instant withdrawals enabled, a user requesting instant withdraw that remains unfunded (`pendingInstantWithdraws` stays nonzero — no borrower funding via `getInstantWithdrawFunds`/`collectInstantWithdrawFunds`), an epoch rollover (`stopEpoch` then `startEpoch`), then borrower default and `finalizeDefaultRecovery`. All steps use honest privileged actors for sequencing and an unprivileged instant-withdraw requester as the beneficiary, consistent with the threat model. The one unverified assumption is whether `startEpoch` force-funds all `pendingInstantWithdraws` from CDO cash; if it does, the window narrows to cases where available cash is insufficient to cover the queue (partially prefunded case the code explicitly contemplates at lines 638-640 and 714).

### Recommendation
In `defaultPendingClaimBasis`, `_defaultPrefundedInstantReserve`, and `_claimDefaultedInstantWithdrawRequest`, account for instant receipts by the global aggregate (`instantWithdrawsRequests[_user]` / total `instantWithdrawClaimsByEpoch` across epochs, or maintain a global `instantWithdrawClaimsTotal`) rather than `instantWithdrawClaimsByEpoch[epochNumber]`. Alternatively, decay `pendingInstantWithdraws` and migrate per-epoch receipt keys forward at each `epochNumber` increment so the write key and read key cannot diverge.

### Proof of Concept
```solidity
// Foundry fork PoC sketch (test/foundry/)
// 1. Standard epoch variant, instant withdrawals enabled:
//    cdoEpoch.setInstantWithdrawParams(delay, minAprDiff, false)
// 2. Epoch N: attacker deposits AA, requests instant withdraw of X.
//    - requestInstantWithdraw records instantWithdrawsRequestsByEpoch[attacker][N] = X
//    - pendingInstantWithdraws = X; borrower never funds (collectInstantWithdrawFunds not called)
// 3. stopEpoch + startEpoch -> strategy.epochNumber() == N+1, pendingInstantWithdraws still X
// 4. Borrower defaults; finalizeDefaultRecovery runs:
//    - defaultPendingClaimBasis() reads instantWithdrawClaimsByEpoch[N+1] == 0
//      -> X excluded from totalBasis and from reserve funding
//    - defaultInstantWithdrawsFinalized = true (pendingInstantWithdraws != 0)
// 5. claimInstantWithdrawRequest(attacker):
//    - _claimDefaultedInstantWithdrawRequest clears instantWithdrawsRequestsByEpoch[attacker][N+1] == 0
//    - instantWithdrawsRequests[attacker] still == X -> _transferFundedClaim pays X at par
//      from any non-reserve balance (theft vs haircutted claimants),
//      or reverts permanently if balance <= defaultRecoveryReserve (freeze).
```