### Title
Claimed instant-withdraw receipts keep their per-epoch basis and corrupt default-recovery accounting — ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
The kernel bug is a use-after-free: an async `FREE_STATEID` references an `nfs_server` object that can be released while the operation is still in flight. The analog in `IdleCreditVault` is identical in shape: `claimInstantWithdrawRequest` pays out an instant receipt mid-epoch but leaves the per-epoch bookkeeping (`instantWithdrawsRequestsByEpoch`, `instantWithdrawClaimsByEpoch`) referencing it. If the pool is defaulted and `finalizeDefaultRecovery` runs in the same `epochNumber`, that already-satisfied receipt is counted again as outstanding claim basis and again as "prefunded" reserve, corrupting `defaultRecoveryPrice` for everyone and permanently freezing the tail of default claims.

### Finding Description
`claimInstantWithdrawRequest` (lines 380–393) clears only the aggregate ledgers:

```solidity
uint256 amount = instantWithdrawsRequests[_user];
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);
```

It never touches `instantWithdrawsRequestsByEpoch[_user][epochNumber]` or `instantWithdrawClaimsByEpoch[epochNumber]`, which were both incremented in `requestInstantWithdraw` (lines 371–372). Those per-epoch slots are the "object" the delayed operation (default finalization) later dereferences.

At finalization, two places consume the stale basis while `pendingInstantWithdraws != 0`:

- `defaultPendingClaimBasis` (lines 644–649) adds the full `instantWithdrawClaimsByEpoch[epochNumber]`, including receipts already paid at par.
- `_defaultPrefundedInstantReserve` (lines 716–723) computes `instantBasis - pendingInstant` as funds "already held" for instant claims — but the claimed portion was already transferred out to users, so this phantom amount is booked into `defaultRecoveryReserve` (line 686) without backing tokens.

When the user later claims via `_claimDefaultedInstantWithdrawRequest` (lines 842–856), `claimBasis` is the full per-epoch basis (claimed + unclaimed), while `instantWithdrawsRequests[_user]` and the user's receipt balance only cover the unclaimed remainder, so `instantWithdrawsRequests[_user] -= claimBasis` underflows / `_burn` reverts.

No guard prevents this: `_ensureDefaultRecoveryInitialized` doesn't reconcile the ledgers, and `defaulted()`/`defaultRecoveryFinalized` gating only controls ordering, not consistency.

### Impact Explanation
Direct insolvency / permanent freezing of honest users' default-recovery claims. The phantom `prefundedReserve` inflates `defaultRecoveryPrice` while the reserve is short by exactly the already-claimed amount `A`. Claims are paid out of `defaultRecoveryReserve` in order, so the last claimants' `_transferDefaultRecovery` reverts on insufficient balance — their recovery is permanently frozen (there is no top-up path). Alternatively, if the phantom basis dominates, `defaultRecoveryPrice` is diluted and part of the reserve is stranded as unclaimable dust. Quantified loss: up to the full amount `A` of instant withdrawals claimed during the default epoch, at the cost to the attacker of one small second request `B` left unclaimed.

### Likelihood Explanation
Requires a borrower default to be finalized in the same `epochNumber` in which at least one instant withdrawal was already claimed, with `pendingInstantWithdraws != 0` — i.e., an instant request is claimed mid-epoch, another instant request remains unfunded, and `stopEpoch` defaults before `epochNumber` increments. Instant-withdraw mode and borrower defaults are both normal operating modes; the attacker only needs to call `claimInstantWithdrawRequest` and `requestInstantWithdraw` in the right order — both unprivileged user actions. The manager/owner calls (startEpoch, stopEpoch, finalizeDefault) are honest sequencing the attack wraps around, which matches the threat model.

### Recommendation
In `claimInstantWithdrawRequest`, also clear the per-epoch basis for funded claims: subtract `amount` from `instantWithdrawsRequestsByEpoch[_user][epochNumber]` and `instantWithdrawClaimsByEpoch[epochNumber]` (mirroring what `_claimDefaultedInstantWithdrawRequest` does at lines 847–853). This "pins" the receipt's lifetime: once paid, the epoch-level object no longer references it, so default finalization only sees genuinely outstanding instant claims.

### Proof of Concept
Foundry fork sketch (modeled on `testProcessWithdrawalClaimsInstantEpoch` / `testProcessPostDefaultWithdrawalAsNormalClaim` in `test/foundry/IdleCDOEpochQueue.t.sol`):

```solidity
function testClaimedInstantReceiptCorruptsDefaultRecovery() external {
    _useStandardEpochVariant();
    _stopCurrentEpochWithApr(10e18);           // epoch #1

    vm.prank(manager);
    cdoEpoch.setInstantWithdrawParams(100, 1e18, false);

    address attacker = makeAddr('attacker');
    uint256 tranchesA = _depositWithUser(attacker, 90e6);   // large instant claim A
    _depositWithUser(makeAddr('victim'), 10e6);

    vm.prank(manager);
    cdoEpoch.startEpoch();                     // epochNumber = E, instant pool prefunded

    _requestInstantWithdrawWithUser(attacker, tranchesA);   // request A
    vm.warp(block.timestamp + 101);
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest();    // A paid at par; per-epoch basis NOT cleared

    // second small instant request keeps pendingInstantWithdraws != 0
    _requestInstantWithdrawWithUser(attacker, 1e6);         // request B

    // borrower defaults in the same epochNumber E
    address borrower = strategy.borrower();
    deal(address(underlying), borrower, 0, true);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(cdoEpoch.owner());
    cdoEpoch.stopEpoch(0, 0);
    assertTrue(cdoEpoch.defaulted());

    // finalize with a recovered amount sized to the real outstanding basis
    uint256 recovered = 11e6;
    deal(address(underlying), manager, recovered, true);
    vm.startPrank(manager);
    underlying.approve(address(strategy), recovered);
    cdoEpoch.finalizeDefault(recovered, manager);
    vm.stopPrank();

    // instantWithdrawClaimsByEpoch[E] still contains A + B, and
    // _defaultPrefundedInstantReserve() booked A as "held" though it was paid out.
    // Victim's defaulted claim is paid from a reserve short by A:
    vm.expectRevert();                          // reserve insolvency -> permanent freeze
    vm.prank(makeAddr('victim'));
    cdoEpoch.claimWithdrawRequest();
}
```

Expected pre-fix behavior: `defaultRecoveryReserve` overstates held funds by `A` (90e6); `defaultRecoveryPrice` is computed against phantom basis; the victim's recovery transfer reverts for insufficient reserve, permanently freezing their claim while the attacker's already-paid `A` is double-counted.