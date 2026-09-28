### Title
Stale per-epoch instant-withdraw accounting inflates default recovery reserve and freezes last claimants - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`claimInstantWithdrawRequest` clears only the aggregate `instantWithdrawsRequests[_user]` and never removes the per-epoch entries `instantWithdrawsRequestsByEpoch[_user][epoch]` / `instantWithdrawClaimsByEpoch[epoch]` — a "leaked" receipt, exactly analogous to the kernel bug where an allocated `mode` was never freed. If the same epoch later defaults with other instant receipts still unfunded, `defaultPendingClaimBasis` / `_defaultPrefundedInstantReserve` treat the already-paid-out claim as still-held collateral, inflating `defaultRecoveryPrice` and `defaultRecoveryReserve` beyond the real token balance. First-come claimants drain the reserve; the last claimants' `safeTransfer` reverts permanently.

### Finding Description
In `requestInstantWithdraw`, the strategy records three ledgers: `instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch[_user][currentEpoch]`, and `instantWithdrawClaimsByEpoch[currentEpoch]` [1](#0-0) . On a normal claim, only the aggregate is zeroed — both per-epoch entries leak [2](#0-1) .

Later, at default finalization, `defaultPendingClaimBasis()` adds `instantWithdrawClaimsByEpoch[epochNumber]` whenever `pendingInstantWithdraws != 0` [3](#0-2) , and `_defaultPrefundedInstantReserve` counts `instantBasis - pendingInstant` as already-held underlying [4](#0-3) . Both include the stale, already-claimed basis. `finalizeDefaultRecovery` then sets `defaultRecoveryReserve = _recoveredAmount + prefundedReserve + ...` and prices recovery against the inflated basis [5](#0-4) . Every default claim decrements the phantom reserve via `_transferDefaultRecovery` [6](#0-5) , but the underlying tokens backing the leaked portion were already paid out, so the balance is short and later claims revert in `safeTransfer`.

### Impact Explanation
Permanent freezing of unclaimed yield/principal: once default finalizes in an epoch where some instant receipts were claimed normally while others remained unfunded, `defaultRecoveryPrice` is computed on a reserve that does not exist. Early claimants receive full `claimBasis * defaultRecoveryPrice` payouts; the reserve and balance are exhausted early, so the last defaulted-epoch claimants (and post-default claimants in `postDefaultRequests`) can never claim — claims revert on the ERC20 transfer. Broken invariant: the default recovery reserve must equal the underlying actually held; one receipt one payout is violated.

### Likelihood Explanation
Requires an unprivileged user to request an instant withdraw during the buffer, the manager to fund it via `getInstantWithdrawFunds`/`collectInstantWithdrawFunds`, the user to claim, a second user to leave an unfunded instant request in the same epoch, and a borrower default with `finalizeDefaultRecovery` while `pendingInstantWithdraws != 0`. All steps are ordinary flows (the mixed funded/unfunded instant path is explicitly supported by `defaultInstantWithdrawsFinalized`), and the attacker need only be a normal lender timing a routine claim; the default and partial funding arise in normal operation. No privileged misbehavior needed.

### Recommendation
In `claimInstantWithdrawRequest`, clear the per-epoch ledgers for the funded epoch: zero `instantWithdrawsRequestsByEpoch[_user][epoch]` and decrement `instantWithdrawClaimsByEpoch[epoch]` by the claimed basis (or track and subtract per-epoch entries for every claim). Alternatively, exclude already-claimed basis in `_defaultPrefundedInstantReserve`/`defaultPendingClaimBasis` by tracking claimed-vs-pending per epoch.

### Proof of Concept
Foundry fork PoC sketch (extend `test/foundry/IdleCreditVault.t.sol` helpers):

```solidity
function testClaimedInstantReceiptInflatesDefaultRecovery() external {
    _useStandardEpochVariant();
    _stopCurrentEpochWithApr(10e18); // enter epoch 1 buffer

    // enable instant withdrawals
    vm.prank(manager);
    cdoEpoch.setInstantWithdrawParams(100, 1e18, false);

    address alice = makeAddr('alice');
    address bob = makeAddr('bob');
    uint256 amt = 100e6;
    _depositWithUser(alice, amt);
    _depositWithUser(bob, amt);

    vm.prank(manager);
    cdoEpoch.startEpoch();

    // both request instant withdraw in the same epoch
    _requestInstantWithdraw(alice, amt); // via cdoEpoch.requestInstantWithdraw
    _requestInstantWithdraw(bob, amt);

    // manager funds ONLY alice's request (partial funding)
    vm.warp(block.timestamp + cdoEpoch.instantWithdrawDelay() + 1);
    deal(address(underlying), address(cdoEpoch), amt, true);
    vm.prank(manager);
    cdoEpoch.getInstantWithdrawFunds(); // collectInstantWithdrawFunds(amt)

    // alice claims normally: aggregate cleared, per-epoch entries LEAK
    vm.prank(alice);
    cdoEpoch.claimInstantWithdrawRequest();
    IdleCreditVault v = IdleCreditVault(address(strategy));
    assertEq(v.instantWithdrawsRequestsByEpoch(alice, v.epochNumber()), amt); // stale
    assertEq(v.instantWithdrawClaimsByEpoch(v.epochNumber()), 2 * amt);       // stale

    // borrower defaults with bob's instant request still unfunded
    _checkDefault();
    uint256 recovered = /* recovered amount per finalizeDefault(recovered, manager) */;
    deal(defaultUnderlying, manager, recovered);
    vm.startPrank(manager);
    IERC20Detailed(defaultUnderlying).approve(address(strategy), recovered);
    cdoEpoch.finalizeDefault(recovered, manager);
    vm.stopPrank();

    // prefundedReserve counted amt that was already paid to alice:
    // defaultRecoveryReserve > strategy's actual underlying balance.
    assertGt(v.defaultRecoveryReserve(), underlying.balanceOf(address(v)));

    // bob (or other claimants) claims: earlier default claims drain the phantom
    // reserve; the final claim reverts on safeTransfer -> permanent freeze.
    vm.prank(bob);
    vm.expectRevert(); // ERC20 transfer exceeds balance
    cdoEpoch.claimInstantWithdrawRequest();
}
```

Expected outcome: `defaultRecoveryReserve` exceeds the strategy token balance by the leaked amount, and the last defaulted-epoch claimant's transaction reverts, demonstrating permanent freezing of funds.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L365-374)
```text
    // increase the instant withdraw requests for the user
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L644-648)
```text
  function defaultPendingClaimBasis() public view returns (uint256 basis) {
    basis = pendingWithdraws;
    if (pendingInstantWithdraws != 0) {
      basis += instantWithdrawClaimsByEpoch[epochNumber];
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L685-696)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L716-722)
```text
  function _defaultPrefundedInstantReserve() internal view returns (uint256 prefundedReserve) {
    uint256 pendingInstant = pendingInstantWithdraws;
    if (pendingInstant == 0) return prefundedReserve;
    uint256 instantBasis = instantWithdrawClaimsByEpoch[epochNumber];
    if (instantBasis > pendingInstant) {
      prefundedReserve = instantBasis - pendingInstant;
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L912-916)
```text
  function _transferDefaultRecovery(address _user, uint256 _amount) internal {
    if (_amount == 0) return;
    // Every defaulted or post-default claim consumes the isolated recovery reserve.
    defaultRecoveryReserve -= _amount;
    underlyingToken.safeTransfer(_user, _amount);
```
