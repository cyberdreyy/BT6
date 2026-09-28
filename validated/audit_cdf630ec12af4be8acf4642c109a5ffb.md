### Title
Missing zero-mint guard in `processDeposits` permanently reverts epoch deposit processing, freezing queued lender deposits - ([File: contracts/IdleCDOEpochQueue.sol](contracts/IdleCDOEpochQueue.sol))

### Summary
The kin-openapi bug is a missing guard on an optional/absent value (`mt.Schema == nil`) on a path that otherwise guards every related condition — a legal input reaches the sink and panics. The in-repo analog is `IdleCDOEpochQueue.processDeposits` (lines 222–246): it guards `_pending == 0`, guards caller permissions, guards prefunded mode — but divides by `_trancheMinted` at line 244 with no zero check. The prefunded sibling `processPrefundedDeposits` (line 270) explicitly documents and handles the exact same case ("the tranche minting results in 0 shares due to rounding"), proving the developers know the value can be zero and that the non-prefunded path is missing the equivalent guard.

### Finding Description [1](#0-0) 

```solidity
uint256 _trancheMinted;
if (tranche == _cdo.AATranche()) {
  _trancheMinted = _cdo.depositAA(_pending);
} else {
  _trancheMinted = _cdo.depositBB(_pending);
}
// save current implied tranche price for this epoch
epochPrice[_epoch] = _pending * ONE_TRANCHE / _trancheMinted;  // <-- div-by-zero, no guard
```

`depositAA`/`depositBB` return `shares = _amount * ONE_TRANCHE / virtualPrice(tranche)`. Since `virtualPrice` monotonically increases with accrued interest (and stays elevated after any loss recovery), once `virtualPrice(tranche) > ONE_TRANCHE`, any `_pending` amount smaller than `virtualPrice / ONE_TRANCHE` units mints exactly `0` shares. `epochPrice[_epoch]` then reverts on division by zero.

Attack path (attacker = KYC-passing lender, buffer/queue phase, non-prefunded variant):
1. Wait until `virtualPrice(AA or BB) > 1e18` (any epoch with positive interest suffices).
2. `requestDeposit(dust)` where `dust < virtualPrice / ONE_TRANCHE`, so `epochPendingDeposits[epoch] = dust`.
3. Honest manager/owner calls `processDeposits()` → `depositAA(dust)` returns 0 → line 244 reverts.
4. If the only queued deposits that epoch are dust-sized (or honest depositors also queued sub-share amounts), `epochPendingDeposits[_epoch]` can never be driven to zero: every retry reverts, so `epochPrice[_epoch]` stays 0 and `claimDepositRequest(_epoch)` reverts at line 375–376 (`epochPrice[_epoch] == 0` → `NotAllowed`) forever.

### Impact Explanation
**Permanent freezing of queued deposits with quantified loss.** Underlyings were already transferred to the queue contract at `requestDeposit` time; there is no deposit-cancel function in the queue (only `deleteWithdrawRequest` exists for withdrawals). All depositors whose requests are in the affected epoch lose access to their funds permanently — both the dust attacker cost is trivial (a few wei of underlying), while honest sub-share depositors in the same epoch lose their full queued principal. A user holding "one receipt" (a deposit request) can never redeem it, breaking the fair-mint invariant.

### Likelihood Explanation
- Requires the non-prefunded epoch-queue variant (prefunded is explicitly protected at line 270).
- Requires `virtualPrice > 1e18`, which is the normal state of any pool that has completed an epoch with positive interest — i.e., most mature pools.
- Requires aggregate `epochPendingDeposits[_epoch]` to remain below one share's underlying value at every `processDeposits` call. Any sufficiently large honest deposit in the same epoch rescues it, so impact is bounded by small-value queues; however, the queue is permissionless to whitelisted lenders and there is no mechanism to purge a poisoned `_pending` value — once an epoch's pending is exclusively dust, no transaction can ever clear it.
- No existing guard stops it: `claimDepositRequest` treats `epochPrice == 0` as "not yet processed", and `processDeposits` never skips or force-prices a zero-mint epoch.

