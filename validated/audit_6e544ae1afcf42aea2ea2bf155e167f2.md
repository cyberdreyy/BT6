### Title
Funded instant-withdraw claims never clear `instantWithdrawsRequestsByEpoch`/`instantWithdrawClaimsByEpoch`, leaking stale claim basis that corrupts default recovery - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
The external bug (CVE-2019-17178) is a leak caused by reusing the input pointer of `realloc` as its return target: the old buffer reference is lost while its resource still exists. The analog in `IdleCreditVault` is the funded instant-withdraw claim path: `claimInstantWithdrawRequest` frees the aggregate receipt (`instantWithdrawsRequests[_user] = 0`) but never clears the per-epoch "pointer" state `instantWithdrawsRequestsByEpoch[_user][epoch]` or the epoch aggregate `instantWithdrawClaimsByEpoch[epoch]`. If the same epoch later defaults with a partially unfunded instant queue, the leaked basis is re-read by `defaultPendingClaimBasis`, `_defaultPrefundedInstantReserve`, and `_claimDefaultedInstantWithdrawRequest`, inflating the recovery basis and/or forcing underflow reverts that permanently freeze recovery funds.

### Finding Description
`requestInstantWithdraw` records three pieces of state per request: `instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch[_user][currentEpoch]`, and `instantWithdrawClaimsByEpoch[currentEpoch]` (`IdleCreditVault.sol:366-374`).

The normal claim path only clears the first:

```solidity
// contracts/strategies/idle/IdleCreditVault.sol:387-392
uint256 amount = instantWithdrawsRequests[_user];
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);
```

`instantWithdrawsRequestsByEpoch[_user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]` remain nonzero forever — the "old pointer" is overwritten (aggregate zeroed) while the per-epoch reference leaks. This is safe only if `epochNumber` has moved on before a default, but nothing requires that:

- `claimInstantWithdrawRequest` has no epoch gating; a user can request and claim a funded instant withdraw while `epochNumber == N`.
- If the borrower then defaults during epoch `N` while some *other* user's instant request remains unfunded (`pendingInstantWithdraws != 0`), `finalizeDefaultRecovery` sets `defaultRecoveryEpoch = N` and `defaultInstantWithdrawsFinalized = true` (`IdleCreditVault.sol:693-696`).
- `defaultPendingClaimBasis` then counts the leaked `instantWithdrawClaimsByEpoch[N]`, including already-paid claims (`IdleCreditVault.sol:644-648`), inflating `totalBasis` and diluting `defaultRecoveryPrice` for every claimant.
- `_defaultPrefundedInstantReserve` computes `instantBasis - pendingInstant`, treating already-paid-out funds as held reserve (`IdleCreditVault.sol:716-722`), so `recoveryPrice` is computed over phantom backing; later `_transferDefaultRecovery` calls underflow `defaultRecoveryReserve` or fail the ERC20 transfer (`IdleCreditVault.sol:912-917`).
- For the user whose receipt was already claimed, `_claimDefaultedInstantWithdrawRequest` reads the stale `instantWithdrawsRequestsByEpoch[user][N] = A` and executes `instantWithdrawsRequests[_user] -= claimBasis` with the aggregate already 0 (or smaller), which underflows and reverts permanently (`IdleCreditVault.sol:844-852`), freezing that user's remaining receipts and anyone behind them in the same call path.

### Impact Explanation
- Recovery price dilution: phantom (already-claimed) basis reduces `defaultRecoveryPrice`, so all honest defaulted-epoch claimants receive less than their fair share.
- Permanent freezing: the underflow in `_claimDefaultedInstantWithdrawRequest` and the phantom `prefundedReserve` make recovery claims revert, permanently locking `defaultRecoveryReserve` (unclaimed yield / recovered principal) in the strategy.
- Quantified loss: every instant-withdraw unit claimed-and-not-cleared in the defaulted epoch inflates the basis 1:1; the reserve is split across real + leaked claims, and the leaked share can never be paid out.

### Likelihood Explanation
Requires (a) a user to request *and* fully claim an instant withdrawal within the same `epochNumber`, and (b) a borrower default in that epoch while `pendingInstantWithdraws != 0`. Both are reachable with honest-role sequencing: instant claims are epoch-ungated, and partial instant funding followed by default is an explicitly supported flow (`_defaultPrefundedInstantReserve`, `defaultInstantWithdrawsFinalized`). The attacker needs no privilege — they only need to have claimed an instant receipt in the defaulted epoch — though the damage primarily hits all recovery claimants rather than profiting the attacker directly.

### Recommendation
Clear per-epoch instant-withdraw accounting on the funded claim path, symmetric with `_clearWithdrawClaimForEpoch` for normal requests: in `claimInstantWithdrawRequest`, iterate or track the user's request epochs and zero `instantWithdrawsRequestsByEpoch[_user][epoch]` and decrement `instantWithdrawClaimsByEpoch[epoch]` when paying at par, so no stale basis can leak into `defaultRecoveryEpoch` accounting.

### Proof of Concept
A Foundry fork test on the existing `IdleCreditVault.t.sol` harness:

```solidity
// 1. Deposit AA + BB, run epoch 0 (start/stop with low APR so instant withdraws are enabled).
// 2. User A: cdoEpoch.requestInstantWithdraw -> funded via getInstantWithdrawFunds -> claimInstantWithdrawRequest.
//    Assert: instantWithdrawsRequests[A] == 0 but
//    instantWithdrawsRequestsByEpoch[A][epochNumber] != 0 && instantWithdrawClaimsByEpoch[epochNumber] != 0  (LEAK).
// 3. User B: requestInstantWithdraw in the same epoch; borrower underfunds so pendingInstantWithdraws > 0.
// 4. Borrower defaults; manager calls finalizeDefault/finalizeDefaultRecovery with recoveredAmount.
//    Assert: defaultPendingClaimBasis() includes A's already-paid amount; defaultRecoveryPrice is diluted.
//    Assert: A's claimInstantWithdrawRequest reverts (underflow on instantWithdrawsRequests -= claimBasis);
//    B's claim pays basis * dilutedPrice, leaving reserve dust permanently locked.
```

Uncertainty: the exact trigger for `epochNumber` advancement between request and claim depends on `IdleCDOEpochVariant`'s instant-withdraw timing; the test must keep request, funded claim, second request, and default within one `epochNumber` value.