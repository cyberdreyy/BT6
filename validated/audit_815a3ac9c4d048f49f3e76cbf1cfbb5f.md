### Title
Unfunded instant-withdraw receipts from a prior epoch are excluded from default-recovery basis and paid at par, draining underlyings reserved for funded withdraw claimants — (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
Analogous to the Incus nil-deref (config assumed populated but missing), `finalizeDefaultRecovery` computes the default-epoch instant-claim basis via `instantWithdrawClaimsByEpoch[epochNumber]` and `_defaultPrefundedInstantReserve`, silently assuming all outstanding instant receipts were recorded in the *current* `epochNumber`. `epochNumber` is incremented inside `deposit()` during every successful `stopEpoch` (`IdleCreditVault.sol:607-610`), so instant receipts that survived a stop only partially funded keep `pendingInstantWithdraws > 0` but live under a stale epoch key. On a later default, they contribute zero to `totalBasis` and zero prefunded reserve, yet `defaultInstantWithdrawsFinalized` is still set true. Claiming then falls through `_claimDefaultedInstantWithdrawRequest` (which reads `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` = 0) into the funded path, which burns the receipt and calls `_transferFundedClaim` for the full par amount — paid out of underlyings that belong to older funded normal withdraw receipts, without any recovery haircut.

### Finding Description
- `defaultPendingClaimBasis` adds `instantWithdrawClaimsByEpoch[epochNumber]` only for the current epoch (`IdleCreditVault.sol:644-649`).
- `_defaultPrefundedInstantReserve` likewise computes `instantBasis - pendingInstant` using only the current-epoch bucket (`IdleCreditVault.sol:716-723`).
- `finalizeDefaultRecovery` sets `defaultInstantWithdrawsFinalized = pendingInstantWithdraws != 0` and stores `defaultRecoveryEpoch = epochNumber` (`IdleCreditVault.sol:690-696`).
- `claimInstantWithdrawRequest` then calls `_claimDefaultedInstantWithdrawRequest`, which only clears `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` (`IdleCreditVault.sol:842-856`); for a stale-epoch receipt this returns 0, and the function proceeds to burn `instantWithdrawsRequests[_user]` and transfer the full amount via `_transferFundedClaim` (`IdleCreditVault.sol:387-392`), which only guards `defaultRecoveryReserve`, not the funded-receipt pool.

Sequence: epoch N — user calls `requestInstantWithdraw`, borrower funding at `stopEpoch` covers only part of the instant queue (`collectInstantWithdrawFunds` reduces `pendingInstantWithdraws` partially), and the stop-time `deposit()` bumps `epochNumber` to N+1. In epoch N+1 the borrower defaults; `finalizeDefaultRecovery` sees `instantWithdrawClaimsByEpoch[N+1] == 0`, excludes the receipt from `totalBasis`, adds no prefunded reserve, yet marks instant withdrawals finalized. The attacker then claims at par from the strategy's underlying balance, consuming funds backing other users' funded `withdrawsRequests` (or reverting once drained, permanently freezing the receipt).

### Impact Explanation
An unfunded instant-withdraw receipt is paid 1:1 instead of the `defaultRecoveryPrice` haircut, directly spending underlyings reserved for funded withdraw claimants (the `_transferFundedClaim` reserve check only protects `defaultRecoveryReserve`). Quantified loss equals the stale-epoch instant receipt amount, up to the full funded-receipt balance held by the strategy; alternatively, if the balance is insufficient, the user's receipt is permanently unclaimable.

### Likelihood Explanation
Requires only that an instant withdraw request remain partially unfunded across one `stopEpoch` (a normal liquidity condition — `pendingInstantWithdraws` is explicitly designed to carry an unfunded remainder) followed by a borrower default in a later epoch. The attacker is an ordinary KYC'd lender/tranche holder requesting an instant withdrawal; no privileged misbehavior is needed.

### Recommendation
Aggregate instant claim basis across epochs (e.g., track a global `instantWithdrawClaimsTotal`, or record receipts' epoch so finalization can iterate/sum), or key `instantWithdrawClaimsByEpoch` lookups off the epoch(s) that actually contain receipts rather than assuming `epochNumber`. At minimum, `finalizeDefaultRecovery` should revert (or include all outstanding `pendingInstantWithdraws` basis) when `pendingInstantWithdraws != 0` but `instantWithdrawClaimsByEpoch[epochNumber] == 0`, mirroring the missing `srcBackup.Config != nil` check in the Incus fix.

### Proof of Concept
Foundry fork outline (against `test/foundry/IdleCreditVault.t.sol` harness):
1. Deposit via `idleCDO.depositAA`/`depositBB`; `startEpoch`.
2. User calls `cdoEpoch.requestInstantWithdraw`; fund borrower so `stopEpoch` collects less than `pendingInstantWithdraws` (partial `collectInstantWithdrawFunds`); call `stopEpoch` — `epochNumber` increments to N+1.
3. In epoch N+1, underfund the borrower and call `stopEpoch` to trigger `_handleBorrowerDefault`, then `finalizeDefault`/`finalizeDefaultRecovery` with a nonzero recovery amount.
4. Assert `defaultInstantWithdrawsFinalized() == true` and `instantWithdrawClaimsByEpoch(defaultRecoveryEpoch) == 0` while `instantWithdrawsRequests(user) > 0`.
5. Call `cdoEpoch.claimInstantWithdrawRequest()` for the user: observe a par payout drawn from the strategy's underlying balance that was backing another user's funded `withdrawsRequests`, and that user's subsequent `claimWithdrawRequest` reverting or underpaying.