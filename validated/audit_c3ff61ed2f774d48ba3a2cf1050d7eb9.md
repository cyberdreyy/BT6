### Title
Instant-withdraw receipts are not cleared from `instantWithdrawsRequestsByEpoch`/`instantWithdrawClaimsByEpoch` on a funded claim, letting an attacker double-claim and inflate the default-recovery basis - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
`claimInstantWithdrawRequest` zeroes `instantWithdrawsRequests[_user]` and pays the user, but never clears the per-epoch receipt basis `instantWithdrawsRequestsByEpoch[_user][epoch]` or the aggregate `instantWithdrawClaimsByEpoch[epoch]`. If the epoch later defaults with `pendingInstantWithdraws != 0`, `defaultPendingClaimBasis` counts the already-paid receipt again, `_defaultPrefundedInstantReserve` counts the already-spent funds as reserve, and `_claimDefaultedInstantWithdrawRequest` pays the attacker a second time from `defaultRecoveryReserve`. Like the kernel leak, the consumed resource (receipt basis / funded reserve) is never freed, starving later claimants.

### Finding Description
- Funded claim path: `claimInstantWithdrawRequest` burns `instantWithdrawsRequests[_user]` and calls `_transferFundedClaim`, but touches neither `instantWithdrawsRequestsByEpoch` nor `instantWithdrawClaimsByEpoch` ([IdleCreditVault.sol:380-393](contracts/strategies/idle/IdleCreditVault.sol)).
- `collectInstantWithdrawFunds` supports partial funding (`pendingInstantWithdraws -= _amount`), so a state exists where some instant claims are paid out while `pendingInstantWithdraws != 0` ([:398-403](contracts/strategies/idle/IdleCreditVault.sol)).
- On default, `defaultPendingClaimBasis` adds `instantWithdrawClaimsByEpoch[epochNumber]` (stale, includes paid receipts) and `_defaultPrefundedInstantReserve` treats `instantBasis - pendingInstant` as still-held reserve even though part was already transferred out ([:644-649](contracts/strategies/idle/IdleCreditVault.sol), [:716-723](contracts/strategies/idle/IdleCreditVault.sol)).
- After finalization, `claimInstantWithdrawRequest` routes into `_claimDefaultedInstantWithdrawRequest`, which reads the still-nonzero `instantWithdrawsRequestsByEpoch[_user][defaultEpoch]` and pays `claimBasis * defaultRecoveryPrice` from `defaultRecoveryReserve` a second time ([:842-856](contracts/strategies/idle/IdleCreditVault.sol)).

Result: the recovery price is computed over an inflated basis while the reserve is simultaneously overstated by already-spent funds, and the attacker extracts the phantom `A` twice — once at par, once at recovery price — directly draining reserve owed to honest pending claimants.

### Impact Explanation
Direct theft / insolvency: an unprivileged tranche holder who makes an instant-withdraw request of amount `A` that gets funded and claimed pre-default still holds a nonzero defaulted-epoch basis. After `finalizeDefault`, they claim `A * defaultRecoveryPrice` again. Because both `reserveAmount` and `totalBasis` are inflated by `A`, the last honest claimant(s) are underpaid or the reserve underflows (`defaultRecoveryReserve -= _amount` reverts), permanently freezing their recovery. Loss ≈ `A * defaultRecoveryPrice` stolen plus `A` of phantom reserve.

### Likelihood Explanation
Requires: instant withdrawals enabled, an epoch where the CDO collects instant funds only partially (explicitly supported by `_defaultPrefundedInstantReserve`'s design), the attacker claims the funded portion pre-default, and the borrower defaults in the same epoch with other instant claims still pending. Attacker cost is only a normal `requestInstantWithdraw`; no privileged collusion needed. Epoch-dependent, so medium likelihood.

### Recommendation
In `claimInstantWithdrawRequest`, clear `instantWithdrawsRequestsByEpoch[_user][epochNumber]` and decrement `instantWithdrawClaimsByEpoch[epochNumber]` (tracking the request epoch per user, e.g. via a `lastInstantRequestEpoch` marker), so paid receipts can never re-enter `defaultPendingClaimBasis`, `_defaultPrefundedInstantReserve`, or `_claimDefaultedInstantWithdrawRequest`.

### Proof of Concept
```solidity
// Foundry fork test sketch (IdleCreditVault instantiated via cdoEpoch.strategy())
// 1. Epoch N running, instant withdraws enabled (setInstantWithdrawParams).
// 2. attacker deposits AA, then requestInstantWithdraw(A, attacker) via cdoEpoch.
//    victim does requestInstantWithdraw(B, victim).
// 3. Manager/borrower flow funds only A: IdleCDO calls
//    strategy.collectInstantWithdrawFunds(A)  -> pendingInstantWithdraws = B
// 4. attacker: cdoEpoch.claimInstantWithdrawRequest() -> receives A underlying.
//    instantWithdrawsRequests[attacker] == 0 BUT
//    instantWithdrawsRequestsByEpoch[attacker][N] == A   // stale
//    instantWithdrawClaimsByEpoch[N] == A + B            // stale
// 5. Borrower defaults; owner stopEpoch -> defaulted() == true.
// 6. manager: cdoEpoch.finalizeDefault(recovered, manager) ->
//    basis = pendingWithdraws + (A + B)   // A counted twice
//    reserve includes prefundedReserve = (A+B) - B = A   // phantom, already paid
// 7. attacker: cdoEpoch.claimInstantWithdrawRequest() again ->
//    _claimDefaultedInstantWithdrawRequest pays A * defaultRecoveryPrice
//    from defaultRecoveryReserve a second time.
// 8. victim's defaulted claim now underpaid or reverts on
//    defaultRecoveryReserve -= _amount (reserve drained by phantom A).
assertGt(underlying.balanceOf(attacker), A); // second payout succeeded
```