### Recommendation
Mirror the guard already written for the prefunded path:

```solidity
// contracts/IdleCDOEpochQueue.sol, in processDeposits()
epochPrice[_epoch] = _trancheMinted == 0
    ? _cdo.virtualPrice(tranche)
    : _pending * ONE_TRANCHE / _trancheMinted;
epochPendingDeposits[_epoch] = 0;
```

This prices dust deposits at the current virtual price and unblocks the epoch, exactly as `processPrefundedDeposits` does for `_prefundedMinted == 0`. Alternatively, revert cleanly on `requestDeposit` when the implied minted shares would be zero.

### Proof of Concept
Foundry fork PoC (non-prefunded queue, pool that has accrued interest so `virtualPrice(AA) > 1e18`):

```solidity
function testProcessDepositsZeroMintedFreezesEpoch() external {
    // Pool has run at least one epoch with positive APR => virtualPrice > ONE_TRANCHE
    _stopCurrentEpochWithApr(10e18);
    uint256 vp = cdoEpoch.virtualPrice(address(tranche)); // > 1e18
    assertGt(vp, ONE_TRANCHE);

    // KYC'd attacker queues a dust deposit that mints 0 shares
    uint256 dust = vp / ONE_TRANCHE; // underlying worth < 1 share
    deal(address(underlying), attacker, dust, true);
    vm.startPrank(attacker);
    underlying.approve(address(queue), dust);
    queue.requestDeposit(dust); // during running epoch -> buffer queue
    vm.stopPrank();

    uint256 epoch = strategy.epochNumber();
    assertGt(queue.epochPendingDeposits(epoch), 0);

    // stopEpoch -> buffer for next epoch; manager tries to process deposits
    _stopCurrentEpochWithApr(0);

    // Reverts with division-by-zero panic at epochPrice assignment (line 244)
    vm.expectRevert();
    vm.prank(manager);
    queue.processDeposits();

    // Pending deposits are stuck: every retry reverts identically
    vm.expectRevert();
    vm.prank(manager);
    queue.processDeposits();

    // And no claim is possible: epochPrice[epoch] == 0 -> NotAllowed
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    vm.prank(attacker);
    queue.claimDepositRequest(epoch);

    // Attacker's underlyings (and any honest same-epoch deposits) remain
    // locked in the queue contract permanently.
    assertEq(underlying.balanceOf(address(queue)), dust);
}
```

Uncertainty: the exact gate that keeps aggregate pending below one share depends on queue usage; if any epoch accumulates enough pending to mint ≥1 share, the issue self-resolves for that epoch. The permanent-loss case is an epoch whose pending remains exclusively sub-share — which the attacker alone can guarantee whenever they are the only depositor, and which is guaranteed for *every* epoch if the attacker front-runs/creates the sole pending entry and no rescue deposit arrives. There is no recovery path once stuck.

### Citations

**File:** contracts/IdleCDOEpochQueue.sol (L222-246)
```text
  function processDeposits() external {
    // only owner or strategy manager can process deposits
    _checkOnlyOwnerOrManager();
    // prefunded-enabled queues do not use the old buffer-period processing path
    _checkNotAllowed(_isPrefundedQueueEnabled());

    IdleCDOEpochVariant _cdo = IdleCDOEpochVariant(idleCDOEpoch);
    uint256 _epoch = IdleCreditVault(strategy).epochNumber();
    uint256 _pending = epochPendingDeposits[_epoch];

    if (_pending == 0) {
      return;
    }

    // deposit underlyings in the CDO contract, if the epoch is running it will revert
    uint256 _trancheMinted;
    if (tranche == _cdo.AATranche()) {
      _trancheMinted = _cdo.depositAA(_pending);
    } else {
      _trancheMinted = _cdo.depositBB(_pending);
    }
    // save current implied tranche price for this epoch based on underlyings deposited and tranche tokens minted
    epochPrice[_epoch] = _pending * ONE_TRANCHE / _trancheMinted;
    epochPendingDeposits[_epoch] = 0;
  }
```
