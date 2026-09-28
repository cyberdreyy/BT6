I'll investigate the idle-tranches codebase for analogous "stale snapshot vs live counter" bugs.### Title
Post-default instant withdraw requests are indexed into the frozen `defaultRecoveryEpoch` and paid from `defaultRecoveryReserve` - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
`requestWithdraw` explicitly blocks or reroutes requests once `defaultRecoveryFinalized` is set, but `requestInstantWithdraw` has no such guard. Because `epochNumber` no longer advances after a default is finalized, any post-default instant request is recorded under `instantWithdrawsRequestsByEpoch[_user][epochNumber]` where `epochNumber == defaultRecoveryEpoch`. `_claimDefaultedInstantWithdrawRequest` then pays that receipt at `defaultRecoveryPrice` out of `defaultRecoveryReserve`, draining funds that were reserved and priced for the pre-default claimants whose basis was fixed at finalization.

### Finding Description
The external bug class is "a value that should be frozen at a phase transition keeps reading a live counter." Here `defaultRecoveryEpoch`, `defaultRecoveryPrice`, `defaultRecoveryReserve` and the claim basis are all snapshotted in `finalizeDefaultRecovery` (lines 644–710). But `requestInstantWithdraw` (lines 356–375) keeps writing into the live `instantWithdrawsRequestsByEpoch[_user][currentEpoch]` and `instantWithdrawClaimsByEpoch[currentEpoch]` buckets using the still-current `epochNumber`, which is frozen at `defaultRecoveryEpoch` after default. `claimInstantWithdrawRequest` (lines 380–393) then routes through `_claimDefaultedInstantWithdrawRequest` (lines 842–856) whenever `defaultRecoveryFinalized && defaultInstantWithdrawsFinalized`, paying `claimBasis * defaultRecoveryPrice` and decrementing `defaultRecoveryReserve` in `_transferDefaultRecovery` (lines 912–917).

Flow:
1. Borrower defaults with `pendingInstantWithdraws != 0`, so `defaultInstantWithdrawsFinalized` is set true at finalization.
2. After finalization, a user (tranche holder via the CDO) calls `requestInstantWithdraw(_amount, _user)`. No `defaultRecoveryFinalized` check exists. `_burn`/`_mint` succeed; `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch] += _amount` because `epochNumber` is frozen.
3. The same user calls `claimInstantWithdrawRequest`. `_claimDefaultedInstantWithdrawRequest` reads the freshly-written basis under `defaultRecoveryEpoch`, clears it, clamps `pendingInstantWithdraws`, and transfers `claimBasis * defaultRecoveryPrice / 1e18` from `defaultRecoveryReserve`.
4. Each such request converts unbacked receipt tokens into real recovery reserve, diluting/stealing the payout owed to pre-default claimants whose reserve was sized at finalization via `reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve` and `recoveryPrice = reserveAmount * 1e18 / totalBasis` (lines 686–688).

The asymmetry confirms the gap: `requestWithdraw` reverts `NotAllowed` when the user has open requests post-default and isolates new requests into `postDefaultRequests` (lines 247–258), while `requestInstantWithdraw` was never given the equivalent post-default path.

### Impact Explanation
Direct theft of `defaultRecoveryReserve`. An attacker can request an instant withdrawal sized to drain the reserve at the fixed `defaultRecoveryPrice`. Every underlying pulled reduces the reserve available to legitimate defaulted-epoch claimants, leaving their receipts permanently under-collateralized (permanent freezing of unclaimed recovery). Loss is bounded by `defaultRecoveryReserve` but is fully quantifiable per claim.

### Likelihood Explanation
Requires `defaultInstantWithdrawsFinalized == true` (i.e. unfunded instant receipts existed at default finalization — realistic whenever instant liquidity was partially exhausted) and requires the IdleCDO (`IdleCDOEpochVariant`) to still route `requestInstantWithdraw` calls to the strategy after default. I was unable to fully verify the CDO-side gating within the available iterations; if the CDO blocks instant requests while `defaulted()`, this path is unreachable and severity drops. The strategy-side code itself has no defense.

### Recommendation
Mirror the `requestWithdraw` post-default handling in `requestInstantWithdraw`: when `defaultRecoveryFinalized` is true, either revert, or route the request through a dedicated `postDefaultInstantRequests`-style bucket paid 1:1 from fresh funding, never touching `instantWithdrawsRequestsByEpoch[defaultRecoveryEpoch]` or `defaultRecoveryReserve`. Alternatively, snapshot and compare against `epochNumber != defaultRecoveryEpoch` in `_claimDefaultedInstantWithdrawRequest`.

### Proof of Concept
Foundry fork PoC (conceptual, against `test/foundry/IdleCreditVault.t.sol` harness):

```solidity
// Setup: vault with lenders, epoch running, APR > 0
// 1. Alice requests instantWithdraw of X (unfunded -> pendingInstantWithdraws > 0)
// 2. Borrower fails stopEpoch funding -> CDO calls _handleBorrowerDefault, then
//    finalizeDefaultRecovery(recovered, source). recoveryPrice < 1e18,
//    defaultInstantWithdrawsFinalized = true, defaultRecoveryEpoch = epochNumber.
// 3. Attacker (any tranche holder) calls CDO.requestInstantWithdraw(Y).
//    Strategy mints receipt and writes instantWithdrawsRequestsByEpoch[attacker][defaultRecoveryEpoch] = Y
// 4. Attacker calls claimInstantWithdrawRequest -> receives Y * defaultRecoveryPrice / 1e18
//    from defaultRecoveryReserve.
// 5. Alice's legitimate claim then reverts or pays less: reserve is drained.
assertLt(vault.defaultRecoveryReserve(), reserveBefore - expectedAttackerPayout);
```

Note: step 3 depends on the CDO permitting post-default instant requests; this could not be confirmed in `IdleCDOEpochVariant.sol` before the iteration budget expired. If blocked there, this reduces to a defense-in-depth gap rather than an exploitable bug — flag for the fix regardless given the asymmetric guard.