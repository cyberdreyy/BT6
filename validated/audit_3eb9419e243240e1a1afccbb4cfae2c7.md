### Title
Instant-withdraw claims pay unfunded post-default receipts at par after only the defaulted-epoch basis is cleared - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`claimInstantWithdrawRequest` in `IdleCreditVault` follows a "validate the staged part, then commit the whole write" pattern analogous to the OpenClaw staged-write escape. After default recovery is finalized, it clears and haircuts only the receipt recorded for `defaultRecoveryEpoch` (`instantWithdrawsRequestsByEpoch[_user][defaultEpoch]`), then pays the entire remaining `instantWithdrawsRequests[_user]` balance at par via `_transferFundedClaim` — with no check that those receipts were actually funded by `collectInstantWithdrawFunds`. The guard protects the defaulted-epoch basis (the "validated parent"), while a receipt materialized after the default epoch (the "temp file outside the verified parent") slips into the same aggregate payout and is committed at full value, stealing strategy reserves.

### Finding Description
In `claimInstantWithdrawRequest` (`IdleCreditVault.sol:380-393`): [1](#0-0) 

- `_claimDefaultedInstantWithdrawRequest` only clears `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` and pays it at `defaultRecoveryPrice` (`IdleCreditVault.sol:842-856`).
- Afterward `amount = instantWithdrawsRequests[_user]` — which still contains *every other* epoch's uncleared receipt — is burned and paid 1:1 from strategy-held underlying by `_transferFundedClaim`, with no per-epoch funding check.
- `collectInstantWithdrawFunds` (`IdleCreditVault.sol:398-403`) is what actually funds receipts: it decrements `pendingInstantWithdraws` and pulls tokens from the CDO. A receipt recorded in `instantWithdrawsRequests`/`instantWithdrawsRequestsByEpoch` but never collected is unfunded, yet `claimInstantWithdrawRequest` pays it identically to a funded one — the only thing standing between an unfunded receipt and a payout is the CDO-side gating on *when* claims may run, not *which* receipts are backed.
- `requestInstantWithdraw` (`IdleCreditVault.sol:356-375`) records a new receipt in the *current* `epochNumber` and calls `_ensureDefaultRecoveryInitialized()` but does **not** revert when `defaultRecoveryFinalized` is true — unlike `requestWithdraw` (`IdleCreditVault.sol:247-257`), which explicitly blocks new requests until old funded receipts are claimed. So if the CDO still routes an instant request in the post-default window (e.g., during a resumed epoch where `lastEpochApr` dropped below `unscaledApr - instantWithdrawAprDelta`, see `IdleCDOEpochVariant.sol:761-769`), that new receipt lands in a *different* epoch bucket than `defaultRecoveryEpoch`, survives `_claimDefaultedInstantWithdrawRequest`, and is swept into the par-value payout.

This is the direct analog of the advisory: the final guarded step (per-epoch defaulted-basis clearing, like the guarded rename) validates only the staged portion, while materialization into the aggregate (`instantWithdrawsRequests[_user] += _amount`, like temp-file creation) is not pinned to a funded/verified context before commit.

### Impact Explanation
Direct theft / insolvency. The attacker receives `instantWithdrawsRequests[_user]` at par from strategy-held underlying even though part of that balance was never funded by `collectInstantWithdrawFunds`. Those tokens are the reserve earmarked for other users' funded claims and default-recovery distributions (`defaultRecoveryPrice` payouts via `_transferDefaultRecovery`). The quantified loss equals the unfunded receipt amount `X` minted post-default: the attacker burns `X` receipt tokens and receives `X` underlying instead of `X * defaultRecoveryPrice / RECOVERY_FULL` (or nothing), directly draining up to the strategy's funded balance and causing a shortfall for honest claimants — i.e., broken "one receipt one payout" and loss-waterfall invariants.

### Likelihood Explanation
Requires a borrower default followed by recovery finalization, then a new epoch in which the instant-withdraw trigger condition (`lastEpochApr > unscaledApr + instantWithdrawAprDelta`) holds and `allowAAWithdrawRequest`/`allowBBWithdrawRequest` are still enabled — all consistent with the code's own admission that post-default epochs can run (`postDefaultRequests` flow exists for normal withdrawals). The instant path lacks the `defaultRecoveryFinalized` guard that the normal path has, which reads as an oversight rather than intent: post-default normal requests are stored in the segregated `postDefaultRequests` bucket paid from the recovery reserve, while instant requests go into the general funded-claim aggregate. The attacker's cost is one `requestWithdraw` transaction; the prank sequence is fully permissionless given KYC (attacker is a whitelisted lender).

### Recommendation
Mirror the fix pattern from the advisory — pin the claim to a verified funded context before paying:

1. In `requestInstantWithdraw`, revert (or route into `postDefaultRequests`) when `defaultRecoveryFinalized` is true, exactly as `requestWithdraw` does at `IdleCreditVault.sol:247-257`.
2. In `claimInstantWithdrawRequest`, pay per-epoch only amounts that were collected (track a funded-per-epoch counter alongside `instantWithdrawClaimsByEpoch`, or require `pendingInstantWithdraws` coverage for the residual amount) instead of paying the aggregate `instantWithdrawsRequests[_user]` unconditionally.
3. Alternatively, clear `instantWithdrawsRequestsByEpoch` entries on funded claims and, in the post-default branch, iterate and pay only epochs marked funded, haircutting any basis that was never collected.

### Proof of Concept
Foundry fork scenario (mode: instant withdrawals enabled, fixed-APR):

```solidity
// Assume: user1 (attacker) and user2 both hold AA tranches; pool runs epoch N.
// 1. Epoch N runs; borrower defaults -> cdoEpoch.stopEpoch(0,0) with borrowerBalance=0.
// 2. owner calls finalizeDefault(recovered, manager); defaultRecoveryFinalized = true,
//    defaultRecoveryEpoch = N, defaultRecoveryPrice < RECOVERY_FULL.
// 3. Pool resumes: manager calls startEpoch(); manager sets a lower APR at next
//    stopEpoch so that lastEpochApr > unscaledApr + instantWithdrawAprDelta.
// 4. Attacker calls cdoEpoch.requestWithdraw(attackAmount, AAtranche).
//    IdleCDOEpochVariant.requestWithdraw routes to requestInstantWithdraw
//    (IdleCDOEpochVariant.sol:761-768): burns CDO strategy tokens, mints
//    attackAmount receipt tokens to attacker, records
//    instantWithdrawsRequestsByEpoch[attacker][M] += attackAmount  (M > defaultRecoveryEpoch)
//    and pendingInstantWithdraws += attackAmount. NO funds are collected.
// 5. Attacker calls cdoEpoch.claimInstantWithdrawRequest() ->
//    strategy.claimInstantWithdrawRequest(attacker):
//      - _claimDefaultedInstantWithdrawRequest clears only epoch N basis (0 for attacker).
//      - amount = instantWithdrawsRequests[attacker] = attackAmount.
//      - _burn + _transferFundedClaim pays attackAmount underlying AT PAR from the
//        strategy balance funded by collectInstantWithdrawFunds / recovery reserve.
// 6. assertEq(underlying.balanceOf(attacker) - balPre, attackAmount);
//    Honest users' defaulted receipts at epoch N can only claim
//    basis * defaultRecoveryPrice / RECOVERY_FULL; strategy balance is now short
//    by attackAmount -> later _transferDefaultRecovery calls revert/underpay.
```

The key check in the PoC is step 5: `instantWithdrawsRequestsByEpoch[attacker][defaultRecoveryEpoch] == 0`, so the defaulted-epoch guard is a no-op, yet the aggregate payout still commits — the receipt was "materialized" outside the validated (funded or defaulted-haircut) context.

Note on certainty: the exploitability hinges on the CDO permitting an instant-withdraw route during a post-default epoch (step 3-4). The code explicitly blocks this for the normal path but not for `requestInstantWithdraw`, and instant withdrawals are only disabled in programmable-borrower mode (`_isInstantWithdrawEnabled`, `IdleCDOEpochVariant.sol:839-841`), supporting the reading that this gap is reachable. I was not able to fully confirm within the available context that `getInstantWithdrawFunds`/epoch gating cannot veto a claim in that window; if the CDO unconditionally blocks claims while unfunded instant receipts exist, the residual risk degrades to the funded-but-wrong-epoch case, where an old receipt funded pre-default is still paid at par alongside — which the code comments acknowledge but only handle via `pendingInstantWithdraws` bookkeeping, not per-epoch funding checks.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L380-393)
```text
  function claimInstantWithdrawRequest(address _user) external {
    _onlyIdleCDO();
    if (defaultRecoveryFinalized && defaultInstantWithdrawsFinalized) {
      // Clear the defaulted-epoch instant receipt first, then continue so the same call can
      // also pay any older instant receipt that was already funded before default finalization.
      _claimDefaultedInstantWithdrawRequest(_user);
    }
    uint256 amount = instantWithdrawsRequests[_user];
    // burn strategy tokens from user
    _burn(_user, amount);

    instantWithdrawsRequests[_user] = 0;
    _transferFundedClaim(_user, amount);
  }
```
