### Title
Per-epoch loss haircut is keyed only to `lastWithdrawRequest`, so older receipts stored under earlier epoch keys evade the haircut — the haircut delta is stranded in the vault and the user is overpaid at par (File: `contracts/strategies/idle/IdleCreditVault.sol`)

### Summary
`IdleCreditVault.collectWithdrawFunds` computes one `lossRecoveryPrice` over the *aggregate* `pendingWithdraws` basis, which includes receipts a user accumulated across multiple epochs (`withdrawsRequestsByEpoch[user][E1]`, `[E2]`, ...). However the loss-adjusted claim path only ever looks up and clears the epoch stored in `lastWithdrawRequest[user]`. Receipts recorded under older epoch keys are never marked loss-adjusted, so after the haircut they are still paid at par by `_claimFundedWithdrawRequest`. The slot-confusion analog: the haircut is written under a single epoch key while the claim basis it applies to is spread over several epoch keys — the mapping between "haircutted basis" and "claim epoch" is corrupted, exactly like the pump writing to `slot + maxI` instead of `slot + maxI*32`.

### Finding Description
- `requestWithdraw` records `withdrawsRequestsByEpoch[_user][currentEpoch] += _amount` and overwrites `lastWithdrawRequest[_user] = currentEpoch`, without clearing earlier epochs' entries or `withdrawsRequests[_user]` (`IdleCreditVault.sol:282-294`).
- On a stop with `_lossAmount > 0`, `_stopEpoch` calls `previewLossAdjustedWithdrawFunds` which haircuts the full `pendingWithdraws` aggregate, then `collectWithdrawFunds(_pendingWithdraws)` stores `lossRecoveryPriceByEpoch[epochNumber] = _amount * 1e18 / pendingBasis` and zeroes `pendingWithdraws` (`IdleCreditVault.sol:411-421`, `IdleCDOEpochVariant.sol:393-410`).
- On claim, `_claimLossAdjustedWithdrawRequest` reads `lossEpoch = lastWithdrawRequest[_user]` and `_clearWithdrawClaimForEpoch(_user, lossEpoch)` only subtracts `withdrawsRequestsByEpoch[_user][lossEpoch]` from the aggregate (`IdleCreditVault.sol:789-801`, `811-837`).
- The remaining balance of `withdrawsRequests[_user]` — which contains older-epoch receipt amounts that were *also* haircutted in the funding price — then flows to `_claimFundedWithdrawRequest`, which pays them at par because `epochNumber > lastWithdrawRequest[_user]` (reset to 0) and `_transferFundedClaim` only guards against the default-recovery reserve, not against already-haircut basis (`IdleCreditVault.sol:312-349`, `897-907`).

### Impact Explanation
Concretely: a user holds a funded receipt `W1` from epoch E1 and a new receipt `W2` from epoch E2. A loss stop funds `price*(W1+W2)` for the aggregate. The user claims `W2*price` (loss-adjusted) **plus** `W1` at par — i.e., the user escapes the haircut on `W1` even though the borrower was only asked to fund `price*W1` for it. The `price*W1` portion of the new funding has no claimant (`pendingWithdraws` is zeroed, all per-epoch entries cleared) and is permanently stranded in the strategy as unclaimable underlying — theft/permanent freezing of unclaimed yield. The invariant "one receipt → haircutted payout" is broken: the same receipt basis is simultaneously haircutted (in funding) and paid at par (in claim).

### Likelihood Explanation
- Triggerable by any KYC-passing lender: request a withdraw, let it be funded at stop, *don't claim*, request again in a later epoch. `requestWithdraw` only reverts if the *last* epoch has a nonzero `lossRecoveryPrice` (`IdleCreditVault.sol:263-271`), so stacking receipts across epochs is unrestricted.
- The loss stop itself requires manager `stopEpochWithDuration(_lossAmount > 0)` — an honest but rare operation; the attacker simply positions beforehand and waits, so no privileged collusion is needed.
- Guard `lossRecoveryPrice == 0` checks in `requestWithdraw` do not help: they check the *previous* `lastWithdrawRequest` epoch, which had no loss.

### Recommendation
Either (a) restrict `requestWithdraw` when `withdrawsRequests[_user] != 0` (or when any per-epoch entry exists), forcing users to claim before stacking a new receipt — mirroring the existing loss-adjusted guard — or (b) store the loss recovery price against a global "loss round" index (e.g., increment a `lossRecoveryRound`) rather than an epoch key, and have `_claimFundedWithdrawRequest` apply the haircut to the entire aggregate `withdrawsRequests[_user]` when the claim spans a loss round.

### Proof of Concept
Foundry fork PoC sketch (USDC, standard `IdleCDOEpochVariant` + `IdleCreditVault`, non-APR0 mode):

```solidity
// setup: user deposits AA, epoch 1 starts, user requestWithdraw -> W1
// stopEpoch (no loss): W1 funded; strategy.pendingWithdraws() == 0 after collect,
//   but withdrawsRequests[user] == W1 and withdrawsRequestsByEpoch[user][E1] == W1
// user does NOT claim.
// buffer: user deposits more / uses second tranche position, requestWithdraw -> W2 (epoch E2)
//   withdrawsRequests[user] = W1 + W2; lastWithdrawRequest[user] = E2
// epoch E2 ends; manager calls stopEpochWithDuration(apr, interest, dur, lossAmount)
//   where lossAmount makes pendingBasis = W1 + W2 haircut to price P < 1e18
//   -> lossRecoveryPriceByEpoch[E2] = P; strategy receives P*(W1+W2)
// user calls cdoEpoch.claimWithdrawRequest():
//   _claimLossAdjustedWithdrawRequest pays W2*P, clears only byEpoch[E2]
//   _claimFundedWithdrawRequest pays withdrawsRequests[user] == W1 at par
// assertEq(received, W1 + W2*P)  // instead of P*(W1+W2)
// assertGt(underlying.balanceOf(strategy), leftover)  // P*W1 stranded, pendingWithdraws == 0
```

Uncertainty: I verified the write/read paths for `lossRecoveryPriceByEpoch` and the epoch-keyed claim clearing, but did not exhaustively check whether an upstream guard in `IdleCDOEpochVariant.requestWithdraw` already blocks a second request while an unclaimed funded receipt exists; the contract comments suggest waiting "for another epoch to claim both requests" is intended behavior, which implies stacking is permitted.