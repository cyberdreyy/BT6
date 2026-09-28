### Title
Already-claimed instant withdrawals can re-enter default recovery accounting - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
`claimInstantWithdrawRequest` pays a funded instant-withdraw receipt but never clears `instantWithdrawsRequestsByEpoch` or decrements `instantWithdrawClaimsByEpoch`. If the borrower later defaults while another instant receipt from the same epoch remains unfunded, `defaultPendingClaimBasis` treats the entire epoch’s claim amount—including already-paid receipts—as pending. This allows a claimant who already received funds to claim a second haircut payout from the default-recovery reserve.

### Finding Description
`requestInstantWithdraw` records each receipt in both the aggregate `instantWithdrawsRequests[_user]` bucket and the per-epoch mappings `instantWithdrawsRequestsByEpoch[_user][currentEpoch]` and `instantWithdrawClaimsByEpoch[currentEpoch]` [1](#0-0) .

For a funded instant withdrawal, `claimInstantWithdrawRequest` clears only `instantWithdrawsRequests[_user]`; it does not clear `instantWithdrawsRequestsByEpoch[_user][currentEpoch]` or remove the receipt from `instantWithdrawClaimsByEpoch[currentEpoch]` [2](#0-1) .

During default finalization, whenever `pendingInstantWithdraws != 0`, `defaultPendingClaimBasis` adds the entire `instantWithdrawClaimsByEpoch[epochNumber]` to the defaulted claim basis [3](#0-2) . That bucket still contains claims already paid at par.

After finalization, `claimInstantWithdrawRequest` invokes `_claimDefaultedInstantWithdrawRequest`, which reads the stale per-user epoch entry, clears it, burns `claimBasis` receipt tokens, and pays a second amount from `defaultRecoveryReserve` [4](#0-3) [5](#0-4) . The first claim did not consume the stale mapping, so the same receipt can be settled once at par and once at the recovery price.

### Impact Explanation
An unprivileged tranche-token holder can receive a funded instant withdrawal and then receive a second payment after default finalization. The protocol also overstates `defaultPendingClaimBasis`, which depresses `defaultRecoveryPrice`; however, the stale per-user claim still pays `claimBasis * defaultRecoveryPrice`, directly draining recovery assets intended for still-pending claimants [6](#0-5) .

For example, if two users request 100 each, the strategy prefunds 100, the first user claims 100, and recovery provides 100 more, the honest remaining claimant should receive all 100. Because the epoch claim basis remains 200 instead of 100, the recovery price becomes 0.5e18 and the already-paid user can steal approximately 50 additional tokens, leaving only 50 for the unpaid claimant [7](#0-6) [8](#0-7) .

### Likelihood Explanation
The sequence requires a partially funded instant-withdraw queue in one epoch followed by a borrower default and recovery finalization. Partial funding can occur naturally because `startEpoch` collects only the available CDO balance and leaves the remainder in `pendingInstantWithdraws` [9](#0-8) . A user in the funded subset can claim before default, while an unpaid claimant remains exposed. No privileged role needs to behave maliciously; the borrower can honestly fail repayment, and recovery source approval is part of normal default handling.

### Recommendation
When an instant receipt is paid through the funded path, clear all accounting associated with that receipt’s request epoch:

- Clear `instantWithdrawsRequestsByEpoch[_user][requestEpoch]` for each funded receipt.
- Decrement `instantWithdrawClaimsByEpoch[requestEpoch]` by the paid amount.
- Store the request epoch or otherwise derive it safely when claiming funded receipts.
- Ensure each funded instant receipt is removed from default-finalization basis even if `pendingInstantWithdraws` remains nonzero for other users.
- Add a regression test where two same-epoch instant requests are partially funded, one user claims, the borrower defaults, and the claimed user cannot receive a second recovery payment.

### Proof of Concept
A Foundry fork test can reproduce the issue with the following sequence:

```solidity
function testClaimedInstantWithdrawIsRecountedAfterDefault() external {
    uint256 amount = 100e18;

    // Arrange AA deposits and configure instant withdrawals.
    _depositWithUser(userA, amount, true);
    _depositWithUser(userB, amount, true);
    _setInstantWithdrawParams(0, aprDelta, false);

    // User A and user B request instant withdrawals in the same strategy epoch.
    vm.prank(userA);
    cdo.requestWithdraw(amount, address(AAtranche));
    vm.prank(userB);
    cdo.requestWithdraw(amount, address(AAtranche));

    uint256 requestEpoch = creditVault.epochNumber();
    assertEq(creditVault.instantWithdrawClaimsByEpoch(requestEpoch), 2 * amount);
    assertEq(creditVault.pendingInstantWithdraws(), 2 * amount);

    // Start epoch with only enough CDO liquidity to fund user A.
    // The remaining user B request remains pending.
    deal(address(underlying), address(cdo), amount);
    vm.prank(manager);
    cdo.startEpoch();

    assertEq(creditVault.pendingInstantWithdraws(), amount);

    // User A receives the funded 100 at par.
    vm.prank(userA);
    cdo.claimInstantWithdrawRequest();
    assertEq(underlying.balanceOf(userA), amount);

    // BUG: A's epoch-local receipt is still recorded even though it was paid.
    assertEq(
        creditVault.instantWithdrawsRequestsByEpoch(userA, requestEpoch),
        amount
    );
    assertEq(
        creditVault.instantWithdrawClaimsByEpoch(requestEpoch),
        2 * amount
    );

    // Honest borrower fails repayment during the running epoch.
    vm.warp(cdo.epochEndDate() + 1);
    deal(address(underlying), borrower, 0);
    vm.prank(manager);
    cdo.stopEpoch(0, expectedInterest);

    assertTrue(cdo.defaulted());

    // Recovery source supplies only the 100 tokens needed for unpaid user B.
    uint256 recovery = amount;
    deal(address(underlying), recoverySource, recovery);
    vm.prank(recoverySource);
    underlying.approve(address(creditVault), recovery);

    vm.prank(manager);
    cdo.finalizeDefault(recovery, recoverySource);

    // Default basis incorrectly includes A's already-paid 100:
    // activeBasis + 200 pending basis instead of activeBasis + 100.
    assertEq(creditVault.defaultRecoveryEpoch(), requestEpoch);

    uint256 aBefore = underlying.balanceOf(userA);
    uint256 bBefore = underlying.balanceOf(userB);

    // User A reclaims the already-paid receipt through default recovery.
    vm.prank(userA);
    cdo.claimInstantWithdrawRequest();

    uint256 aSecondClaim = underlying.balanceOf(userA) - aBefore;
    assertGt(aSecondClaim, 0);

    // User B's remaining recovery payout is reduced by A's second claim.
    vm.prank(userB);
    cdo.claimInstantWithdrawRequest();

    assertLt(underlying.balanceOf(userB) - bBefore, recovery);
    assertApproxEqAbs(
        aSecondClaim + (underlying.balanceOf(userB) - bBefore),
        recovery,
        2
    );
}
```

The important state corruption is the stale `instantWithdrawsRequestsByEpoch` and `instantWithdrawClaimsByEpoch` after the first claim; both mappings continue to classify a paid receipt as part of the default-epoch pending claim set.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L356-375)
```text
  function requestInstantWithdraw(uint256 _amount, address _user) external {
    _onlyIdleCDO();
    _ensureDefaultRecoveryInitialized();
    // burn strategy tokens from cdo
    _burn(msg.sender, _amount);
  
    // mint equal amount of strategy tokens to the user as receipt, useful in case of default
    _mint(_user, _amount);

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
  }
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L635-649)
```text
  /// @notice Total claim basis that should be haircut by default finalization.
  /// @dev Normal pending withdraws are already tracked globally. Current-epoch instant receipts
  /// join recovery only if `pendingInstantWithdraws` is still non-zero at finalization. This can
  /// happen when startEpoch moved the CDO's available cash to the strategy but that cash covered
  /// only part of the instant queue. The full current-epoch instant claim is included as basis,
  /// while the already-funded part is added to the reserve by `_defaultPrefundedInstantReserve()`.
  /// Receipt accounting is aggregate and does not retain AA/BB identity. IdleCDOEpochVariant
  /// therefore applies one recovery multiplier to both tranche classes.
  /// @return basis amount of defaulted receipt claims in underlying units
  function defaultPendingClaimBasis() public view returns (uint256 basis) {
    basis = pendingWithdraws;
    if (pendingInstantWithdraws != 0) {
      basis += instantWithdrawClaimsByEpoch[epochNumber];
    }
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L686-693)
```text
    uint256 reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve;
    // Recovery can be above par if the recovered funds exceed the computed basis.
    uint256 recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;

    defaultRecoveryFinalized = true;
    defaultRecoveryReserve = reserveAmount;
    defaultRecoveryPrice = recoveryPrice;
    defaultRecoveryEpoch = epochNumber;
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

**File:** contracts/IdleCDOEpochVariant.sol (L276-290)
```text
    // we should first check if there are *instant* redeem requests pending 
    // and if yes we should send as much underlyings as possible to the IdleCreditVault contract
    // if there is any surplus then we send those to the borrower
    uint256 pendingInstant = _pendingInstant();
    uint256 totUnderlyings = _contractTokenBalance(token);
    uint256 _pendingWithdraws = _strategy.pendingWithdraws();
    _strategy.collectInstantWithdrawFunds(pendingInstant > totUnderlyings ? totUnderlyings : pendingInstant);

    // if there are more requests than the current underlyings we simply send all underlyings
    // to the IdleCreditVault contract
    if (pendingInstant > totUnderlyings) {
      // if borrower is programmable, notify epoch start even if no funds were sent
      _startEpochProgrammableBorrower(_pendingWithdraws);
      return;
    }
```
