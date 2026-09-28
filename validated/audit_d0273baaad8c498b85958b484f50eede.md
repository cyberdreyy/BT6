### Title
Unfunded instant-withdraw receipts carried across an epoch rollover escape the default haircut and are paid at par - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
Like the kernel bug where enabling edge detection while software debounce is active skips allocating the kfifo, `requestInstantWithdraw` allocates the per-epoch ledger entry only for the *current* `epochNumber` (`instantWithdrawsRequestsByEpoch[_user][currentEpoch]`, `instantWithdrawClaimsByEpoch[currentEpoch]`, `IdleCreditVault.sol:367-372`). If the request remains unfunded while `epochNumber` advances, the receipt is never re-registered under the new epoch — the per-epoch bucket is "uninitialised" for the epoch in which default recovery is later finalized.

### Finding Description
- `requestInstantWithdraw` records the receipt under the epoch active at request time and bumps `pendingInstantWithdraws` (lines 366-374). `pendingInstantWithdraws` is only decreased by `collectInstantWithdrawFunds` (line 401); nothing clears or migrates the per-epoch entries when `stopEpoch`/`startEpoch` bump `epochNumber`.
- `defaultPendingClaimBasis` (lines 644-649) adds only `instantWithdrawClaimsByEpoch[epochNumber]` — the *default* epoch. A receipt opened in epoch N that is still unfunded when default finalizes in epoch N+1 is excluded from `basis`, so the recovery denominator omits it while `pendingInstantWithdraws != 0` still sets `defaultInstantWithdrawsFinalized = true` (line 696).
- At claim time `_claimDefaultedInstantWithdrawRequest` reads `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` (line 844), which is 0 for the epoch-N receipt, so it returns early without clearing or paying anything. `claimInstantWithdrawRequest` (lines 387-392) then falls through, burns the entire `instantWithdrawsRequests[_user]`, and calls `_transferFundedClaim(_user, amount)` — paying the *full par amount* from strategy-held underlying instead of the recovery reserve at `defaultRecoveryPrice`.

### Impact Explanation
Direct theft / insolvency: the attacker receives 100% of the receipt's face value while every other defaulted claimant is haircut to `defaultRecoveryPrice`. The payout comes via `_transferFundedClaim` (lines 897+), which spends general strategy balance rather than the sized `defaultRecoveryReserve`, so it drains funds backing other users' funded claims and the recovery reserve, breaking the one-receipt-one-payout and loss-waterfall invariants. Loss equals `receipt * (1 - recoveryPrice)` per attacking receipt, bounded only by how much the attacker can route through instant withdraws.

### Likelihood Explanation
Requires a sequence around honest actors: attacker requests an instant withdraw late in epoch N; it stays unfunded or only partially funded (the code comments at lines 636-640 and 713-722 explicitly acknowledge `pendingInstantWithdraws` can remain non-zero across funding); the epoch rolls to N+1; the borrower then defaults and the manager finalizes recovery. No guard stops it: `_claimDefaultedInstantWithdrawRequest` silently returns 0, and `claimInstantWithdrawRequest` has no epoch check equivalent to the `epochNumber <= lastWithdrawRequest` gate used for normal withdraws (line 326). Uncertainty: whether CDO stop-epoch logic always forces full funding of pending instant requests was not fully verified; if `getInstantWithdrawFunds` always settles them, the window shrinks to epochs where instant funding is partial, which the code itself treats as reachable.

### Recommendation
In `finalizeDefaultRecovery`/`defaultPendingClaimBasis`, aggregate *all* outstanding instant receipts (or track per-user receipts independent of epoch), and in `claimInstantWithdrawRequest` revert or route through the recovery path whenever `defaultInstantWithdrawsFinalized` is set and the user has an uncleared receipt — e.g. check `instantWithdrawsRequests[_user]` against all epochs, not only `defaultRecoveryEpoch`, mirroring the `lastWithdrawRequest` epoch guard for normal withdraws.

### Proof of Concept
Foundry fork sketch (mirroring `test/foundry/IdleCreditVault.t.sol` setup):

```solidity
// epoch N running; attacker holds AA tranche tokens
cdoEpoch.requestInstantWithdraw(ATTACK_AMOUNT, AAtranche); // attacker
// instant queue only partially funded -> pendingInstantWithdraws > 0
// honest manager stops epoch N, starts epoch N+1 (epochNumber bumps to N+1)
cdoEpoch.stopEpoch(apr, 0);
cdoEpoch.startEpoch();
// borrower defaults honestly; manager finalizes recovery at price < RECOVERY_FULL
cdoEpoch.finalizeDefault(recovered, source); // -> finalizeDefaultRecovery
// attacker claims: defaulted-path lookup hits epoch N+1 bucket == 0,
// falls through to _transferFundedClaim and pays ATTACK_AMOUNT at par
uint256 balPre = underlying.balanceOf(attacker);
cdoEpoch.claimInstantWithdrawRequest(); // attacker
assertEq(underlying.balanceOf(attacker) - balPre, ATTACK_AMOUNT); // no haircut
// assert recovery reserve is now short vs. other claimants' basis
```