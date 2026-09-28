### Title
Post-default instant-withdraw receipts are re-haircut and drain the default recovery reserve — `instantWithdrawsRequestsByEpoch`/`pendingInstantWithdraws` reuse the defaulted epoch tag for post-default requests - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault` handles post-default normal withdrawals through a dedicated `postDefaultRequests` bucket (paid 1:1 from the recovery reserve), but `requestInstantWithdraw` has no analogous post-default path. After `finalizeDefaultRecovery`, a new instant-withdraw request is recorded under `instantWithdrawsRequestsByEpoch[user][epochNumber]`, where `epochNumber` is still equal to `defaultRecoveryEpoch` because no further epochs run after default. The same storage slot therefore holds two semantically different things: pre-default receipts whose basis must be haircut, and post-default receipts that were already priced at the haircutted `virtualPrice`. When the user claims, `claimInstantWithdrawRequest` → `_claimDefaultedInstantWithdrawRequest` applies `defaultRecoveryPrice` a second time and pays the amount out of `defaultRecoveryReserve`, which was sized at finalization only for pre-default claims.

### Finding Description
- `requestWithdraw` explicitly branches on `defaultRecoveryFinalized` and stores the request in `postDefaultRequests`, paying it 1:1 ("Post-default receipts are paid 1:1 because the haircut was applied when the request was made", `IdleCreditVault.sol:247-257,760-767`).
- `requestInstantWithdraw` performs no such check: it unconditionally does `instantWithdrawsRequestsByEpoch[_user][epochNumber] += _amount` and `pendingInstantWithdraws += _amount` (`IdleCreditVault.sol:356-375`).
- `finalizeDefaultRecovery` sets `defaultRecoveryEpoch = epochNumber` and `defaultInstantWithdrawsFinalized = pendingInstantWithdraws != 0` (`IdleCreditVault.sol:692-696`). After default, `epochNumber` is never incremented again (epoch bump happens only via `deposit`/`stopEpoch` while an epoch is running), so `epochNumber == defaultRecoveryEpoch` forever.
- `claimInstantWithdrawRequest` calls `_claimDefaultedInstantWithdrawRequest` whenever `defaultRecoveryFinalized && defaultInstantWithdrawsFinalized`. That function reads `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` — which now includes the post-default request — burns it, and transfers `claimBasis * defaultRecoveryPrice / RECOVERY_FULL` from `defaultRecoveryReserve` (`IdleCreditVault.sol:380-393,842-856`).
- The union-style interpretation conflict (one epoch-tagged bucket, two meanings) is compounded by the "incorrect fix" pattern: the reserve/top-up logic added for correct pre-default haircutting (`_defaultPrefundedInstantReserve`, `defaultPendingClaimBasis`) is not reconciled with the fact that `epochNumber` is frozen, so later requests alias the defaulted epoch.

### Impact Explanation
Two distinct harms:
1. Double haircut on the post-default requester: the CDO already prices the instant request at the post-default `virtualPrice` (recovery-adjusted), then `_claimDefaultedInstantWithdrawRequest` multiplies by `defaultRecoveryPrice` again. With a 70% recovery price, the user receives ~49% of their pre-default value instead of 70% — a direct loss equal to `(1 - recoveryPrice) * claimBasis`.
2. Reserve insolvency for legitimate claimants: `defaultRecoveryReserve` was set to `reserveAmount = recovered + prefunded + reserveDefaultRecovery` to cover exactly the pre-default basis (`IdleCreditVault.sol:685-691`). Every post-default instant claim consumes `defaultRecoveryReserve` (via `_transferDefaultRecovery`), draining funds earmarked for defaulted-epoch receipt holders, who then cannot claim (permanent loss of unclaimed recovery).

Additionally, `pendingInstantWithdraws += _amount` on each post-default instant request is never collectable (no one calls `collectInstantWithdrawFunds` on a defaulted pool), so `defaultInstantWithdrawsFinalized` stays true and the misclassification is permanent.

### Likelihood Explanation
Requires the CDO to still route `claimInstantWithdrawRequest`/instant requests post-default. The attacker is an ordinary KYC'd tranche holder calling `requestInstantWithdraw` via the CDO after `finalizeDefault`; no privileged collusion is needed. The main uncertainty is whether `IdleCDOEpochVariant` gates instant-withdraw requests on `defaulted()`/`allowInstantWithdraw()` — the foundry tests show `allowInstantWithdraw` is forced false while unfunded instant claims exist, but they also exercise `claimInstantWithdrawRequest` successfully after finalization (`test/foundry/IdleCreditVault.t.sol:4540-4550`), indicating the post-default claim path is live. Whether fresh post-default *requests* are permitted is the open question; `requestInstantWithdraw` in the strategy itself has no guard, unlike `requestWithdraw`.

### Recommendation
Mirror the `postDefaultRequests` handling in `requestInstantWithdraw`: when `defaultRecoveryFinalized` (or `IIdleCDOEpochVariant(idleCDO).defaulted()`), store the amount in a dedicated post-default instant bucket (or reuse `postDefaultRequests`) instead of `instantWithdrawsRequestsByEpoch[currentEpoch]`, and do not increase `pendingInstantWithdraws`. Alternatively, tag instant claims with an explicit epoch and treat `epochNumber > defaultRecoveryEpoch` — or a separate post-default flag — as 1:1 reserve claims, and revert instant requests in the CDO while defaulted if post-default instant withdrawals are not intended.

### Proof of Concept
Foundry fork PoC sketch (setup mirrors `test/foundry/IdleCreditVault.t.sol` default tests):

```solidity
// 1. Epoch 0: userA (instant) and userB deposit; startEpoch; borrower repays partially.
// 2. userA.requestWithdraw via instant path -> instantWithdrawsRequestsByEpoch[A][0] = amtA
// 3. getInstantWithdrawFunds fails / borrower defaults -> _checkDefault()
// 4. finalizeDefaultRecovery(recovered) -> defaultRecoveryEpoch = 0, defaultRecoveryPrice = P,
//    defaultInstantWithdrawsFinalized = true, defaultRecoveryReserve sized for amtA only.
// 5. Post-default: userB still holds tranche tokens.
//    cdoEpoch.requestInstantWithdraw(B)  // CDO prices at post-default virtualPrice, amount = vB (already haircut)
//    strategy: instantWithdrawsRequestsByEpoch[B][0] += vB  // epochNumber still == 0
// 6. userB.claimInstantWithdrawRequest()
//    -> _claimDefaultedInstantWithdrawRequest(B): burns vB, pays vB * P / 1e18   // DOUBLE HAIRCUT
//    -> defaultRecoveryReserve -= vB*P/1e18                                    // drains A's recovery
// 7. userA.claimInstantWithdrawRequest() reverts / underpays:
//    _transferDefaultRecovery underflows defaultRecoveryReserve or insolvency.
assertEq(userBPayout, vB * P / RECOVERY_FULL);            // should have been vB
assertLe(strategy.defaultRecoveryReserve(), reserveForA); // reserve stolen
```

Caveat: I could not fully verify, within the available iterations, whether `IdleCDOEpochVariant` exposes a post-default instant-request path; if the CDO hard-blocks `requestInstantWithdraw` once `defaulted()`, the exploit is unreachable and this should be treated as defense-in-depth hardening rather than a live vulnerability.