### Title
Instant-withdraw receipts from epochs before the default epoch are permanently unclaimable after `finalizeDefaultRecovery` - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault` accepts instant-withdraw requests that mint a strategy-token receipt, but its default-recovery accounting only recognizes instant receipts belonging to the *current* (`defaultRecoveryEpoch`) epoch. An unfunded instant receipt created in an earlier epoch is excluded from the recovery basis, cannot be paid from `defaultRecoveryReserve` (the `_transferFundedClaim` reserve guard reverts), and is never funded again — so the holder's receipt is permanently frozen.

### Finding Description
When a user calls `requestInstantWithdraw`, the vault burns CDO-held strategy tokens and mints a receipt to the user, recording `instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch[_user][currentEpoch]`, `instantWithdrawClaimsByEpoch[currentEpoch]`, and `pendingInstantWithdraws` (contracts/strategies/idle/IdleCreditVault.sol:356-375).

`collectInstantWithdrawFunds` only decrements `pendingInstantWithdraws` by whatever the CDO actually funded — an unfunded remainder persists across `stopEpoch`/epoch increments (contracts/strategies/idle/IdleCreditVault.sol:398-403). So a receipt requested in epoch N can remain unfunded while `epochNumber` advances to M.

On default, `defaultPendingClaimBasis` computes `basis = pendingWithdraws + instantWithdrawClaimsByEpoch[epochNumber]` — only the *current* epoch M's instant claim basis, even though `pendingInstantWithdraws` still includes the epoch-N remainder (contracts/strategies/idle/IdleCreditVault.sol:644-649). `finalizeDefaultRecovery` sizes `defaultRecoveryReserve` to cover exactly this basis times `defaultRecoveryPrice` (contracts/strategies/idle/IdleCreditVault.sol:685-709). The epoch-N receipt is never added to the reserve and is not haircut.

Later, when the user calls `claimInstantWithdrawRequest`, `_claimDefaultedInstantWithdrawRequest` only clears `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` — i.e., epoch M (contracts/strategies/idle/IdleCreditVault.sol:842-856). The epoch-N remainder stays in `instantWithdrawsRequests[_user]` and falls to `_transferFundedClaim`, which reverts with `NotAllowed` because `balance - defaultRecoveryReserve < amount` — every funded-weis unit is reserved for recovery claimants (contracts/strategies/idle/IdleCreditVault.sol:897-907). No other path ever funds it: the epoch is closed/defaulted, so `collectInstantWithdrawFunds` will not be called again.

### Impact Explanation
The minted receipt cannot be redeemed and cannot be transferred (`_transfer` restricts moves to the IdleCDO, contracts/strategies/idle/IdleCreditVault.sol:939-942). The holder's underlying-equivalent claim — up to the full unfunded instant-withdraw amount — is permanently frozen, mirroring the external report's "funding accepted but payout impossible" class. Loss equals the pre-default-epoch unfunded instant receipt balance.

### Likelihood Explanation
Requires an instant withdrawal that the CDO could not fully fund at epoch end (borrower shortfall) followed by a borrower default in a later epoch — a plausible sequence in stressed credit pools, triggered by an ordinary lender's instant-withdraw request rather than any attacker privilege. No existing guard stops it: `defaultPendingClaimBasis` silently drops older instant bases, and `_transferFundedClaim` actively reverts on the fallback path.

### Recommendation
Include all outstanding instant receipt bases in `defaultPendingClaimBasis` (e.g., sum `instantWithdrawClaimsByEpoch` over non-funded epochs or track an aggregate unfunded instant basis), and let `_claimDefaultedInstantWithdrawRequest` clear and haircut a user's total unfunded instant basis rather than only `defaultRecoveryEpoch`. Alternatively, treat older unfunded instant receipts as funded claims by reserving for them explicitly at finalization.

### Proof of Concept
Foundry fork outline (single-borrower credit vault, e.g. Pareto):

```solidity
// 1. LP deposits, epoch N running.
// 2. LP calls requestInstantWithdraw(amount) -> receipt minted,
//    instantWithdrawsRequestsByEpoch[LP][N] = amount.
// 3. Borrower repays less than amount; stopEpoch collects partial/zero
//    instant funds -> pendingInstantWithdraws stays > 0, epochNumber = N+1.
// 4. Epochs run to M; borrower defaults -> _handleBorrowerDefault,
//    finalizeDefaultRecovery(recovered, source):
//    basis misses instantWithdrawClaimsByEpoch[N]; reserve undersized.
// 5. LP calls claimInstantWithdrawRequest via CDO:
//    _claimDefaultedInstantWithdrawRequest clears only epoch M (0 for LP);
//    remaining instantWithdrawsRequests[LP] hits _transferFundedClaim and
//    reverts NotAllowed because balance - defaultRecoveryReserve < amount.
//    Receipt is permanently unclaimable.
assertEq(strategy.instantWithdrawsRequests(LP), amount); // still stuck
```