### Title
Loss-adjusted withdraw receipts escape the haircut and are paid at par once the user files a later withdraw request - (contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The analog of "secret retained in memory after it should have been cleared" is a **loss-bearing withdraw receipt retained in `withdrawsRequests`/`withdrawsRequestsByEpoch` after it should have been haircutted**. `_claimLossAdjustedWithdrawRequest` only inspects `lastWithdrawRequest[_user]` — a single-slot marker that a subsequent `requestWithdraw` overwrites. Once overwritten, the loss-epoch receipt is never cleared through the loss path and is paid out at par by `_claimFundedWithdrawRequest`, stealing from the funded reserve/other claimants.

### Finding Description
When the borrower repays only part of the epoch obligation, `stopEpochWithDuration(_lossAmount)` funds the vault with a loss-adjusted amount and records a per-epoch haircut in `lossRecoveryPriceByEpoch[epoch]`. Users who requested withdrawals in that epoch are supposed to claim `claimBasis * lossRecoveryPrice / RECOVERY_FULL` via `_claimLossAdjustedWithdrawRequest` (contracts/strategies/idle/IdleCreditVault.sol:789-801).

That path derives the loss epoch exclusively from `lastWithdrawRequest[_user]` (IdleCreditVault.sol:790). However, `requestWithdraw` unconditionally overwrites `lastWithdrawRequest[_user] = currentEpoch` and *adds* the new amount into the aggregate `withdrawsRequests[_user]` without clearing the old per-epoch entry (IdleCreditVault.sol:282-293). The protocol explicitly supports stacked requests — the comment in `_claimFundedWithdrawRequest` notes a user "will have to wait for another epoch to claim both requests" (IdleCreditVault.sol:323-324).

So a user who holds a loss-epoch receipt can simply submit a second `requestWithdraw` in the next epoch. On the subsequent claim:

1. `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]] == 0` → `_claimLossAdjustedWithdrawRequest` returns 0 and the stale loss-epoch entry in `withdrawsRequestsByEpoch[_user][lossEpoch]` is never cleared.
2. `_claimFundedWithdrawRequest` pays `withdrawsRequests[_user]` — which still aggregates the loss-epoch amount — **at par** (IdleCreditVault.sol:338-349).

The broken invariant is the loss waterfall: the vault was only funded `claimBasis * lossRecoveryPrice` for that epoch, but the claim drains `claimBasis`. The shortfall is socialized onto other claimants' funded reserve, and the last claimants' transactions revert on insufficient balance.

### Impact Explanation
Direct theft/insolvency. A withdrawer with a defaulted-by-loss receipt recovers 100% instead of `lossRecoveryPrice` percent. The excess is paid from underlyings reserved for other epochs' funded receipts, so honest claimants are left unclaimable (permanent freezing of their payout). Loss scales with `1 - lossRecoveryPrice` times the receipt size.

### Likelihood Explanation
Requires a `stopEpochWithDuration` loss event (partial borrower repayment — a normal, non-privileged-attack protocol flow), and the attacker merely calls `requestWithdraw` again in the following epoch — an ordinary user action explicitly supported by the design. No privileged cooperation needed; any tranche holder can do this. Gating on `_hasWithdrawRequest` is not applied in `requestWithdraw`.

### Recommendation
Don't key loss-adjusted claims solely off the mutable `lastWithdrawRequest`. Either iterate the user's non-zero `withdrawsRequestsByEpoch` epochs and apply any nonzero `lossRecoveryPriceByEpoch[epoch]` haircut before falling back to the funded path, or sweep all epochs with a recorded `lossRecoveryPriceByEpoch` inside `_claimFundedWithdrawRequest` before paying `withdrawsRequests[_user]` at par. Equivalently, block `requestWithdraw` while an unclaimed receipt exists in an epoch with `lossRecoveryPriceByEpoch[epoch] != 0`.

### Proof of Concept
Foundry fork PoC outline (not executed — tool iterations exhausted):

```solidity
// setup: deposit as attacker, startEpoch, requestWithdraw(attackerAmount)
// epoch N ends with borrower repaying only part -> manager calls
// cdoEpoch.stopEpochWithDuration(...) causing strategy to record
// lossRecoveryPriceByEpoch[N] < RECOVERY_FULL and fund haircutted amount.
// epoch N+1 starts (buffer/startEpoch).
// attack:
vm.prank(attacker);
cdoEpoch.requestWithdraw(x, address(AAtranche)); // overwrites lastWithdrawRequest
// wait one epoch boundary so funded-claim gating passes
vm.warp(...); // epochNumber > lastWithdrawRequest[attacker]
vm.prank(attacker);
cdoEpoch.claimWithdrawRequest();
// assert attacker received (lossReceipt + x) at par instead of
// lossReceipt * lossRecoveryPriceByEpoch[N] / RECOVERY_FULL + x
// assert a second honest claimant's claimWithdrawRequest() reverts or is shortchanged
```

Uncertainty: I verified the claim/clear logic in `IdleCreditVault.sol:271-350` and `789-837`, but did not fully read `stopEpochWithDuration`/`previewLossAdjustedWithdrawFunds` in `IdleCDOEpochVariant.sol` to confirm the funded amount equals exactly the haircutted total; the PoC should assert that funding assumption first.