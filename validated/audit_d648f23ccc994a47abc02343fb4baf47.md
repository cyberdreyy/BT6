### Title
Claimed instant-withdraw receipts are never cleared from per-epoch accounting, inflating default-recovery basis and enabling double payout or frozen claims - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
Analog of the resource-leak bug class (allocated state never released per operation). `claimInstantWithdrawRequest` releases the aggregate receipt (`instantWithdrawsRequests[_user]`) but never releases the per-epoch allocations `instantWithdrawsRequestsByEpoch[_user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]`. This stale basis leaks into the default-recovery accounting path, corrupting the recovery price and letting a user be paid twice (or permanently reverting other users' claims via reserve insolvency).

### Finding Description
`requestInstantWithdraw` records three pieces of state per request:

- `instantWithdrawsRequests[_user] += _amount` (line 366)
- `instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount` (line 371)
- `instantWithdrawClaimsByEpoch[currentEpoch] += _amount` (line 372)

The funded-claim path `claimInstantWithdrawRequest` (lines 380–393) burns the receipt and zeroes only `instantWithdrawsRequests[_user]`; the two per-epoch mappings are never decremented or cleared anywhere in the contract. The only function that touches them is `_claimDefaultedInstantWithdrawRequest` (lines 842–856), which uses them as the payout basis:

```solidity
claimBasis = instantWithdrawsRequestsByEpoch[_user][defaultEpoch];
...
pendingInstantWithdraws = claimBasis >= pending ? 0 : pending - claimBasis;
instantWithdrawClaimsByEpoch[defaultEpoch] -= claimBasis;
_burn(_user, claimBasis);
_transferDefaultRecovery(_user, (claimBasis * defaultRecoveryPrice) / RECOVERY_FULL);
```

These stale entries feed the default-finalization math:

- `defaultPendingClaimBasis` (lines 644–649) adds `instantWithdrawClaimsByEpoch[epochNumber]` whenever `pendingInstantWithdraws != 0`.
- `_defaultPrefundedInstantReserve` (lines 716–723) computes `instantBasis - pendingInstant` and treats the difference as underlying already held by the strategy, folding it into `defaultRecoveryReserve`/`defaultRecoveryPrice` (lines 685–692).

Scenario: in epoch E the CDO collects instant-withdraw funds via `collectInstantWithdrawFunds` (line 398, which decrements `pendingInstantWithdraws` but not the per-epoch claim mappings). A user then calls `claimInstantWithdrawRequest` and is paid, but `instantWithdrawsRequestsByEpoch[user][E]` and `instantWithdrawClaimsByEpoch[E]` retain the full amount. Still within epoch E (or a later epoch where the user re-requested, since `requestInstantWithdraw` re-adds to the same per-epoch slot only for the *current* epoch — the stale E entries persist regardless), the borrower defaults. If `pendingInstantWithdraws` is non-zero at `finalizeDefaultRecovery` (partial funding, which the code explicitly supports), the already-claimed, already-paid amount is counted again: as claim basis (inflating `totalBasis` and diluting `defaultRecoveryPrice` for honest claimants), and as phantom prefunded reserve (inflating `reserveAmount` without backing tokens). After finalization, `_claimDefaultedInstantWithdrawRequest(user)` reads the stale per-epoch basis and attempts `_burn(_user, claimBasis)` + `_transferDefaultRecovery(_user, claimBasis * price)`:

- If the user holds new receipt tokens (from a post-claim re-request), they receive a second payout for tokens already redeemed — direct theft from `defaultRecoveryReserve`, insolventing later claimants.
- If they hold no receipt tokens, the `_burn`/transfer underflows or the reserve drains, leaving `_claimDefaultedInstantWithdrawRequest` for honest users to revert or be paid zero — permanent freezing/theft of recovery funds.

Existing guards do not stop this: the `defaultRecoveryFinalized && defaultInstantWithdrawsFinalized` gate (line 382) actually routes users into the buggy path; `requestWithdraw`'s post-default `NotAllowed` check (line 249) inspects `instantWithdrawsRequests[_user]`, which was correctly cleared — the vulnerability lives in the per-epoch mappings that were leaked, exactly matching the leak bug class.

### Impact Explanation
Direct theft of `defaultRecoveryReserve` (double payout on an already-claimed instant receipt) and/or permanent freezing of honest users' defaulted-instant recovery claims once the reserve is drained or the burn underflows. Loss is bounded by total instant withdrawals made in the defaulted epoch; with the common pattern of partial instant funding at default time it can consume the entire instant-recovery allocation and dilute `defaultRecoveryPrice` for all claimants including normal withdraw receipts and active tranche holders.

### Likelihood Explanation
Requires: (a) instant withdrawals enabled and funded mid-epoch via `getInstantWithdrawFunds`/`collectInstantWithdrawFunds`, (b) a user claiming (so the aggregate is cleared but per-epoch entries are not), and (c) a borrower default finalized while `pendingInstantWithdraws != 0`. All steps are normal protocol flows with no privileged misbehavior — instant withdrawals are a first-class feature, claiming is permissionless, and defaults are an anticipated state (dedicated finalization code exists). The triggering user is an unprivileged tranche holder.

### Recommendation
In `claimInstantWithdrawRequest`, delete the user's per-epoch receipt entries and decrement `instantWithdrawClaimsByEpoch` for every epoch contributing to `instantWithdrawsRequests[_user]` (e.g., iterate/track epochs or store the request epoch alongside the amount, mirroring `lastWithdrawRequest`). Alternatively store a single `instantWithdrawRequestEpoch[_user]` marker and clear both mappings in one step. Add a regression test that funds an instant request, claims it, defaults the same epoch with remaining `pendingInstantWithdraws`, and asserts the user cannot claim again and `instantWithdrawClaimsByEpoch` reflects only unclaimed basis.

### Proof of Concept
Foundry fork sketch (building on `test/foundry/IdleCDOEpochQueue.t.sol` helpers `_depositWithUser`, `_requestWithdrawWithUser`, `_stopCurrentEpochWithApr`):

```solidity
function testInstantReceiptLeakDoubleClaim() external {
    _useStandardEpochVariant();
    _stopCurrentEpochWithApr(10e18);                    // epoch 1
    vm.prank(manager);
    cdoEpoch.setInstantWithdrawParams(100, 1e18, false);

    address alice = makeAddr('alice');
    uint256 tranchesA = _depositWithUser(alice, 100e6);
    _depositWithUser(makeAddr('bob'), 100e6);

    vm.prank(manager);
    cdoEpoch.startEpoch();                              // epoch running
    _requestInstantWithdrawWithUser(alice, tranchesA);  // epoch E entry written

    // CDO partially funds instant queue; collect via getInstantWithdrawFunds path
    // such that pendingInstantWithdraws remains > 0 for another user.
    _fundInstantPartial();                              // collectInstantWithdrawFunds(amount < total)

    // Alice claims funded portion: aggregate cleared, per-epoch basis leaked
    vm.prank(address(cdoEpoch));
    strategy.claimInstantWithdrawRequest(alice);
    assertEq(strategy.instantWithdrawsRequests(alice), 0);
    uint256 epoch = strategy.epochNumber();
    assertGt(strategy.instantWithdrawsRequestsByEpoch(alice, epoch), 0); // leaked

    // Alice re-requests instant withdraw in same epoch -> has fresh receipt tokens
    _requestInstantWithdrawWithUser(alice, 1e6);

    // Borrower defaults; finalize recovery with pendingInstantWithdraws != 0
    _defaultAndFinalize();

    // Alice's stale per-epoch basis pays her again (double payout), draining reserve
    uint256 reservePre = strategy.defaultRecoveryReserve();
    vm.prank(address(cdoEpoch));
    strategy.claimInstantWithdrawRequest(alice); // pays claimBasis(old + new) * price
    assertLt(strategy.defaultRecoveryReserve(), reservePre - strategy.instantWithdrawsRequestsByEpoch(alice, epoch));
}
```

Key assertions demonstrating the broken invariant: `instantWithdrawsRequestsByEpoch(alice, epoch)` remains non-zero after a full claim (leaked allocation), and `instantWithdrawClaimsByEpoch[epoch]` is counted in `defaultPendingClaimBasis` despite having been paid out, producing both an inflated recovery price and a second payout from `defaultRecoveryReserve`.