### Title
Updating `idleCDO` via `setWhitelistedCDO` permanently freezes pending withdraw receipts and claims created under the previous CDO - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.setWhitelistedCDO` lets the owner swap the `idleCDO` pointer at any time with no check on outstanding withdrawal state. All strategy-token movements and claim paths are gated by `msg.sender == idleCDO`. Once the pointer is rotated (e.g., during a CDO implementation/proxy migration), every withdrawal receipt, funded claim, APR0 receipt, and default-recovery claim issued under the old CDO can no longer be serviced, because calls routed through the old `IdleCDOEpochVariant` revert with `NotAllowed`, and the new CDO has no record of the old requests. This is the same bug class as the CSVerifier `WITHDRAWAL_ADDRESS` mismatch: a mutable address used as the sole authorization anchor invalidates pre-existing positions after an upgrade.

### Finding Description
`IdleCreditVault` stores a single `idleCDO` address and authorizes token movements strictly against it:

- `setWhitelistedCDO` (contracts/strategies/idle/IdleCreditVault.sol:954-957) overwrites `idleCDO` with only a zero-address check — no guard on `pendingWithdraws`, `pendingInstantWithdraws`, `defaultRecoveryReserve`, or unsettled per-epoch receipts.
- `_transfer` (IdleCreditVault.sol:939-942) reverts unless `msg.sender == idleCDO`; receipt tokens (the strategy ERC20 that represents funded withdraw claims, per `withdrawsRequests`/`withdrawsRequestsByEpoch`) are minted/moved to users exclusively through the whitelisted CDO during `requestWithdraw`/`claimWithdrawRequest`.
- The claim/disbursement internals (`_transferFundedClaim`, `_transferDefaultRecovery`, `_claimDefaulted*`) are only reached through the whitelisted CDO's `claimWithdrawRequest`/`claimInstantWithdrawRequest`/`_claimDefaulted` entry points.

When the honest owner migrates to a new CDO and calls `setWhitelistedCDO(newCDO)` while the old CDO still has live `requestWithdraw` receipts, queued `epochPendingWithdrawals`, or default-recovery claimants:

1. Any user calling `claimWithdrawRequest` on the old CDO causes the old CDO to call into the strategy as `msg.sender == oldCDO != idleCDO` → `NotAllowed` revert.
2. Any user calling `requestWithdraw`/`claimWithdrawRequest` on the new CDO has no receipt record there (receipts and `withdrawsRequests` entries were created under old-CDO epochs, and the strategy-token receipts could not even be delivered because `_transfer` reverted for the old CDO).
3. Recovery claims (`_transferDefaultRecovery`, `DefaultDistributor.claim` paths that consume `defaultRecoveryReserve`) routed through the superseded CDO are likewise bricked, permanently locking the isolated `defaultRecoveryReserve`.

The analogous "track historical addresses" gap: nothing records the previous `idleCDO`, and there is no dual-acceptance window, so authorization is a strict equality against a single mutable slot — exactly the failure mode the external report describes for `WITHDRAWAL_ADDRESS`.

### Impact Explanation
All underlyings reserved for pending withdrawal receipts, instant withdrawals, and the `defaultRecoveryReserve` become permanently frozen in the strategy. Concretely: `pendingWithdraws` + `pendingInstantWithdraws` + `defaultRecoveryReserve` worth of underlying (which the reserve accounting in `claimWithdrawRequest`/instant-claim paths explicitly protects via the `balance - reserve < _amount` guard at IdleCreditVault.sol:901-906) can never be paid out, because the only contract authorized to move receipts and trigger payouts is no longer the one holding the request state. For a vault with, e.g., 1M USDC of funded pending claims and recovery reserve at rotation time, the entire amount is locked indefinitely; there is no recovery path because `setWhitelistedCDO` cannot be safely rolled back (the new CDO may already have issued fresh requests, and flipping back would freeze those instead).

### Likelihood Explanation
Rotation is a routine operation: `IdleCreditVaultFactory`/upgrade tasks already treat CDO+strategy as separately upgradeable blueprint components (`tasks/tranches-utils.js` `assertCvUpgradeReady` checks strategy/CDO/queue as distinct components), so a CDO replacement that requires repointing `idleCDO` is a realistic admin action rather than an exotic edge. The freeze requires no attacker — it triggers automatically on any pre-existing receipt or pending claim once the pointer changes. An unprivileged user can also force the impact to manifest by holding a receipt across the rotation window; no guard prevents a user from creating `requestWithdraw` receipts immediately before a scheduled migration, maximizing the frozen amount. The only mitigation is operational discipline ("rotate only when fully settled"), which is exactly the kind of implicit invariant the external report flags as insufficient.

### Recommendation
Mirror the report's recommendation:

1. Block `setWhitelistedCDO` while `pendingWithdraws != 0`, `pendingInstantWithdraws != 0`, `defaultRecoveryReserve != 0`, or the current CDO reports a non-settled epoch (`epochEndDate() != 0` / `defaulted()` with pending claims), analogous to the `epochAccountingActive` guard already used in `ProgrammableBorrower.setVault` (contracts/strategies/idle/ProgrammableBorrower.sol:158-170).
2. Alternatively, keep a `previousIdleCDO` (or an `isWhitelistedCDO` mapping) so claims initiated under the old CDO remain serviceable until drained, preserving backward compatibility for legacy receipts.

### Proof of Concept
Conceptual Foundry fork PoC against a live credit vault (USDC variant):

```solidity
// Setup: vault in buffer, user holds a funded pending withdraw receipt
_idleCDO.depositAA(1_000_000e6);           // seed vault
cdoEpoch.requestWithdraw(0, address(AAtranche));   // creates receipt under current CDO
// epoch runs and stops successfully -> receipt becomes funded claim
_startEpoch();
_stopEpoch(apr, expectedFunds);            // pendingWithdraws funded
assertGt(creditVault.pendingWithdraws(), 0);

// Honest owner migrates to a replacement CDO
IdleCDOEpochVariant newCdo = _deployNewCdo(sameStrategy);
vm.prank(owner);
creditVault.setWhitelistedCDO(address(newCdo));    // succeeds: no pending-claims guard

// User's funded claim via the old CDO now reverts
vm.prank(user);
vm.expectRevert(NotAllowed.selector);              // _transfer / claim path: msg.sender != idleCDO
oldCdo.claimWithdrawRequest();

// New CDO has no record of the request either -> claim amount is 0 / reverts
vm.prank(user);
vm.expectRevert();                                 // Is0 / NothingToClaim
newCdo.claimWithdrawRequest();

// Reserved underlyings are locked forever
assertGt(underlying.balanceOf(address(creditVault)), 0);
assertEq(creditVault.pendingWithdraws(), frozenAmount); // never decreases
```

The PoC demonstrates permanent freezing of funded withdrawal claims equal to `pendingWithdraws` (+ `defaultRecoveryReserve` if a default occurred pre-rotation), triggered solely by an authorized-but-unguarded address update — the direct analog of the CSVerifier withdrawal-address mismatch.