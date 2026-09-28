I have limited iterations left — let me check whether post-default instant withdraw requests are reachable via the CDO and how `requestWithdraw` routes.### Title
Post-default instant withdraw receipts inherit the finalized recovery context and drain `defaultRecoveryReserve` at par - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
`IdleCreditVault.requestInstantWithdraw` is the only receipt-creation entry point that does not account for `defaultRecoveryFinalized`. A new instant request opened after default finalization is recorded into `instantWithdrawsRequestsByEpoch[user][epochNumber]`, where `epochNumber` is frozen at `defaultRecoveryEpoch`, and its claim is paid either at par from strategy-held underlying (the recovery reserve) or through the default-epoch haircut path that was sized only for pre-default receipts. The new request inherits a funding/recovery context it was never part of — the same "stale context leaked to a subsequent request" class as the Spring gRPC SecurityContext bug.

### Finding Description
In `requestInstantWithdraw` (`IdleCreditVault.sol:356-375`) the function burns CDO strategy tokens, mints the user a receipt, and pushes accounting into `instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch[_user][currentEpoch]`, `instantWithdrawClaimsByEpoch[currentEpoch]`, and `pendingInstantWithdraws` — with no `defaultRecoveryFinalized` branch.

Contrast with `requestWithdraw` (`IdleCreditVault.sol:247-257`), which explicitly handles the post-default case: it reverts if the user has any outstanding receipt (`_hasWithdrawRequest`, `instantWithdrawsRequests`, `postDefaultRequests`) and routes new requests into the isolated `postDefaultRequests` bucket paid 1:1 from the reserve. Instant requests get no such isolation.

After `finalizeDefaultRecovery` (`IdleCreditVault.sol:661-710`), `defaultRecoveryEpoch = epochNumber` and `epochNumber` never advances again (the pool is defaulted, no further `stopEpoch`/`deposit` epoch bumps). Two leak paths result:

1. **`defaultInstantWithdrawsFinalized == false`** (all instant receipts were funded before default): `claimInstantWithdrawRequest` skips `_claimDefaultedInstantWithdrawRequest`, then pays the *entire* `instantWithdrawsRequests[_user]` at par via `_transferFundedClaim` (`IdleCreditVault.sol:380-393`). The only underlying sitting in the strategy post-finalization is `defaultRecoveryReserve`, so a brand-new request — created at post-haircut tranche prices — redeems 1:1 against reserve money earmarked for defaulted-epoch claimants.

2. **`defaultInstantWithdrawsFinalized == true`**: `_claimDefaultedInstantWithdrawRequest` (`IdleCreditVault.sol:842-856`) clears `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]`, which now includes the attacker's *new* post-default request. It pays `claimBasis * defaultRecoveryPrice` out of the reserve while also zeroing `pendingInstantWithdraws` (`claimBasis >= pending → 0`), permanently destroying the pending-accounting invariant the finalization relied on. Either way the reserve is consumed by claims that were never haircut into `totalBasis` at finalization, so legitimate defaulted-epoch claimants (`_transferDefaultRecovery`, `DefaultDistributor.claim`) find the reserve drained or accounting underflowed.

The attacker is an ordinary KYC'd lender holding tranche tokens; the sequence only wraps honest-manager epoch/default calls, matching the external report's model where a subsequent same-"thread" request silently inherits the previous request's privileged context.

### Impact Explanation
Direct theft: post-finalization instant requests are paid out of `defaultRecoveryReserve` at par (or at the stale haircut price while corrupting `pendingInstantWithdraws`/`instantWithdrawClaimsByEpoch`), extracting reserve funds that belong to defaulted-epoch withdraw and instant-withdraw claimants. Loss equals the attacker's request size, bounded by the reserve; the last legitimate claimants are permanently underpaid or revert on underflow.

### Likelihood Explanation
Requires an instant-withdraw-enabled credit vault where the borrower defaults and `finalizeDefaultRecovery` completes. After that, any tranche holder can trigger the path with a single redeem-shaped request — no privileged cooperation, timing race, or economic prerequisite beyond holding tranche tokens. The missing `defaultRecoveryFinalized` check is a clear asymmetry versus `requestWithdraw`, indicating the post-default instant path was not considered.

### Recommendation
Mirror `requestWithdraw`'s post-default handling in `requestInstantWithdraw`: revert on new instant requests once `defaultRecoveryFinalized` is true (or route them through an isolated `postDefaultRequests`-style bucket), and in `claimInstantWithdrawRequest` bound the defaulted-epoch clearing to basis recorded at or before `defaultRecoveryEpoch` so newly-created receipts cannot be swept into the finalized haircut bucket.

### Proof of Concept
Foundry fork sketch (based on `test/foundry/IdleCreditVault.t.sol` helpers):

```solidity
// Setup: instant-enabled IdleCDOEpochVariant + IdleCreditVault.
idleCDO.depositAA(amountWei);          // attacker and other LPs deposit
_startEpochAndCheckPrices(0);

// Honest LP opens an instant withdraw; manager funds it partially, then borrower defaults.
// Manager calls stopEpoch with default -> CDO calls _handleBorrowerDefault ->
// strategy.finalizeDefaultRecovery(recovered, recoverySource).

// Post-finalization state: epochNumber == defaultRecoveryEpoch (frozen),
// defaultRecoveryReserve > 0, pendingInstantWithdraws == 0 case:
assertTrue(strategy.defaultRecoveryFinalized());
uint256 reserve = strategy.defaultRecoveryReserve();

// Attacker opens a NEW instant request at post-haircut tranche prices.
vm.prank(attacker);
cdoEpoch.requestInstantWithdraw(attackAmount);   // -> strategy.requestInstantWithdraw
// receipt recorded under instantWithdrawsRequestsByEpoch[attacker][defaultRecoveryEpoch]

// Claim pays at par from strategy balance == defaultRecoveryReserve.
vm.prank(attacker);
cdoEpoch.claimInstantWithdrawRequest();
// attacker received `attackAmount` underlying 1:1 although finalization never
// included it in totalBasis -> reserve drained, later defaulted-epoch claims
// revert on _transferDefaultRecovery underflow or are underpaid.
```

Note: I could not fully verify within the available search budget whether `IdleCDOEpochVariant.requestWithdraw`/instant routing gates on `defaulted()` before forwarding to `requestInstantWithdraw`; if the CDO blocks instant requests post-default, this path is unreachable and the finding reduces to a defense-in-depth gap. The strategy-level guard asymmetry itself is concrete in `IdleCreditVault.sol:356-375` vs `:247-257`.