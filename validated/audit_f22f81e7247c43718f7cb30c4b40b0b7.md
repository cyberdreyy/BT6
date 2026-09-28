### Title
Stale per-epoch instant-withdraw claim totals inflate `defaultPendingClaimBasis`, diluting default recovery and permanently locking reserve funds - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
When a user claims a funded instant-withdraw receipt during a running epoch, `claimInstantWithdrawRequest` clears `instantWithdrawsRequests[user]` but never decrements `instantWithdrawsRequestsByEpoch[user][epoch]` or the aggregate `instantWithdrawClaimsByEpoch[epoch]`. If the borrower defaults in that same epoch and `finalizeDefaultRecovery` runs, `defaultPendingClaimBasis()` still counts the already-claimed instant receipts as defaulted claim basis. The recovery price is therefore computed against an inflated basis, every legitimate recovery claimant is underpaid, and the excess recovery reserve corresponding to the phantom basis can never be distributed and is permanently locked in the strategy.

### Finding Description
`requestInstantWithdraw` records both per-user-per-epoch and aggregate per-epoch basis: [1](#0-0) 

`claimInstantWithdrawRequest` pays the user at par from already-collected strategy underlyings, but only clears the flat aggregate — neither `instantWithdrawsRequestsByEpoch[user][epoch]` nor `instantWithdrawClaimsByEpoch[epoch]` is decremented on the normal (pre-default) claim path. They are only decremented inside `_claimDefaultedInstantWithdrawRequest`, i.e. only for claims that occur after default finalization. [2](#0-1) 

At finalization, `defaultPendingClaimBasis()` adds `instantWithdrawClaimsByEpoch[epochNumber]` whenever `pendingInstantWithdraws != 0` (i.e. the instant queue was only partially funded by `getInstantWithdrawFunds`): [3](#0-2) 

That aggregate still includes receipts that were already claimed at par during the epoch, so `totalBasis` in `finalizeDefaultRecovery` is overstated by the claimed amount `C`, and `recoveryPrice = reserveAmount * 1e18 / totalBasis` is too low: [4](#0-3) 

Every defaulted normal/instant receipt is paid `claimBasis * defaultRecoveryPrice / 1e18`, so the `C` phantom basis is never claimed (the attacker already withdrew at par and a second claim reverts on the `instantWithdrawsRequests[user] -= claimBasis` underflow at line 848). The reserve slice proportional to `C` remains in `defaultRecoveryReserve` forever — there is no sweep or redistribution path for it.

### Impact Explanation
Theft/permanent freezing of recovery funds. If an attacker claimed `C` of instant receipts before the default in the same epoch, then `defaultRecoveryPrice` is multiplied by roughly `trueBasis / (trueBasis + C)`. All other defaulted receipt holders and post-default recovery claimants lose that fraction of their recovery, and a reserve amount of approximately `C * recoveryPrice` is permanently locked as unclaimable dust in `IdleCreditVault` (no function releases recovery-reserve excess). The attacker profit is the ability to have extracted at par pre-default while also degrading everyone else's recovery; the protocol-level loss equals the locked reserve.

### Likelihood Explanation
Requires only unprivileged actions sequenced around honest privileged calls: (1) deposit, (2) request instant withdraw in the buffer, (3) wait for the honest manager to partially fund the instant queue via `getInstantWithdrawFunds` and claim the funded portion, (4) borrower fails to fully return funds at `stopEpoch` (default), (5) honest manager calls `finalizeDefault`. Instant withdrawals are a normal user flow and partial prefunding of the instant queue is an explicitly supported mode (`pendingInstantWithdraws != 0`, `defaultInstantWithdrawsFinalized`). The attacker's only cost is the instant-withdraw delay and the haircut-free par claim itself is legitimate.

### Recommendation
In `claimInstantWithdrawRequest` (or a shared helper), clear the per-epoch accounting when paying a claim: `instantWithdrawsRequestsByEpoch[_user][currentRequestEpoch] = 0` and `instantWithdrawClaimsByEpoch[epoch] -= amount`, mirroring what `_claimDefaultedInstantWithdrawRequest` does at lines 847–853. This requires tracking which epoch a user's outstanding instant receipt belongs to (analogous to `lastWithdrawRequest` for normal receipts) so that claims spanning multiple epochs decrement the correct buckets. Alternatively, compute the default instant basis from the still-outstanding per-user balances rather than a cumulative aggregate that never shrinks on normal claims.

### Proof of Concept
Foundry-style fork/unit PoC sketch against `test/foundry/IdleCreditVault.t.sol` helpers:

```solidity
function testStaleInstantBasisInflatesDefaultRecovery() external {
    IdleCreditVault creditVault = IdleCreditVault(address(strategy));
    uint256 instantDelay = cdoEpoch.instantWithdrawDelay();
    vm.prank(manager);
    cdoEpoch.setInstantWithdrawParams(instantDelay, 1000, false);

    address attacker = makeAddr('attacker');
    address victim = makeAddr('victim');
    uint256 amount = 10_000 * ONE_SCALE;
    _depositWithUser(attacker, amount, true);
    _depositWithUser(victim, amount, true);
    idleCDO.depositAA(amount); // active LP keeping pendingInstantWithdraws > 0

    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr / 2, _expectedFundsEndEpoch());

    // buffer: both request instant withdraws (epoch 1 basis = 2 * claim)
    vm.prank(attacker);
    uint256 attackerClaim = cdoEpoch.requestWithdraw(0, address(AAtranche));
    vm.prank(victim);
    cdoEpoch.requestWithdraw(0, address(AAtranche));

    _startEpochAndCheckPrices(1);
    // partial funding so pendingInstantWithdraws != 0 but strategy holds some instant cash
    vm.warp(block.timestamp + instantDelay + 1);
    vm.prank(manager);
    cdoEpoch.getInstantWithdrawFunds();

    // attacker claims funded portion at par during the running epoch
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest();
    // stale: instantWithdrawsRequestsByEpoch[attacker][1] and
    // instantWithdrawClaimsByEpoch[1] still include attackerClaim

    // borrower defaults in the same epoch
    _checkDefault(); // or stopEpoch with insufficient borrower funds per suite helpers

    uint256 inflatedBasis = creditVault.defaultPendingClaimBasis();
    // inflatedBasis includes attacker's already-claimed attackerClaim
    uint256 fairBasis = inflatedBasis - attackerClaim;

    uint256 recovered = /* choose so recoveryRatio < 1 */;
    deal(defaultUnderlying, manager, recovered);
    vm.startPrank(manager);
    underlying.approve(address(strategy), recovered);
    cdoEpoch.finalizeDefault(recovered, manager);
    vm.stopPrank();

    // victim is paid at diluted price
    uint256 balPre = underlying.balanceOf(victim);
    vm.prank(victim);
    cdoEpoch.claimInstantWithdrawRequest();
    uint256 victimPaid = underlying.balanceOf(victim) - balPre;

    uint256 fairPrice = creditVault.defaultRecoveryReserve()
        * ONE_TRANCHE / fairBasis; // price if stale basis were excluded
    assertLt(victimPaid, /* victimBasis * fairPrice / 1e18 */);

    // residual reserve proportional to stale basis can never be claimed
    assertGt(creditVault.defaultRecoveryReserve(), 0, 'dust locked forever');
}
```

Broken invariant: one receipt one payout + proportional default distribution — a receipt that was already fully paid is still counted as outstanding claim basis, so the recovery multiplier is mispriced and a slice of `defaultRecoveryReserve` is permanently stranded.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L366-374)
```text
    instantWithdrawsRequests[_user] += _amount;
    uint256 currentEpoch = epochNumber;
    // we record both per-user (old, kept for compatibility) and per-epoch so on
    // finalization we can distinguish "default-epoch pending instant receipts"
    // from old funded instant receipts.
    instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
    instantWithdrawClaimsByEpoch[currentEpoch] += _amount;
    // increase the total instant withdraw requests
    pendingInstantWithdraws += _amount;
```

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L644-649)
```text
  function defaultPendingClaimBasis() public view returns (uint256 basis) {
    basis = pendingWithdraws;
    if (pendingInstantWithdraws != 0) {
      basis += instantWithdrawClaimsByEpoch[epochNumber];
    }
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L679-696)
```text
    uint256 pendingBasis = defaultPendingClaimBasis();
    uint256 totalBasis = activeBasis + pendingBasis;
    if (totalBasis == 0) revert NotAllowed();

    // Some recovery funds may already be in this strategy: partially prefunded instant requests
    // and borrower-send funds that failed at epoch start. Count both without pulling them again.
    uint256 prefundedReserve = _defaultPrefundedInstantReserve();
    uint256 reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve;
    // Recovery can be above par if the recovered funds exceed the computed basis.
    uint256 recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;

    defaultRecoveryFinalized = true;
    defaultRecoveryReserve = reserveAmount;
    defaultRecoveryPrice = recoveryPrice;
    defaultRecoveryEpoch = epochNumber;
    // A non-zero pending instant bucket means current-epoch instant receipts were not fully funded
    // and must be paid through the same recovery ratio as normal pending receipts.
    defaultInstantWithdrawsFinalized = pendingInstantWithdraws != 0;
```
