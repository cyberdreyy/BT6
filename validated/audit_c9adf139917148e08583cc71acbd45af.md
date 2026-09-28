### Title
Post-default instant withdraw requests are misrecorded under the defaulted epoch, corrupting recovery accounting - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`requestInstantWithdraw` lacks the `defaultRecoveryFinalized` guard that `requestWithdraw` has. After default finalization, `epochNumber` no longer advances, so a new instant receipt is recorded under `epochNumber == defaultRecoveryEpoch`. On claim it is processed by `_claimDefaultedInstantWithdrawRequest` as if it were an unfunded default-epoch receipt, mutating `pendingInstantWithdraws` and `instantWithdrawClaimsByEpoch[defaultRecoveryEpoch]` for a receipt that was never part of the finalized recovery basis.

### Finding Description
The external bug is a context-confusion flaw: an XSLT load runs with the wrong source document's security context (CSP bypass). The credit-vault analog is epoch-context confusion: receipts carry the epoch they were created in, and after `finalizeDefaultRecovery` fixes `defaultRecoveryEpoch = epochNumber` (line 693), the vault never advances `epochNumber` again, so any new receipt is stamped with the defaulted epoch.

`requestWithdraw` handles this correctly: when `defaultRecoveryFinalized` it refuses to touch epoch accounting and routes to `postDefaultRequests` (lines 247-258). `requestInstantWithdraw` (lines 356-375) has no equivalent branch — it burns CDO strategy tokens, mints a receipt to the user, and writes:

- `instantWithdrawsRequestsByEpoch[_user][epochNumber] += _amount` — i.e. under `defaultRecoveryEpoch`
- `instantWithdrawClaimsByEpoch[epochNumber] += _amount`
- `pendingInstantWithdraws += _amount`

Later, `claimInstantWithdrawRequest` (lines 380-393) checks `defaultRecoveryFinalized && defaultInstantWithdrawsFinalized` and calls `_claimDefaultedInstantWithdrawRequest` (lines 842-856), which clears `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` — silently sweeping the attacker's *new* post-default receipt into the *old* recovery bucket, decrementing `pendingInstantWithdraws` and `instantWithdrawClaimsByEpoch[defaultEpoch]` and paying `claimBasis * defaultRecoveryPrice` out of `defaultRecoveryReserve` via `_transferDefaultRecovery`, without `_transferFundedClaim`'s reserve-isolation guard (lines 897-907).

Two concrete failure modes:

1. **Reserve drain / claim DoS (defaultInstantWithdrawsFinalized == true).** The new receipt inflates `instantWithdrawClaimsByEpoch[defaultRecoveryEpoch]` and `pendingInstantWithdraws` beyond what the finalized reserve covers. Once real defaulted-epoch claimants and the attacker claim, `defaultRecoveryReserve` is exhausted early; later legitimate claimants hit `defaultRecoveryReserve -= _amount` underflow and their recovery is permanently frozen.
2. **Unconditional par claim against an isolated reserve (defaultInstantWithdrawsFinalized == false).** If no unfunded instant receipts existed at finalization, `claimInstantWithdrawRequest` skips the default branch entirely and attempts `_transferFundedClaim` for a receipt that was never funded. The guard `balance - reserve < _amount` reverts, so the attacker's receipt (and the CDO basis it burned) is permanently frozen — a loss-of-funds liveness break rather than a clean revert at request time.

The broken invariant is "one receipt one payout" / reserve isolation: post-default state (receipt epochs, reserve, claim bases) must be immutable w.r.t. new requests, and it is not.

### Impact Explanation
An unprivileged tranche holder who can invoke the CDO's instant-withdraw request path post-default can either drain `defaultRecoveryReserve` ahead of legitimate defaulted-epoch claimants (theft of unclaimed recovery yield, capped by the receipt size they can mint) or permanently freeze their own and other instant claimants' payouts via reserve/claim-basis corruption.

### Likelihood Explanation
Requires an epoch-mode vault with instant withdraws enabled and a finalized default where `pendingInstantWithdraws != 0` (theft) or `== 0` (freeze). The attacker needs tranche tokens burnable through the CDO's request path after `defaulted()`; whether `IdleCDOEpochVariant.requestWithdraw` still routes instant requests post-default could not be fully verified in this pass — if the CDO blocks it, the finding reduces to a latent accounting bug. The missing `defaultRecoveryFinalized` guard in `requestInstantWithdraw` is itself verifiable and asymmetric with `requestWithdraw`.

### Recommendation
Mirror the `requestWithdraw` post-default branch in `requestInstantWithdraw`: when `defaultRecoveryFinalized`, either revert or route the receipt into `postDefaultRequests` (never into `instantWithdrawsRequestsByEpoch[epochNumber]`/`pendingInstantWithdraws`). Additionally, tag instant receipts with an explicit flag distinguishing pre-default from post-default basis so `_claimDefaultedInstantWithdrawRequest` cannot clear a receipt created after `defaultRecoveryEpoch` was fixed.

### Proof of Concept
```solidity
// Foundry fork PoC sketch (follow test/foundry/IdleCreditVault.t.sol harness)
// 1. Deposit as attacker + victim (AA), run epoch, trigger borrower default.
// 2. manager calls cdoEpoch.finalizeDefault(recovered, manager) with
//    pendingInstantWithdraws != 0 so defaultInstantWithdrawsFinalized == true.
// 3. Attacker (still holding tranche tokens) calls the CDO request-withdraw
//    path that reaches strategy.requestInstantWithdraw(_amount, attacker).
//    assert: instantWithdrawsRequestsByEpoch[attacker][defaultRecoveryEpoch]
//            == _amount  // new receipt stamped into the defaulted epoch
//    assert: pendingInstantWithdraws increased post-finalization.
// 4. Attacker calls cdoEpoch.claimInstantWithdrawRequest():
//    _claimDefaultedInstantWithdrawRequest pays
//    _amount * defaultRecoveryPrice / 1e18 from defaultRecoveryReserve.
// 5. Victim (legit defaulted-epoch instant claimant) calls
//    claimInstantWithdrawRequest() -> reverts on
//    defaultRecoveryReserve -= _amount underflow once reserve is drained.
// Expected: attacker drains reserve earmarked for victim; victim's recovery
// claim permanently reverts.
```

Note: step 3 depends on `IdleCDOEpochVariant` still forwarding instant requests after `defaulted()` — I could not fully inspect `contracts/IdleCDOEpochVariant.sol`'s request gating within this pass. If it reverts, the strategy-level inconsistency still stands but the exploitability precondition fails.