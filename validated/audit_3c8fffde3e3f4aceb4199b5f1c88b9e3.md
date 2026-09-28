### Title
Instant-withdraw claim only inspects the default-epoch slice — receipts from other epochs are paid at par unfunded - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
`claimInstantWithdrawRequest` applies the default-recovery haircut only to `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` and then burns/pays the *entire* `instantWithdrawsRequests[_user]` aggregate at par through `_transferFundedClaim`. Instant receipts recorded in any other epoch — which are equally unfunded (they are part of `pendingInstantWithdraws`) — escape the haircut entirely and are paid 1:1, while default-epoch claimants are diluted by `defaultRecoveryPrice`. This is the direct analog of CVE-2018-6336: only one slice of a composite object is inspected (the default-epoch slice), and the uninspected slices execute as if they were fully "signed" (funded).

### Finding Description
In `IdleCreditVault`, instant withdraw receipts are tracked three ways:

- `instantWithdrawsRequests[_user]` — aggregate per user (line 366)
- `instantWithdrawsRequestsByEpoch[_user][epoch]` — per epoch (line 371)
- `instantWithdrawClaimsByEpoch[epoch]` / `pendingInstantWithdraws` — global unfunded basis (lines 372–374)

When the borrower defaults and the CDO finalizes recovery, `claimInstantWithdrawRequest` runs:

```solidity
// contracts/strategies/idle/IdleCreditVault.sol:380-393
if (defaultRecoveryFinalized && defaultInstantWithdrawsFinalized) {
    _claimDefaultedInstantWithdrawRequest(_user);   // clears ONLY defaultEpoch slice
}
uint256 amount = instantWithdrawsRequests[_user];   // entire remaining aggregate
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);                // paid at par
```

`_claimDefaultedInstantWithdrawRequest` (lines 842–856) only clears `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` and decrements `pendingInstantWithdraws` for that slice. Receipts booked under any *other* epoch remain in `instantWithdrawsRequests[_user]` and are paid in full, even though they were never funded by `collectInstantWithdrawFunds` — i.e. they are part of the unfunded `pendingInstantWithdraws` hole that default finalization already socialized into `defaultRecoveryPrice`.

Worse, `requestInstantWithdraw` (lines 356–375) has none of the post-default hygiene that `requestWithdraw` has: `requestWithdraw` reverts if the user has unclaimed old receipts after `defaultRecoveryFinalized` (lines 247–251), but `requestInstantWithdraw` performs no such check and will happily record a receipt under the *current* `epochNumber`, which by construction differs from `defaultRecoveryEpoch`. That new receipt is unfunded by definition (the borrower defaulted and `collectInstantWithdrawFunds` can only pull real underlying from the CDO), yet `claimInstantWithdrawRequest` pays it at par from the strategy's balance.

Contrast with the normal-withdraw path, which correctly routes every request through either `_claimPostDefaultWithdrawRequest` (funded by `defaultRecoveryReserve`, haircut pre-applied via lowered `virtualPrice`) or `_claimDefaultedWithdrawRequest` (haircut applied). The instant path has no post-default equivalent — a whole class of receipt is inspected for only one epoch slice and the rest pays out unconditionally.

### Impact Explanation
Direct theft / unfair recovery distribution. A tranche holder can:

1. Hold an unfunded instant receipt from a pre-default epoch (or create a fresh one post-default via `requestInstantWithdraw`, which is ungated).
2. Call `claimWithdrawRequest`/`claimInstantWithdrawRequest` on the CDO after `finalizeDefaultRecovery`.
3. Receive the non-default-epoch slice at 100% instead of `defaultRecoveryPrice`, paid from the strategy's non-reserve balance — funds that belong to active LPs or to the funding that should back other claimants.

The `_transferFundedClaim` guard (lines 900–905) only protects `defaultRecoveryReserve`; it does not distinguish funded vs. unfunded instant receipts, so any non-reserve balance (e.g., interest repaid late, partially collected instant funds, skimmed donations not yet swept) is drained preferentially by the first user whose aggregate contains a non-default-epoch slice. Quantified loss: up to the full unfunded non-default-epoch instant basis plus the entire post-default receipt amount, paid at par instead of `defaultRecoveryPrice`.

### Likelihood Explanation
Medium. Requires the pool to reach `defaultRecoveryFinalized` with the attacker holding an instant receipt stamped under an epoch other than `defaultRecoveryEpoch`, or the CDO's `requestInstantWithdraw` entry point remaining callable after default finalization (the strategy side performs no `defaulted`/`defaultRecoveryFinalized` gating at all, unlike `requestWithdraw`). KYC/whitelist checks do not mitigate it — any allowed lender qualifies. The reserve guard limits the payout to non-reserve balance, but any positive non-reserve balance is sufficient for a partial theft at par.

### Recommendation
Mirror the normal-withdraw post-default flow for instant withdrawals:

- In `requestInstantWithdraw`, revert when `defaultRecoveryFinalized` and the user has any outstanding instant/normal receipt, or route post-default instant requests through a reserve-backed path like `postDefaultRequests`.
- In `claimInstantWithdrawRequest`, clear and haircut **all** per-epoch unfunded slices (iterate or track the user's unfunded epochs), not only `instantWithdrawsRequestsByEpoch[user][defaultRecoveryEpoch]`; alternatively record each post-default instant request into `postDefaultRequests` so it is funded by `defaultRecoveryReserve` before becoming claimable.
- Decrement `pendingInstantWithdraws` for every receipt paid at par, not just the default-epoch slice, so the unfunded basis cannot be double-counted.

### Proof of Concept
Foundry fork sketch (extend `test/foundry/IdleCreditVault.t.sol` setup):

```solidity
// 1. Deposit AA for attacker, start epoch, requestInstantWithdraw in epoch N-1.
//    Do NOT let the CDO collect via collectInstantWithdrawFunds (receipt stays unfunded,
//    pendingInstantWithdraws = R1).
// 2. Let epoch roll: stopEpoch/startEpoch so the request epoch != upcoming default epoch.
// 3. Borrower defaults in epoch N: stopEpoch(0,0) -> defaulted; finalizeDefault ->
//    finalizeDefaultRecovery sets defaultRecoveryPrice < 1e18 and
//    defaultInstantWithdrawsFinalized = true.
// 4. Attacker calls cdoEpoch.claimInstantWithdrawRequest().
//    - _claimDefaultedInstantWithdrawRequest clears only the epoch-N slice (0 for attacker
//      if their receipt is from N-1).
//    - instantWithdrawsRequests[attacker] (= R1) is burned and paid at par via
//      _transferFundedClaim, while pendingInstantWithdraws still counts R1 as unfunded.
// 5. assert attacker received R1 (not R1 * defaultRecoveryPrice / 1e18) and that
//    pendingInstantWithdraws was not decremented for the paid slice.
// Variant: after finalization, call requestInstantWithdraw (no defaultRecoveryFinalized
//    gate) to mint a receipt under epochNumber != defaultRecoveryEpoch, then claim it at par.
```

Note: step 4's profitability depends on non-reserve underlying balance existing at the strategy; verify in the PoC whether the CDO instant-withdraw entry point remains callable post-default, since the strategy contract itself imposes no such gate.