### Title
Stale defaulted-epoch instant-withdraw receipt paid at par before `defaultInstantWithdrawsFinalized` — double-spend of the recovery haircut (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The io_uring bug class is "free a registered resource while a stale reference still permits use". In `IdleCreditVault` the analogous pattern exists in `claimInstantWithdrawRequest`: once `defaultRecoveryFinalized` is set but before instant withdrawals are finalized (`defaultInstantWithdrawsFinalized == false`), the function skips `_claimDefaultedInstantWithdrawRequest` and falls through to the funded path, paying `instantWithdrawsRequests[_user]` **at par** via `_transferFundedClaim` while leaving `instantWithdrawsRequestsByEpoch[_user][defaultEpoch]` and `instantWithdrawClaimsByEpoch[defaultEpoch]` populated — a stale, already-spent receipt still referenced by the default-accounting buckets.

### Finding Description
In `claimInstantWithdrawRequest` (contracts/strategies/idle/IdleCreditVault.sol:380-393):

```solidity
if (defaultRecoveryFinalized && defaultInstantWithdrawsFinalized) {
    _claimDefaultedInstantWithdrawRequest(_user);
}
uint256 amount = instantWithdrawsRequests[_user];
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);
```

When `defaultRecoveryFinalized == true` but `defaultInstantWithdrawsFinalized == false` (the two flags are set in different finalization steps, so a window exists), a user whose instant receipt was created in `defaultRecoveryEpoch` can call `claimInstantWithdrawRequest` through the CDO. The receipt is burned and paid at **full par value** instead of `claimBasis * defaultRecoveryPrice / RECOVERY_FULL`, and crucially `instantWithdrawsRequestsByEpoch[_user][defaultEpoch]` is **not** cleared and `instantWithdrawClaimsByEpoch[defaultEpoch]` is **not** decremented (contrast `_claimDefaultedInstantWithdrawRequest` at lines 842-856, which clears both). The aggregate `instantWithdrawsRequests[_user]` is zeroed but the per-epoch record remains — the "freed" receipt still has a live reference.

### Impact Explanation
- Direct theft: the attacker receives `claimBasis` instead of `claimBasis * defaultRecoveryPrice / RECOVERY_FULL`. With a 50% recovery price and a 100k USDC receipt, that is ~50k USDC overpaid from vault underlyings that back other claimants/LPs (the `_transferFundedClaim` reserve guard only protects `defaultRecoveryReserve`, not the general funded-claim balance).
- Corrupted accounting: after `defaultInstantWithdrawsFinalized` is set, the leftover `instantWithdrawsRequestsByEpoch[_user][defaultEpoch]` causes `_claimDefaultedInstantWithdrawRequest` to execute `instantWithdrawsRequests[_user] -= claimBasis`, which underflows and reverts — the stale record permanently bricks that user's claim path and leaves `instantWithdrawClaimsByEpoch`/`pendingInstantWithdraws` inconsistent with reality.

### Likelihood Explanation
Requires (a) the pool in defaulted state with `defaultRecoveryFinalized == true`, (b) instant-withdraw finalization pending, and (c) sufficient non-reserve underlying balance in the strategy so `_transferFundedClaim` does not revert. Any unprivileged tranche holder with a pending instant receipt in the default epoch qualifies; no privileged cooperation is needed. The pre-default funded-claim reserve check does not stop it because the overpayment comes from ordinary funded balance.

Caveat: I could not fully verify the exact ordering/atomicity of `defaultRecoveryFinalized` vs `defaultInstantWithdrawsFinalized` being set (in `finalizeDefault`/`finalizeDefaultRecovery` in `IdleCDOEpochVariant.sol`/`IdleCreditVault.sol`) within the available search iterations. If both flags are always set in a single transaction, the window collapses and the finding degrades to a defense-in-depth gap; if instant finalization is a separate manager transaction (as the split-flag design suggests), the attack path is live.

### Recommendation
In `claimInstantWithdrawRequest`, when `defaultRecoveryFinalized && !defaultInstantWithdrawsFinalized`, either revert for users with a nonzero `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` (forcing them to wait for instant finalization) or route them through `_claimDefaultedInstantWithdrawRequest` immediately. At minimum, always clear `instantWithdrawsRequestsByEpoch[_user][epoch]` and decrement `instantWithdrawClaimsByEpoch[epoch]` whenever the aggregate receipt is consumed, so no stale per-epoch reference survives a claim.

### Proof of Concept
A reproducible Foundry fork test would:

1. Deposit via `cdoEpoch.depositAA`, enable instant withdrawals (`setInstantWithdrawParams`), and call `requestInstantWithdraw` so the receipt lands in epoch N.
2. Drive the pool to default: `stopEpoch` then `_checkDefault()` path → `finalizeDefault(recovered, manager)` with `recoveryRatio < 1e18`, asserting `defaultRecoveryFinalized == true` while `defaultInstantWithdrawsFinalized == false`.
3. `vm.prank(user); cdoEpoch.claimInstantWithdrawRequest();`
4. Assert received `amount == instantWithdrawsRequests` (par) rather than `amount * defaultRecoveryPrice / RECOVERY_FULL`, and assert `instantWithdrawsRequestsByEpoch(user, defaultRecoveryEpoch)` is still nonzero.
5. After the manager finalizes instant withdrawals, call `claimInstantWithdrawRequest` again and observe the underflow revert in `_claimDefaultedInstantWithdrawRequest`.

Base it on the existing harness in `test/foundry/IdleCreditVault.t.sol` (e.g. `testFinalizeDefaultHaircutsPendingInstantRedeems`, line ~4435), inserting the claim between the two finalization steps.

Relevant code: `claimInstantWithdrawRequest` contracts/strategies/idle/IdleCreditVault.sol:380-393; `_claimDefaultedInstantWithdrawRequest` contracts/strategies/idle/IdleCreditVault.sol:842-856; `_transferFundedClaim` reserve guard contracts/strategies/idle/IdleCreditVault.sol:897-907.