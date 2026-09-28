### Title
Post-default instant withdrawals corrupt `instantWithdrawClaimsByEpoch[defaultRecoveryEpoch]`, permanently freezing other users' default-recovery claims - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.requestInstantWithdraw` keys receipts by `epochNumber`, which still equals `defaultRecoveryEpoch` after `finalizeDefaultRecovery`. A post-default instant request therefore writes into the same per-epoch slot used by `_claimDefaultedInstantWithdrawRequest`, which then decrements `instantWithdrawClaimsByEpoch[defaultEpoch]` and pays from `defaultRecoveryReserve`. Once post-default receipts have consumed that aggregate, legitimate default-epoch instant claimants hit an underflow revert in `instantWithdrawClaimsByEpoch[defaultEpoch] -= claimBasis` and their recovery is frozen forever.

### Finding Description
Analog of the HDF5 out-of-bounds write in `H5FS__sect_find_node`: here the "index" is the epoch key and the boundary confusion is writing post-default receipts into the already-finalized default epoch bucket.

- `requestInstantWithdraw` records `instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount` and `instantWithdrawClaimsByEpoch[currentEpoch] += _amount` where `currentEpoch = epochNumber` (`contracts/strategies/idle/IdleCreditVault.sol:367-372`). After `finalizeDefaultRecovery` sets `defaultRecoveryEpoch = epochNumber` (line 693), `epochNumber` does not advance because no new epoch can start on a defaulted vault, so post-default instant receipts land on `defaultRecoveryEpoch`.
- `claimInstantWithdrawRequest` first calls `_claimDefaultedInstantWithdrawRequest` whenever `defaultRecoveryFinalized && defaultInstantWithdrawsFinalized` (lines 382-386). That function reads `instantWithdrawsRequestsByEpoch[_user][defaultEpoch]`, subtracts it from `instantWithdrawsRequests[_user]`, and critically does `instantWithdrawClaimsByEpoch[defaultEpoch] -= claimBasis` (lines 844-853).
- `instantWithdrawClaimsByEpoch[defaultEpoch]` is the shared aggregate of default-epoch instant claims. A post-default requester mints receipts, and on claim decrements this shared aggregate by an amount that was never part of the defaulted basis. After one or more post-default claims, the aggregate can be smaller than a legitimate default-epoch claimant's `claimBasis`, so the subtraction underflows and `claimInstantWithdrawRequest` reverts permanently.
- Additionally, post-default instant receipts are paid `claimBasis * defaultRecoveryPrice / RECOVERY_FULL` from `defaultRecoveryReserve`, draining reserve that belongs to default-epoch claimants even though the post-default requester paid full basis into `pendingInstantWithdraws`.

No guard prevents this: `requestInstantWithdraw` has no `defaultRecoveryFinalized` branch (unlike `requestWithdraw`, which routes post-default requests to `postDefaultRequests` at lines 247-257), and nothing separates post-default instant receipts from the default-epoch bucket.

### Impact Explanation
Direct theft plus permanent freezing of unclaimed yield. Post-default instant claimants are paid from `defaultRecoveryReserve` at the recovery haircut, and each such claim shrinks `instantWithdrawClaimsByEpoch[defaultEpoch]` below the remaining real default-epoch basis. Every subsequent legitimate default-epoch instant claim reverts on underflow, permanently locking the corresponding reserve in the strategy. Loss is bounded by `defaultRecoveryReserve` allocated to instant-epoch claimants — potentially the entire remaining reserve.

### Likelihood Explanation
Requires the instant-withdraw mode to be enabled and `defaultInstantWithdrawsFinalized == true` (i.e., `pendingInstantWithdraws != 0` at finalization), and a post-default instant request reaching the strategy via the CDO. I could not fully verify the CDO-side gating for instant requests after `defaulted()` is set — if `IdleCDOEpochVariant.requestInstantWithdraw` reverts when defaulted, this path is unreachable and the finding reduces to a latent accounting bug. The strategy-level write/read asymmetry is confirmed in code.

### Recommendation
In `requestInstantWithdraw`, handle `defaultRecoveryFinalized` like `requestWithdraw` does (route to `postDefaultRequests` or revert), and/or key post-default instant receipts to a separate bucket so they never share `instantWithdrawClaimsByEpoch[defaultRecoveryEpoch]`. Alternatively, in `claimInstantWithdrawRequest`, cap the defaulted-epoch claim at the pre-finalization basis tracked separately from post-default requests.

### Proof of Concept
A Foundry fork test would:
1. Enable instant withdrawals; have user A request an instant withdraw; let the epoch default with `pendingInstantWithdraws > 0`; call `finalizeDefaultRecovery` so `defaultInstantWithdrawsFinalized == true` and `defaultRecoveryEpoch == epochNumber`.
2. Post-default, have attacker B call `requestInstantWithdraw` via the CDO (assuming the CDO forwards it); this increments `instantWithdrawsRequestsByEpoch[B][defaultRecoveryEpoch]` and `instantWithdrawClaimsByEpoch[defaultRecoveryEpoch]`.
3. B calls `claimInstantWithdrawRequest`; `_claimDefaultedInstantWithdrawRequest` burns B's receipt, pays B `claimBasis * defaultRecoveryPrice` from reserve, and decrements the shared aggregate.
4. A calls `claimInstantWithdrawRequest`; `instantWithdrawClaimsByEpoch[defaultEpoch] -= claimBasis` underflows (aggregate already consumed by B's post-default claim), reverting. A's recovery is permanently frozen while B extracted reserve funds.

Caveat: step 2 depends on CDO-side behavior after default, which I could not confirm within the tool budget; if the CDO blocks instant requests once `defaulted()`, this finding is not exploitable and should be rejected.