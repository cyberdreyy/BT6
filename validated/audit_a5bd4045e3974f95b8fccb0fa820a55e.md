### Title
Legacy (uninitialized) per-epoch receipt storage permanently bricks default finalization — `finalizeDefaultRecovery` reverts forever when an unfunded pre-upgrade pending withdrawal exists - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The `gfx-auxil` bug is a use-of-uninitialized-resource: code trusts a buffer whose contents were never written. The direct analog in this repo is the appended default-recovery storage in `IdleCreditVault`. `withdrawsRequestsByEpoch`, `instantWithdrawsRequestsByEpoch`/`instantWithdrawClaimsByEpoch`, `apr0Users`, `postDefaultRequests` and `defaultRecoveryInitialized` were appended to an already-deployed strategy. For receipts created before the upgrade these slots are *uninitialized* (read as zero) even though the legacy aggregates `pendingWithdraws` and `withdrawsRequests[_user]` are non-zero. The code compensates via lazy initialization in `_ensureDefaultRecoveryInitialized()`, but that initializer refuses to run precisely in the state where it is most needed — a defaulted pool with a legacy unfunded receipt — so `finalizeDefaultRecovery` can never succeed.

### Finding Description
`_ensureDefaultRecoveryInitialized()` is invoked at the top of `finalizeDefaultRecovery` and reverts whenever `pendingWithdraws != 0` while `cdo.epochEndDate() != 0 || cdo.defaulted()`:

```solidity
// contracts/strategies/idle/IdleCreditVault.sol:924-935
function _ensureDefaultRecoveryInitialized() internal {
  if (defaultRecoveryInitialized) return;
  if (pendingInstantWithdraws != 0) revert NotAllowed();
  if (pendingWithdraws != 0) {
    IIdleCDOEpochVariant cdo = IIdleCDOEpochVariant(idleCDO);
    if (cdo.epochEndDate() != 0 || cdo.defaulted()) revert NotAllowed();
    pendingWithdraws = 0;
    apr0TotalPrincipal = 0;
  }
  defaultRecoveryInitialized = true;
  canTransfer = false;
}
```

Two facts combine into a deadlock:

1. `finalizeDefaultRecovery` requires `cdo.defaulted() == true` (`contracts/strategies/idle/IdleCreditVault.sol:666`) and calls `_ensureDefaultRecoveryInitialized()` first (line 663).
2. On a defaulted pool with `pendingWithdraws != 0`, `_ensureDefaultRecoveryInitialized` unconditionally reverts via `cdo.defaulted()` (line 929).

`pendingWithdraws` is only decremented by `collectWithdrawFunds` (line 420/425) during a successful `stopEpoch` funding. In a default (`stopEpochWithDuration` with an unrecoverable loss, or borrower funding failure), the borrower never funds the pending bucket, so `pendingWithdraws` stays non-zero forever. There is no other code path that clears it — `requestWithdraw`'s lazy init hits the same revert, and `pendingInstantWithdraws != 0` reverts with no escape at all.

The "uninitialized resource" aspect: the code cannot reconstruct per-epoch ownership for legacy receipts (comment at line 415: "Legacy receipts do not have per-epoch ownership data"), so it reads the never-written `withdrawsRequestsByEpoch`/`instantWithdrawClaimsByEpoch` mappings as authoritative zero and gates everything on the aggregate `pendingWithdraws` instead — and then makes the only escape hatches unreachable in the default state.

### Impact Explanation
Permanent freezing of all lender funds. Once a borrower defaults while a legacy (pre-upgrade) pending withdrawal or instant-withdraw receipt exists, `finalizeDefault` → `finalizeDefaultRecovery` reverts on every call. Consequences:

- `defaultRecoveryFinalized` never becomes true, so no holder of AA/BB tranches can claim any recovered funds, and pending receipt holders cannot claim at all.
- Any recovered capital sitting in the strategy or offered by `_recoverySource` is locked permanently.
- Even partial: `pendingInstantWithdraws != 0` alone bricks initialization permanently with no recovery path, since nothing decrements `pendingInstantWithdraws` except `collectInstantWithdrawFunds` (IdleCDO-driven, requires funded claims).

Quantified loss: the entire vault NAV plus all pending receipts (100% of TVL) is frozen permanently, not just delayed.

### Likelihood Explanation
The trigger requires (a) the strategy to be an upgraded deployment whose `defaultRecoveryInitialized` storage slot is still `false`, and (b) a pending withdraw/instant receipt that is never funded before default. No privileged malicious action is needed: an unprivileged KYC'd lender creates the receipt via `requestWithdraw`/`requestInstantWithdraw` before the upgrade (or between upgrade and lazy init), and any subsequent honest borrower default — an ordinary, expected protocol event — permanently locks everything. `requestWithdraw` even enshrines this state: the lazy-init on a fresh request reverts while `pendingWithdraws != 0` on a running pool, so the flag can only be cleaned in the narrow window where the legacy bucket is fully funded or the pool is closed but not defaulted. The `epochEndDate() == 0` branch shows the authors knew closed pools can clear stale counters; defaulted pools were left with no equivalent path.

