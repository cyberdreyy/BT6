### Title
Unprivileged instant-withdraw request permanently bricks withdraw requests and loss-adjusted `stopEpoch` on upgraded (non-initialized) `IdleCreditVault`s - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The external bug (CVE-2022-49844) is a variant-specific state read: `priv->ctrlmode` is dereferenced on virtual CAN interfaces that never allocate `struct can_priv`, so the drop check misbehaves for interface types the original commit did not consider. The analog in idle-tranches is the lazy-migration flag `defaultRecoveryInitialized`. Upgraded vaults carry pre-upgrade pending receipts that lack the new per-epoch ownership data (`withdrawsRequestsByEpoch`, `instantWithdrawsRequestsByEpoch`, `apr0Users`). Instead of tolerating that missing state like vcan tolerates missing `can_priv`, `_ensureDefaultRecoveryInitialized()` reverts whenever `pendingInstantWithdraws != 0`, and it is called on the request and default/loss paths — letting any unprivileged user permanently gate key flows on a legacy vault.

### Finding Description
`requestWithdraw` and `requestInstantWithdraw` both call `_ensureDefaultRecoveryInitialized()` at the top:

```
requestWithdraw:  contracts/strategies/idle/IdleCreditVault.sol:245
requestInstantWithdraw: contracts/strategies/idle/IdleCreditVault.sol:358
```

`_ensureDefaultRecoveryInitialized` unconditionally reverts while `pendingInstantWithdraws != 0` for vaults whose flag is still `false` (i.e., vaults upgraded from an implementation that predates this accounting — `initialize` only sets it at line 147 for fresh deployments):

```
if (pendingInstantWithdraws != 0) revert NotAllowed();   // line 926
```

`pendingInstantWithdraws` is only decremented by `collectInstantWithdrawFunds` (line 401), which requires the CDO to actually pull funded underlyings at a subsequent `startEpoch`. If the borrower does not return enough cash — or simply before the next epoch funding occurs — the flag can never flip.

The same wall exists on the loss path: `collectWithdrawFunds` reverts `NotAllowed` for a non-initialized vault whenever `_amount < pendingBasis` (lines 414–416), i.e., any `stopEpochWithDuration` that realizes a loss reverts, and `previewLossAdjustedWithdrawFunds` hits the same guard at line 453. `finalizeDefaultRecovery` also calls `_ensureDefaultRecoveryInitialized` (line 663).

Broken invariant: withdraw-request liveness and epoch-close liveness must not depend on an unprivileged user's single instant-withdraw request. Here one `requestInstantWithdraw` call on an upgraded vault sets `pendingInstantWithdraws > 0` and thereafter:

- every `requestWithdraw`/`requestInstantWithdraw` from any user reverts until the instant queue is fully funded;
- any `stopEpochWithDuration` with `_lossAmount > 0` reverts in `collectWithdrawFunds`/`previewLossAdjustedWithdrawFunds`, so the manager cannot close an epoch with a realized loss;
- if default occurs while unfunded instant receipts exist, `finalizeDefaultRecovery` reverts (recovery can never finalize — though the default-triggered part alone would be out of scope, it compounds the freeze).

No existing guard helps: the skim/`_transferFundedClaim` reserve check (lines 897–906) is downstream, `NotAllowed` here is the bug itself, and there is no privileged "force init" escape — `setCanTransfer` only clears a deprecated flag.

### Impact Explanation
An unprivileged tranche holder (any user able to call `withdrawAA/BB` → `requestInstantWithdraw` on the CDO) can freeze all new withdraw requests on an upgraded vault for the remainder of the epoch, and — critically — prevents the manager from closing the epoch with a realized loss, because `collectWithdrawFunds` reverts whenever the funded amount is below `pendingBasis`. Funds are frozen at least until the instant queue is fully funded; if borrower repayment is short (the exact scenario `stopEpochWithDuration` exists for), the freeze is effectively permanent for the loss-adjusted path, trapping all LP principal in the vault. Quantified loss: 100% of vault TVL temporarily frozen, and permanently frozen on the loss-adjusted epoch-close path.

### Likelihood Explanation
Requires an `IdleCreditVault` upgraded in place (flag `false`) — a supported migration path explicitly handled by the lazy-init code — plus one unprivileged instant-withdraw request while `pendingInstantWithdraws == 0`. Attacker cost is one transaction; no privileged cooperation needed. The freeze on `stopEpochWithDuration` materializes exactly when the pool needs it most (under-funded repayment).

### Recommendation
Do not gate `requestWithdraw`/`requestInstantWithdraw` on `pendingInstantWithdraws` for legacy vaults: treat legacy instant receipts as an aggregate bucket (analogous to how `pendingWithdraws` legacy receipts are fully funded at par in `collectWithdrawFunds` line 425), or provide an owner/manager `initializeDefaultRecovery()` migration that snapshots `pendingInstantWithdraws` into `instantWithdrawClaimsByEpoch[epochNumber]` so finalization can proceed. At minimum, allow `collectWithdrawFunds`/`previewLossAdjustedWithdrawFunds` to fully fund legacy pending receipts (`pendingToFund = pendingBasis`) without the init flag.

### Proof of Concept
Foundry fork sketch (against a mainnet-forked deployed vault upgraded to the new implementation, where `defaultRecoveryInitialized == false`):

```solidity
// test: legacy vault, flag == false, pendingInstantWithdraws == 0
IdleCreditVault vault = IdleCreditVault(LEGACY_VAULT);
IdleCDOEpochVariant cdo = IdleCDOEpochVariant(vault.idleCDO());

// 1. Attacker (any tranche holder) requests an instant withdraw via CDO
vm.prank(attacker);
cdo.withdrawBBInstant(amount);           // -> vault.requestInstantWithdraw
assertGt(vault.pendingInstantWithdraws(), 0);

// 2. Any subsequent withdraw request reverts (blocked by lazy init)
vm.prank(victim);
cdo.depositBB(x); // funded earlier
vm.prank(victim);
vm.expectRevert(NotAllowed.selector);
cdo.withdrawBB(y);                        // -> vault.requestWithdraw -> _ensureDefaultRecoveryInitialized reverts

// 3. Manager tries to close epoch with partial repayment (loss)
vm.prank(manager);
vm.expectRevert(NotAllowed.selector);
cdo.stopEpochWithDuration(duration, lossAmount); // -> collectWithdrawFunds(_amount < pendingBasis) reverts
// epoch cannot be closed with the realized loss; LP funds remain locked
```