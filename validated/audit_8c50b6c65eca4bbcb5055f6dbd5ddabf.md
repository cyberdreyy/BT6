### Title
Claimed instant-withdraw receipt leaves stale per-epoch basis that is double-paid from the default recovery reserve - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The kernel bug is a stale back-pointer: `bfqq->bic` is not cleared when the queue is detached, so a freed `bic` is dereferenced later. The vault analog is `instantWithdrawsRequestsByEpoch[_user][epoch]`: `claimInstantWithdrawRequest` zeroes the aggregate `instantWithdrawsRequests[_user]` and burns the receipt but never clears the per-epoch entry or `instantWithdrawClaimsByEpoch[epoch]`. After a borrower default in that same epoch, `_claimDefaultedInstantWithdrawRequest` dereferences the stale per-epoch basis and pays out again from `defaultRecoveryReserve`.

### Finding Description
In `claimInstantWithdrawRequest` (lines 380-393) only `instantWithdrawsRequests[_user]` is reset to 0; `instantWithdrawsRequestsByEpoch[_user][requestEpoch]` and `instantWithdrawClaimsByEpoch[requestEpoch]` are left untouched — the "pointer" survives the detach. In contrast, the normal-withdraw path explicitly clears per-epoch data (`_clearWithdrawClaimForEpoch`, lines 811-837, which also resets `lastWithdrawRequest`). After `defaultRecoveryFinalized`, `claimInstantWithdrawRequest` first calls `_claimDefaultedInstantWithdrawRequest` (lines 382-385), which reads `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` (line 844), decrements `instantWithdrawsRequests[_user]`, clamps `pendingInstantWithdraws`, burns `claimBasis` strategy tokens, and transfers `claimBasis * defaultRecoveryPrice / RECOVERY_FULL` from `defaultRecoveryReserve` (lines 847-855).

Attack sequence (fixed-APR variant with instant withdrawals enabled, running epoch):
1. Attacker (any tranche holder) calls `requestInstantWithdraw` in epoch D — `instantWithdrawsRequestsByEpoch[u][D] = X`, `instantWithdrawClaimsByEpoch[D] += X`, receipt tokens minted.
2. Instant liquidity is collected (`collectInstantWithdrawFunds`), attacker calls `claimInstantWithdrawRequest` and is paid X. Aggregate is zeroed; `instantWithdrawsRequestsByEpoch[u][D]` stays X.
3. Borrower under-repays at `stopEpoch` → epoch D defaults; `finalizeDefaultRecovery` counts `instantWithdrawClaimsByEpoch[D]` (still containing X) in the recovery accounting and `defaultRecoveryEpoch = D`.
4. Attacker makes a `requestWithdraw` post-default; `postDefaultRequests` mints fresh strategy-token receipts (lines 247-257), restoring a burnable balance.
5. Attacker calls `claimInstantWithdrawRequest` again → `_claimDefaultedInstantWithdrawRequest` reads the stale X, burns it against the new receipt balance, and pays `X * defaultRecoveryPrice` from `defaultRecoveryReserve` a second time for a receipt already redeemed at par.

### Impact Explanation
The same instant receipt is paid twice: once at par pre-default and once haircut from the isolated `defaultRecoveryReserve`. Every defaulted/post-default claim consumes that reserve (`_transferDefaultRecovery`, lines 912-917), so the attacker's duplicated claim directly reduces the reserve available to other defaulted-epoch claimants — theft of recovery proceeds up to the attacker's pre-default instant withdrawal size.

### Likelihood Explanation
Requires an epoch that both funds instant withdrawals and ends in a borrower default — an unusual but reachable state (borrower default is a designed flow, not attacker-controlled). The attacker needs an instant request fulfilled inside the defaulted epoch, then a post-default request of at least the stale size to satisfy the burn. No privileged-role cooperation is needed; sequencing around honest manager/borrower calls suffices.

### Recommendation
Mirror the kernel fix — clear the pointer on detach. In `claimInstantWithdrawRequest`, iterate/zero `instantWithdrawsRequestsByEpoch[_user][*]` (or track the request epoch like `lastWithdrawRequest` for instant requests) and decrement `instantWithdrawClaimsByEpoch` by the claimed basis, so a claimed receipt cannot later be re-dereferenced by `_claimDefaultedInstantWithdrawRequest`.

### Proof of Concept
```solidity
// Foundry fork PoC sketch (test/foundry/IdleCreditVault.t.sol harness)
// 1. enable instant withdraws (setInstantWithdrawParams(delay, aprDelta, false))
// 2. user deposits, startEpoch
// 3. user requestInstantWithdraw via cdoEpoch.instantWithdraw / request path
//    -> instantWithdrawsRequestsByEpoch[user][epochD] = X
// 4. warp delay, fund + claimInstantWithdrawRequest -> paid X at par
//    assert(instantWithdrawsRequestsByEpoch[user][epochD] == X); // stale entry
// 5. borrower under-repays -> stopEpoch -> defaulted, finalizeDefaultRecovery
//    defaultRecoveryEpoch == epochD
// 6. user requestWithdraw -> postDefaultRequests mints fresh receipt >= X
// 7. user claimInstantWithdrawRequest
//    -> _claimDefaultedInstantWithdrawRequest burns X from new receipt
//    -> _transferDefaultRecovery pays X*defaultRecoveryPrice again
// assert(defaultRecoveryReserve decreased by X*price AND user already received X pre-default)
```

Uncertainty note: I could not read `finalizeDefaultRecovery` in this pass to confirm exactly how `instantWithdrawClaimsByEpoch[D]` feeds `defaultRecoveryReserve`/`defaultRecoveryPrice` sizing; if the reserve is funded only from actual recovered underlyings, the stale basis still produces an illegitimate haircut payout that drains it, but the precise dilution math should be verified. Also confirm an instant claim is claimable inside the same epoch that later defaults (the delay path in the queue tests suggests it is).