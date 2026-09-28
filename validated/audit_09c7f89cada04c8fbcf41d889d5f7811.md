### Title
Post-default `requestInstantWithdraw` bypasses recovery accounting and pays new receipts at par from other users' funded claims - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
Analogous to the Dompdf bug where the validator honors `xlink:href` while the consumer prefers `href`, `IdleCreditVault` maintains two post-default request paths that interpret the same state differently: `requestWithdraw` has an explicit `defaultRecoveryFinalized` branch that routes new requests into the haircut-priced `postDefaultRequests` reserve, while `requestInstantWithdraw` has no such branch. A post-default instant request is recorded under the current (frozen) `epochNumber`, yet `claimInstantWithdrawRequest` only applies the recovery haircut when `defaultInstantWithdrawsFinalized` is set, and otherwise pays `instantWithdrawsRequests` at par through `_transferFundedClaim`.

### Finding Description
When `finalizeDefaultRecovery` runs with `pendingInstantWithdraws == 0`, it sets `defaultRecoveryFinalized = true` but `defaultInstantWithdrawsFinalized = false` (`IdleCreditVault.sol:690-696`). `epochNumber` no longer increments because `deposit` only bumps it while `isEpochRunning()`. In this state:

1. `requestInstantWithdraw` (lines 356-375) still burns CDO strategy tokens and records a fresh receipt in `instantWithdrawsRequests[_user]` / `instantWithdrawsRequestsByEpoch[_user][epochNumber]`, with no `defaultRecoveryFinalized` handling — unlike `requestWithdraw` (lines 247-257), which explicitly redirects post-default requests into the reserve-backed `postDefaultRequests` path and reverts if old claims are outstanding.
2. `claimInstantWithdrawRequest` (lines 380-393) skips `_claimDefaultedInstantWithdrawRequest` because `defaultInstantWithdrawsFinalized` is false, so the by-epoch basis is never cleared and never haircut.
3. It then pays the aggregate `instantWithdrawsRequests[_user]` at par via `_transferFundedClaim`, which only excludes `defaultRecoveryReserve` (lines 897-907) — any other underlying held by the strategy (funded-but-unclaimed normal withdraw receipts collected via `collectWithdrawFunds`, or partial prefunds) is spendable at 1:1.

The attacker burns tranche tokens priced at the post-haircut `virtualPrice`/`defaultRecoveryPrice` and receives par-valued underlying, extracting the difference plus other users' unclaimed funded claims.

### Impact Explanation
Direct theft at a quantified rate: for each unit of post-default instant request, the attacker pays `amount * defaultRecoveryPrice` worth of tranche tokens and receives `amount` underlying, stealing `(1 - defaultRecoveryPrice)` per unit from the strategy's funded-claim balance — i.e., confiscating unclaimed funded withdraw receipts belonging to other users. Invariant broken: "one receipt one payout" and donation/recovery isolation, since a receipt created after the haircut is paid as if funded at par.

### Likelihood Explanation
`allowInstantWithdraw` is explicitly enabled after default (`test/foundry/ProgrammableBorrowerCreditVault.t.sol:831` asserts it), so the CDO-level `requestInstantWithdraw`/`claimInstantWithdrawRequest` path remains callable by any tranche holder after finalization. Preconditions: default finalized with `pendingInstantWithdraws == 0` at finalization, and residual non-reserve underlying in the strategy (unclaimed funded receipts — plausible whenever a user hasn't yet claimed a previously fulfilled withdraw). Uncertainty: I could not fully verify the IdleCDOEpochVariant `requestInstantWithdraw` body post-default; if the CDO reverts instant requests while `defaulted`, the attack is unreachable. Exploitation also fails if the strategy holds no underlying beyond `defaultRecoveryReserve`.

### Recommendation
Mirror the `requestWithdraw` post-default handling in `requestInstantWithdraw`: when `defaultRecoveryFinalized`, either revert or route the request into a reserve-backed/haircut-priced path. Alternatively, in `claimInstantWithdrawRequest`, always clear `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` through `_claimDefaultedInstantWithdrawRequest` whenever `defaultRecoveryFinalized` (not only when `defaultInstantWithdrawsFinalized`), and price any epoch tagged `>= defaultRecoveryEpoch` at `defaultRecoveryPrice` rather than par.

### Proof of Concept
```solidity
// Foundry fork test sketch (requires pool already defaulted and finalized)
// Pre-state: cdoEpoch.defaulted() == true, strategy.defaultRecoveryFinalized() == true,
//            strategy.defaultInstantWithdrawsFinalized() == false (pendingInstantWithdraws was 0),
//            strategy holds fundedReceiptBalance underlying beyond defaultRecoveryReserve.

// 1. Attacker holds AA tranche tokens; virtualPrice reflects defaultRecoveryPrice haircut.
uint256 req = fundedReceiptBalance; // sized to drain unclaimed funded claims
vm.prank(attacker);
cdoEpoch.requestInstantWithdraw(req, address(aaTranche)); // burns attacker tranches at haircut price

// 2. Claim pays par because defaultInstantWithdrawsFinalized == false
//    skips _claimDefaultedInstantWithdrawRequest, takes _transferFundedClaim path.
vm.prank(attacker);
cdoEpoch.claimInstantWithdrawRequest();

// 3. Attacker received `req` underlying; cost was req * defaultRecoveryPrice / 1e18.
//    assertGt(underlying.balanceOf(attacker), attackerCostInUnderlying);
//    Other users' funded withdraw receipts can no longer be paid -> insolvency for them.
```