### Recommendation
Split the lazy initializer into a version callable during default finalization:

- In `finalizeDefaultRecovery`, allow `_ensureDefaultRecoveryInitialized` (or a dedicated migration branch) to run when `cdo.defaulted()` is true. Legacy pending receipts are already included in `defaultPendingClaimBasis()` via `pendingWithdraws`, so their haircutted share is correctly reserved in `defaultRecoveryReserve`; the missing piece is only the per-epoch attribution for `_claimDefaultedWithdrawRequest`.
- For legacy receipts whose per-epoch data is unrecoverable, fall back to paying them from `defaultRecoveryReserve` at `defaultRecoveryPrice` keyed off the aggregate `withdrawsRequests[_user]` / `instantWithdrawsRequests[_user]` when `withdrawsRequestsByEpoch`/`instantWithdrawsRequestsByEpoch` are zero for `defaultRecoveryEpoch`, instead of routing them to `_claimFundedWithdrawRequest` (which can only pay at par and reverts against the reserve guard in `_transferFundedClaim`, lines 899–905).
- Provide an owner/manager escape hatch to clear `pendingInstantWithdraws` for unfunded legacy instant receipts during finalization, mirroring how `defaultInstantWithdrawsFinalized` handles current-epoch instant claims.

### Proof of Concept
Foundry fork sketch (mainnet fork against an upgraded `IdleCreditVault` proxy where `defaultRecoveryInitialized == false` and a legacy receipt exists):

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";

contract LegacyUninitDefaultFreezeTest is Test {
    IdleCreditVault strategy;      // upgraded proxy
    IdleCDOEpochVariant cdoEpoch;  // attached CDO
    IERC20Detailed underlying;
    address manager;
    address borrower;
    address legacyUser;            // lender with pre-upgrade pending receipt

    function testDefaultFinalizationPermanentlyFrozen() external {
        // Precondition: legacyUser called requestWithdraw pre-upgrade.
        // withdrawsRequests[legacyUser] > 0 and pendingWithdraws > 0, but
        // withdrawsRequestsByEpoch[legacyUser][*] == 0 (slot never written)
        // and defaultRecoveryInitialized == false (slot never written).
        assertFalse(strategy.defaultRecoveryInitialized());
        assertGt(strategy.pendingWithdraws(), 0);
        assertGt(strategy.withdrawsRequests(legacyUser), 0);

        // Legacy receipt can never be lazy-initialized on a running pool.
        vm.expectRevert(NotAllowed.selector);
        vm.prank(address(cdoEpoch));
        strategy.requestWithdraw(1, address(0xbeef), 1); // hits _ensureDefaultRecoveryInitialized

        // Borrower defaults: warp past epoch end and stop with no repayment.
        vm.warp(cdoEpoch.epochEndDate() + 1);
        vm.prank(cdoEpoch.owner());
        cdoEpoch.stopEpoch(0, 0);
        assertTrue(cdoEpoch.defaulted());

        // Recovery funds are available, but finalization can never succeed.
        uint256 recovered = strategy.balanceOf(address(cdoEpoch));
        deal(address(underlying), manager, recovered);
        vm.startPrank(manager);
        underlying.approve(address(strategy), recovered);
        vm.expectRevert(NotAllowed.selector); // _ensureDefaultRecoveryInitialized: defaulted() == true
        cdoEpoch.finalizeDefault(recovered, manager);
        vm.stopPrank();

        assertFalse(strategy.defaultRecoveryFinalized());
        // Every future attempt reverts identically: pendingWithdraws is only
        // cleared by collectWithdrawFunds, which a defaulted borrower never calls,
        // and cdo.defaulted() is a latching state. 100% of TVL + receipts frozen.
    }
}
```

Key invariant broken: permanent freezing of unclaimed funds. No existing guard prevents it — `_onlyIdleCDO`, `nonReentrant`, the reserve guard in `_transferFundedClaim`, and the KYC checks all operate normally; the deadlock lives entirely inside `_ensureDefaultRecoveryInitialized`'s refusal to run on a defaulted pool, which is the only caller context that needs it.

Caveat: if `IdleCreditVaultManagerOrchestrator` exposes a manager-callable initializer that clears `pendingWithdraws`/`pendingInstantWithdraws` unconditionally (bypassing the `defaulted()` check), the freeze is reduced to "depends on an honest manager migration call" — the contract code in `IdleCreditVault` itself contains no such path.