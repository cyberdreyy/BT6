### Title
Stale instant-withdraw epoch accounting permanently blocks recovery claims - (contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`claimInstantWithdrawRequest` clears the aggregate receipt balance but leaves `instantWithdrawsRequestsByEpoch[user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]` unchanged. If the same user creates another instant-withdraw receipt in that epoch and the borrower subsequently defaults, `defaultPendingClaimBasis` includes both the already-claimed amount and the new pending amount. During recovery claiming, `_claimDefaultedInstantWithdrawRequest` subtracts the stale combined basis from the smaller aggregate balance, causing an arithmetic revert and permanently preventing the user’s later recovery claim.

### Finding Description
`requestInstantWithdraw` records each request in three places: `instantWithdrawsRequests[user]`, `instantWithdrawsRequestsByEpoch[user][epochNumber]`, and `instantWithdrawClaimsByEpoch[epochNumber]`. [1](#0-0) 

The successful claim path reads only the aggregate `instantWithdrawsRequests[user]`, burns that amount, and resets only that aggregate field. [2](#0-1) 

Neither `instantWithdrawsRequestsByEpoch[user][epoch]` nor `instantWithdrawClaimsByEpoch[epoch]` is decremented after the funded claim. [2](#0-1) 

If a second request is made in the same epoch, the per-epoch basis becomes `firstAmount + secondAmount`, while the aggregate outstanding receipt is only `secondAmount`. [3](#0-2) 

When default recovery is finalized while `pendingInstantWithdraws != 0`, `defaultPendingClaimBasis` adds the stale `instantWithdrawClaimsByEpoch[epochNumber]` to the defaulted claim basis. [4](#0-3) 

After finalization, `claimInstantWithdrawRequest` invokes `_claimDefaultedInstantWithdrawRequest`, which reads the full stale per-epoch basis and then executes `instantWithdrawsRequests[user] -= claimBasis`. [5](#0-4) [6](#0-5) 

For the two-request sequence, this evaluates `secondAmount - (firstAmount + secondAmount)`, which underflows under Solidity 0.8 checked arithmetic and reverts before the recovery transfer can occur. [7](#0-6) 

### Impact Explanation
A user’s second instant-withdraw recovery claim can be permanently frozen after default finalization. [8](#0-7) [7](#0-6) 

The frozen amount equals the outstanding second receipt amount: for an already-claimed receipt of `A` and a second defaulted receipt of `B`, every claim attempts `B - (A + B)` and reverts, so `B * defaultRecoveryPrice / 1e18` cannot be claimed. [7](#0-6) 

The stale epoch claim also causes `_defaultPrefundedInstantReserve` to classify the previously claimed `A` as if it were still held as prefunded reserve whenever `A + B > pendingInstantWithdraws`, corrupting default recovery pricing and reserve accounting. [9](#0-8) [10](#0-9) 

### Likelihood Explanation
The sequence only requires an unprivileged tranche holder to submit two instant-withdraw requests in the same strategy epoch, with a successful funded claim between them, followed by borrower default finalization while the second request remains pending. [11](#0-10) [12](#0-11) 

Instant-withdraw mode is entered through `requestWithdraw` when the current unscaled APR has fallen below `lastEpochApr + instantWithdrawAprDelta`, and the privileged `getInstantWithdrawFunds` call only funds the currently pending amount. [13](#0-12) [14](#0-13) 

The guards do not prevent the sequence: claiming a funded instant withdrawal does not consume the per-epoch accounting, and default finalization later treats that stale accounting as outstanding. [2](#0-1) [15](#0-14) 

### Recommendation
In `claimInstantWithdrawRequest`, clear the caller’s basis in `instantWithdrawsRequestsByEpoch[user][epochNumber]` and subtract the claimed amount from `instantWithdrawClaimsByEpoch[epochNumber]` whenever that receipt is successfully paid. [2](#0-1) 

The cleanup must handle multiple instant receipts in the same epoch and must not remove per-epoch basis belonging to a still-unfunded request; tracking a separate claimed/funded amount per epoch is the safest invariant. [3](#0-2) 

Alternatively, prevent a user from creating another instant-withdraw request in an epoch until all prior per-epoch instant receipt accounting for that user has been cleared. [1](#0-0) 

### Proof of Concept
A Foundry fork test can reproduce the broken invariant with this sequence:

```solidity
function testStaleInstantEpochBasisBlocksDefaultClaim() public {
    // Setup: KYC user, deposit AA/BB, start an epoch, and lower the strategy APR
    // enough that requestWithdraw takes the instant-withdraw branch.
    uint256 first = 1_000e6;
    uint256 second = 500e6;

    // 1. User submits the first instant request in epoch N.
    vm.prank(user);
    cdoEpoch.requestWithdraw(first, address(AAtranche));

    uint256 epoch = creditVault.epochNumber();
    assertEq(creditVault.instantWithdrawsRequestsByEpoch(user, epoch), first);
    assertEq(creditVault.instantWithdrawClaimsByEpoch(epoch), first);

    // 2. Honest manager funds the pending instant request after the deadline.
    vm.warp(cdoEpoch.instantWithdrawDeadline() + 1);
    deal(underlying, borrower, first);
    vm.prank(borrower);
    IERC20(underlying).approve(address(cdoEpoch), first);
    vm.prank(manager);
    cdoEpoch.getInstantWithdrawFunds();

    // 3. User successfully claims first. Aggregate accounting clears,
    //    but per-epoch accounting remains stale.
    vm.prank(user);
    cdoEpoch.claimInstantWithdrawRequest();

    assertEq(creditVault.instantWithdrawsRequests(user), 0);
    assertEq(creditVault.instantWithdrawsRequestsByEpoch(user, epoch), first);
    assertEq(creditVault.instantWithdrawClaimsByEpoch(epoch), first);

    // 4. User submits a second instant request in the same epoch.
    vm.prank(user);
    cdoEpoch.requestWithdraw(second, address(AAtranche));

    assertEq(creditVault.instantWithdrawsRequests(user), second);
    assertEq(
        creditVault.instantWithdrawsRequestsByEpoch(user, epoch),
        first + second
    );

    // 5. Borrower fails to fund the epoch and recovery is finalized with
    //    pendingInstantWithdraws != 0.
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    vm.expectRevert(); // borrower funding path fails
    cdoEpoch.stopEpoch(0, 0);

    // Trigger the production finalization path with whatever partial recovery
    // is available. This sets defaultRecoveryFinalized and
    // defaultInstantWithdrawsFinalized.
    finalizeRecoveryForTest();

    // 6. Claim attempts second - (first + second), underflows, and cannot pay.
    vm.prank(user);
    vm.expectRevert();
    cdoEpoch.claimInstantWithdrawRequest();
}
```

The core assertion is the post-claim state: `instantWithdrawsRequests[user] == 0` while `instantWithdrawsRequestsByEpoch[user][epoch] == first`, demonstrating that the receipt was paid once but still remains recorded as outstanding epoch basis. [2](#0-1) [7](#0-6)

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L356-392)
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L661-696)
```text
  function finalizeDefaultRecovery(uint256 _recoveredAmount, address _recoverySource) external returns (uint256 defaultBBNav) {
    _onlyIdleCDO();
    _ensureDefaultRecoveryInitialized();

    IIdleCDOEpochVariant cdo = IIdleCDOEpochVariant(idleCDO);
    if (defaultRecoveryFinalized || !cdo.defaulted()) revert NotAllowed();
    if (_recoveredAmount != 0 && _recoverySource == address(0)) revert NotAllowed();

    // Active holders are still represented by strategy tokens owned by the CDO. Add the
    // default-epoch net interest so they use the same claim basis as pending redeemers.
    // Split gross backing by saved NAV and default interest by the configured APR split.
    // The CDO strategy-token balance is its gross active value before `unclaimedFees`.
    // Using it directly restores those waived unpaid fees to active recovery basis.
    uint256 activeBalance = balanceOf(idleCDO);
    uint256 activeInterest = _defaultActiveInterestBasis(cdo);
    uint256 activeBasis = activeBalance + activeInterest;
    defaultBBNav = _defaultBBBasis(cdo, activeBalance, activeInterest);
    // Pending receipts have already left active CDO NAV, so they are added as a separate basis.
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L842-855)
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

**File:** contracts/IdleCDOEpochVariant.sol (L758-769)
```text
    // Programmable borrower deployments do not support instant withdrawals.
    // If apr decresed wrt last epoch, request instant withdraw and burn tranche tokens directly
    // we compare unscaled aprs
    if (_isInstantWithdrawEnabled()) {
      uint256 currentApr = creditVault.unscaledApr();
      if (lastEpochApr > (currentApr + instantWithdrawAprDelta)) {
        // burn strategy tokens from cdo and mint an equal amount to msg.sender as receipt
        creditVault.requestInstantWithdraw(_underlyings, msg.sender);
        // burn tranche tokens and decrease NAV
        _withdrawOps(_amount, _underlyings, _tranche);
        return _underlyings;
      }
```
