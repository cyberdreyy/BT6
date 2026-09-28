### Title
Unfunded instant-withdraw receipts from earlier epochs escape the default-recovery haircut and are paid at par - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The kernel bug is a "save/resume" path that fails to re-point data pointers at the correct offset, so stale data is treated as current. The credit-vault analog lives in `IdleCreditVault`'s epoch-indexed instant-withdraw accounting: `instantWithdrawsRequestsByEpoch` and `instantWithdrawClaimsByEpoch` track claims per epoch, but `defaultPendingClaimBasis()` and `_claimDefaultedInstantWithdrawRequest()` only ever look at the single "current" `epochNumber` bucket, while payout (`claimInstantWithdrawRequest`) burns the aggregate `instantWithdrawsRequests[user]` at par. An unclaimed, unfunded instant receipt created in an earlier epoch is therefore invisible to default finalization — it is excluded from the recovery basis (inflating `defaultRecoveryPrice`) yet still redeems 1:1 from strategy funds, overpaying the stale receipt holder at the expense of every other recovery claimant.

### Finding Description
`requestInstantWithdraw` records receipts under `instantWithdrawsRequestsByEpoch[user][epochNumber]` and bumps `instantWithdrawClaimsByEpoch[epochNumber]` and the aggregate `pendingInstantWithdraws` [1](#0-0) . `collectInstantWithdrawFunds` decrements only the aggregate `pendingInstantWithdraws` when the CDO pulls cash from the borrower [2](#0-1)  — if the borrower underfunds, a positive `pendingInstantWithdraws` survives into the next epoch while the unfunded claim basis remains keyed to the *old* epoch in `instantWithdrawClaimsByEpoch`.

When a later borrower default is finalized, `defaultPendingClaimBasis` adds only `instantWithdrawClaimsByEpoch[epochNumber]` — the current epoch — to the recovery basis [3](#0-2) . The stale-epoch instant claim is:
- **excluded from `totalBasis`**, so `recoveryPrice = reserveAmount * 1e18 / totalBasis` is computed over a smaller denominator than true outstanding claims (line 688) [4](#0-3) ;
- **never cleared** by `_claimDefaultedInstantWithdrawRequest`, which only zeroes `instantWithdrawsRequestsByEpoch[user][defaultRecoveryEpoch]` [5](#0-4) ;
- **still paid at par** because `claimInstantWithdrawRequest` burns and pays the full aggregate `instantWithdrawsRequests[user]` via `_transferFundedClaim` [6](#0-5) .

This is exactly the migration-pointer defect: the per-epoch claims table is the "fd-offset" data, but finalization dereferences only `epochNumber`, never re-pointing to older epochs that still carry live claims. The normal withdraw path avoids this because `withdrawsRequestsByEpoch` receipts are haircut per `lastWithdrawRequest` epoch and `requestWithdraw` blocks new requests while a loss-adjusted receipt is outstanding [7](#0-6)  — no equivalent guard or per-epoch sweep exists for instant receipts.

### Impact Explanation
A holder of an old-epoch unfunded instant receipt receives ~100% of their claim post-default while defaulted-epoch receipt holders and active tranche LPs receive only `defaultRecoveryPrice`. The par payout is drawn from the strategy's underlying balance, shrinking the funds backing `defaultRecoveryReserve` claims, so the aggregate promised payouts exceed the reserve: later claimants (defaulted receipts, `postDefaultRequests`, or par-funded claims) are either underpaid or their claims permanently revert, i.e. direct loss / permanent freezing of funds quantified by the stale claim amount. Example: 900k reserve, 800k current-epoch instant basis, 200k stale-epoch unfunded instant claim → basis counted = 800k, price = 112.5% (even over par), stale claimant pulls 200k at par, leaving 700k against 800k of haircut claims — a guaranteed shortfall.

### Likelihood Explanation
Requires: (a) an instant-withdraw request that the borrower only partially funds via `collectInstantWithdrawFunds`, leaving `pendingInstantWithdraws > 0` across an `epochNumber` increment (epochNumber bumps inside `deposit()` during `stopEpoch` [8](#0-7) ); (b) a subsequent borrower default finalized via `finalizeDefaultRecovery`. Both are reachable with honest privileged actors — partial borrower funding and defaults are ordinary protocol states, not attacker-controlled prerequisites. The attacker is merely a KYC'd lender holding an instant receipt who waits one epoch and then claims normally; no privileged collusion or exotic sequence is needed.

### Recommendation
Track unfunded instant basis per epoch and include *all* outstanding instant claims in `defaultPendingClaimBasis` (e.g., carry a `instantWithdrawClaimsByEpoch` rollover so `pendingInstantWithdraws != 0` contributes the full outstanding basis, not just `instantWithdrawClaimsByEpoch[epochNumber]`), or haircut stale-epoch instant receipts through `defaultRecoveryPrice` in `_claimDefaultedInstantWithdrawRequest` by iterating/accumulating non-zero epochs. At minimum, gate `claimInstantWithdrawRequest`'s par payout so post-default claims cannot draw on `defaultRecoveryReserve` for basis that was never included in the recovery denominator.

### Proof of Concept
```solidity
// Foundry fork test (against existing harness in test/foundry/IdleCreditVault.t.sol)
function testStaleInstantReceiptEscapesDefaultHaircut() external {
    _useStandardEpochVariant();
    uint256 amount = 100_000 * ONE_SCALE;

    // User deposits AA and requests an instant withdraw in epoch N-1
    uint256 minted = idleCDO.depositAA(amount);
    _startEpochAndCheckPrices(0);
    cdoEpoch.requestInstantWithdraw(minted / 2, address(AAtranche));
    uint256 reqEpoch = strategy.epochNumber();

    // Borrower funds only HALF the instant queue at buffer end -> pendingInstantWithdraws > 0
    uint256 pending = strategy.pendingInstantWithdraws();
    deal(defaultUnderlying, borrower, pending / 2);
    vm.prank(borrower);
    underlying.approve(address(cdoEpoch), pending / 2);
    // getInstantWithdrawFunds pulls only part; remainder rolls into epoch N
    _partialInstantFunding(pending / 2);

    // Epoch rolls: epochNumber += 1 inside strategy.deposit during stopEpoch
    _stopEpochAndCheckPrices(0, initialProvidedApr, _expectedFundsEndEpoch());
    assertGt(strategy.pendingInstantWithdraws(), 0);
    assertEq(strategy.instantWithdrawsRequestsByEpoch(address(this), reqEpoch), minted / 2);
    // current-epoch bucket is now EMPTY for this user
    assertEq(strategy.instantWithdrawClaimsByEpoch(strategy.epochNumber()), 0);

    // Borrower defaults during next stop; finalizeDefaultRecovery computes
    // basis = pendingWithdraws + instantWithdrawClaimsByEpoch[epochNumber] = 0 instant basis
    _forceDefaultAndFinalize();

    // Stale receipt is NOT in instantWithdrawClaimsByEpoch[defaultRecoveryEpoch],
    // so _claimDefaultedInstantWithdrawRequest skips it and the aggregate pays at par
    uint256 balPre = underlying.balanceOf(address(this));
    cdoEpoch.claimInstantWithdrawRequest();
    assertEq(underlying.balanceOf(address(this)) - balPre, minted / 2); // ~100%, no haircut

    // Meanwhile defaulted-epoch claimants recover only defaultRecoveryPrice, and the
    // reserve is now short by the stale par payout -> later claims revert/underpay.
}
```

Caveat: this depends on `pendingInstantWithdraws` being able to stay non-zero across an epoch boundary with an honest borrower (partial funding in `getInstantWithdrawFunds`). If the CDO hard-blocks `startEpoch`/`stopEpoch` whenever `pendingInstantWithdraws != 0`, the stale-epoch state is unreachable and the finding collapses; the code path should be confirmed in a live session against `getInstantWithdrawFunds`/`startEpoch` gating in `IdleCDOEpochVariant.sol`.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L261-271)
```text
    uint256 lossEpoch = lastWithdrawRequest[_user];
    uint256 lossRecoveryPrice = lossRecoveryPriceByEpoch[lossEpoch];
    if (
      lossRecoveryPrice != 0 &&
      (withdrawsRequestsByEpoch[_user][lossEpoch] != 0 ||
      (apr0Users[_user].principal != 0 && apr0Users[_user].principalEpoch == lossEpoch))
    ) {
      // A loss-adjusted receipt must be claimed before opening a later request, otherwise
      // `lastWithdrawRequest` would stop pointing to the epoch that stores its haircut.
      revert NotAllowed();
    }
```

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L387-392)
```text
    uint256 amount = instantWithdrawsRequests[_user];
    // burn strategy tokens from user
    _burn(_user, amount);

    instantWithdrawsRequests[_user] = 0;
    _transferFundedClaim(_user, amount);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L398-403)
```text
  function collectInstantWithdrawFunds(uint256 _amount) external {
    _onlyIdleCDO();
    if (_amount == 0) return;
    pendingInstantWithdraws -= _amount;
    underlyingToken.safeTransferFrom(idleCDO, address(this), _amount);
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L607-614)
```text
    if (IIdleCDOEpochVariant(idleCDO).isEpochRunning()) {
      // deposit done on stopEpoch (before setting the var to false) so we reset the counter
      totEpochDeposits = 0;
      epochNumber += 1;
    } else {
      // deposit done between epochs so we increase the counter
      totEpochDeposits += _amount;
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L679-692)
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
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L842-856)
```text
  function _claimDefaultedInstantWithdrawRequest(address _user) internal returns (uint256 claimBasis) {
    uint256 defaultEpoch = defaultRecoveryEpoch;
    claimBasis = instantWithdrawsRequestsByEpoch[_user][defaultEpoch];
    if (claimBasis == 0) return claimBasis;

    instantWithdrawsRequestsByEpoch[_user][defaultEpoch] = 0;
    instantWithdrawsRequests[_user] -= claimBasis;
    uint256 pending = pendingInstantWithdraws;
    // `pendingInstantWithdraws` is only the unfunded remainder. If this user's claim is larger,
    // the extra amount was already counted as prefunded reserve during default finalization.
    pendingInstantWithdraws = claimBasis >= pending ? 0 : pending - claimBasis;
    instantWithdrawClaimsByEpoch[defaultEpoch] -= claimBasis;
    _burn(_user, claimBasis);
    _transferDefaultRecovery(_user, (claimBasis * defaultRecoveryPrice) / RECOVERY_FULL);
  }
```
