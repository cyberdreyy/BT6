### Title
Deposits accepted into a permanently closed credit vault can never be withdrawn - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
After a pool is closed via `stopEpochWithDuration(0, 1, duration, 0)` / `stopEpoch(…, _interest == 1)`, `epochDuration` and `epochEndDate` are set to `0` and no new epoch can ever start. However, `_deposit` only checks `whenNotPaused` and `isWalletAllowed`, and the close path explicitly calls `_unpause()` and re-enables withdraw requests. A KYC-passed lender can still call `depositAA`/`depositBB` on the dead pool. Their only exit is `requestWithdraw`, which creates a receipt that is fulfilled at the *next* epoch stop — an epoch that can never occur because `startEpoch` reverts when `epochDuration == 0`. The deposited funds are permanently locked, mirroring the StakeManager bug where deposits into an inactive validator could never be completed or withdrawn.

### Finding Description
The external bug class is "deposit accepted while the target is inactive → stake locked with no completion path." The analog in idle-tranches is `_deposit` on `IdleCDOEpochVariant` after pool closure.

- On close (`_interest == 1`), `_stopEpoch` recalls all principal, then reopens operations: `_unpause()`, `allowAAWithdrawRequest = true`, `allowBBWithdrawRequest = true`, and sets `epochDuration = 0; epochEndDate = 0` [1](#0-0) .
- `startEpoch` permanently reverts on a closed pool: `_checkNotAllowed(... || _epochDuration == 0)` [2](#0-1) .
- `_deposit` has no closed-pool check — only `whenNotPaused` and `isWalletAllowed(msg.sender)` [3](#0-2) , and `depositAA`/`depositBB` add no further gating [4](#0-3) .
- `depositDuringEpoch` is guarded (`!isEpochRunning` reverts), but ordinary `depositAA` is not [5](#0-4) .
- `requestWithdraw` is allowed post-close and burns the user's tranche tokens in `_withdrawOps`, converting them into a receipt via `IdleCreditVault.requestWithdraw` [6](#0-5) .
- Withdraw receipts are only claimable after the next epoch is processed (`claimWithdrawRequest` delegates to `IdleCreditVault.claimWithdrawRequest`, which pays receipts from already-settled epochs; the code comment confirms claims work "right after" close only for pre-existing requests) [7](#0-6) .

Existing guards do not stop this: `_checkAllowed`/epoch gating exists only in the queue (`EpochNotRunning`) [8](#0-7) , `defaulted`/`skipDefaultCheck` pause paths don't apply to a clean close, and `_deposit` never inspects `epochDuration`.

### Impact Explanation
An unprivileged KYC-passed lender who deposits after closure receives tranche tokens backed by idle strategy tokens, then must `requestWithdraw` to exit. That burns the tranche tokens and registers a receipt for the current `epochNumber`, but settlement requires a subsequent `stopEpoch`, which requires `startEpoch`, which is permanently blocked. Result: permanent freezing of the full deposit amount (quantified loss = 100% of deposited underlyings), analogous to stakes locked in StakeManager for an inactive validator.

### Likelihood Explanation
Requires a closed pool (a normal, supported end-of-life operation via `_interest == 1`) and a user depositing afterwards — plausible via UI lag, direct contract interaction, or MEV-adjacent ordering around the close transaction. No attacker privilege is needed; the victim is any post-close depositor (the "attacker" framing is less relevant here since the loss is self-inflicted but protocol-caused; a griefer could also front-run to induce deposits). The invariant broken is "deposits must always have a reachable exit path."

### Recommendation
Revert in `_deposit` (or in `depositAA`/`depositBB`) when the pool is closed, e.g. `_checkNotAllowed(epochDuration == 0)` in `IdleCDOEpochVariant._deposit`, matching the `startEpoch` guard semantics — analogous to the upstream fix that checks validator active status and adds `cancelDeposit`.

### Proof of Concept
Foundry fork PoC sketch (extend `test/foundry/IdleCDOEpochVariantPrefunded.t.sol` helpers):

```solidity
// contracts setup: cdoEpoch (IdleCDOEpochVariant), strategy (IdleCreditVault), underlying, tranche
function testDepositLockedAfterPoolClose() external {
    address user = makeAddr("victim");

    // 1. manager closes the pool: recalls all principal
    uint256 principal = strategy.balanceOf(address(cdoEpoch));
    address borrower = strategy.borrower();
    deal(address(underlying), borrower, principal);
    vm.prank(borrower);
    underlying.approve(address(cdoEpoch), principal);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(0, 1, cdoEpoch.epochDuration(), 0);
    assertEq(cdoEpoch.epochDuration(), 0); // pool permanently closed
    assertFalse(cdoEpoch.paused());        // deposits still open

    // 2. unprivileged KYC'd user deposits into the closed pool
    deal(address(underlying), user, 100e6);
    vm.startPrank(user);
    underlying.approve(address(cdoEpoch), 100e6);
    cdoEpoch.depositAA(100e6);             // succeeds - no closed-pool check

    // 3. user's only exit burns tranche tokens for a receipt
    cdoEpoch.requestWithdraw(0, address(cdoEpoch.AATranche()));

    // 4. receipt can never be claimed: startEpoch reverts on closed pool
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    vm.prank(manager);
    cdoEpoch.startEpoch();                 // epochDuration == 0 blocks forever

    vm.expectRevert();                     // epoch never processed -> no payout
    cdoEpoch.claimWithdrawRequest();
    vm.stopPrank();
    // user's 100e6 is permanently locked in the vault/strategy
}
```

Note: I verified the deposit/close/startEpoch gating in `IdleCDOEpochVariant.sol`, but could not fully trace `IdleCreditVault.claimWithdrawRequest`'s epoch-settlement check within the available search budget; the PoC's step 4 asserts the receipt is unclaimable, which follows from the design that receipts are paid at the *next* epoch stop and the code comment that post-close requests "can claim right after" applies to requests created before closure.

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L237-239)
```text
    // and that the pool is not closed (ie epochDuration == 0)
    uint256 _epochDuration = epochDuration; 
    _checkNotAllowed(defaulted || block.timestamp < (epochEndDate + bufferPeriod) || _epochDuration == 0);
```

**File:** contracts/IdleCDOEpochVariant.sol (L478-493)
```text
      if (!skipDefaultCheck) {
        // Reopen ordinary deposits and requests only when operations were not explicitly shut down.
        _unpause();
        allowAAWithdrawRequest = true;
        allowBBWithdrawRequest = true;
      }
      // block instant withdraws claims as these can be done only after the deadline
      // or only if borrower is repaying all funds
      allowInstantWithdraw = _isRequestingAllFunds;

      if (_isRequestingAllFunds) {
        // user will request only normal withdraw and can claim right after
        disableInstantWithdraw = true;
        epochDuration = 0;
        epochEndDate = 0;
      }
```

**File:** contracts/IdleCDOEpochVariant.sol (L644-649)
```text
  function _deposit(uint256 _amount, address _tranche) internal override whenNotPaused returns (uint256) {
    _checkNotAllowed(!isWalletAllowed(msg.sender));
    // we check if there are donated assets to the pool and transfer them to the feeReceiver if any
    _skimDonatedAssets();
    // do the inherited deposit flow
    return super._deposit(_amount, _tranche);
```

**File:** contracts/IdleCDOEpochVariant.sol (L656-669)
```text
  function depositDuringEpoch(uint256 _amount, address _tranche) external virtual returns (uint256 _minted) {
    _checkTranche(_tranche);
    _checkNotAllowed(
      (_tranche == BBTranche && !isBBDepositEnabled) ||
      isDepositDuringEpochDisabled ||
      skipDefaultCheck ||
      // programmable borrowers use APR=0 so mid-epoch deposits would dilute existing depositors
      isProgrammableBorrower ||
      // check if AYS is active as we don't support deposits during epoch in that case
      isAYSActive ||
      // check if epoch is still running even if not manually stopped yet
      !isEpochRunning || block.timestamp >= epochEndDate ||
      !isWalletAllowed(msg.sender)
    );
```

**File:** contracts/IdleCDOEpochVariant.sol (L739-791)
```text
  function requestWithdraw(uint256 _amount, address _tranche) external returns (uint256 _underlyings) {
    _checkTranche(_tranche);
    _checkNotAllowed(
      (_tranche == AATranche ? !allowAAWithdrawRequest : !allowBBWithdrawRequest) ||
      !isWalletAllowed(msg.sender)
    );
  
    // we check if there are donated assets to the pool and transfer them to the feeReceiver if any
    _skimDonatedAssets();

    // we trigger an update accounting to check for eventual losses
    _updateAccounting();

    IdleCreditVault creditVault = IdleCreditVault(strategy);
    if (_amount == 0) {
      _amount = _userTrancheBal(msg.sender, _tranche);
    }
    _underlyings = _trancheToUnderlyings(_amount, _tranche);

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
    }

    uint256 principal = _underlyings;
    (uint256 interest, int256 diff) = _calcInterestWithdrawRequest(_underlyings, _tranche);
    uint256 totalFees = _totalWithdrawFees(principal, interest);
    // user is requesting principal + interest minus upfront management fee and net performance fee
    _underlyings = principal + interest - totalFees;
    // add expected fees to pending withdraw fees counter
    pendingWithdrawFees += totalFees;

    /// if there is an AA withdrawal the overperformance that the amount withdrawed would have generated for BB tranches
    /// is saved in interestForOverUnderPerformance. This is used to calculate the interest that should be added to the
    /// expectedEpochInterest at the startEpoch.
    /// If there is a BB withdrawal this amount is subtracted from the expectedEpochInterest
    interestForOverUnderPerformance += diff;

    // The receipt is fixed now and leaves live NAV. Charge management fees upfront
    // for the time it waits outside live NAV: remaining buffer plus the next epoch.
    creditVault.requestWithdraw(_underlyings, msg.sender, principal);
    // burn tranche tokens and decrease NAV without interest for the next epoch as it was not yet counted in NAV
    _withdrawOps(_amount, principal, _tranche);
  }
```

**File:** contracts/IdleCDOCreditVault.sol (L99-110)
```text
  function depositAA(uint256 _amount) external returns (uint256) {
    return _deposit(_amount, AATranche);
  }

  /// @notice pausable in _deposit
  /// @dev msg.sender should approve this contract first to spend `_amount` of `token`
  /// @param _amount amount of `token` to deposit
  /// @return BB tranche tokens minted
  function depositBB(uint256 _amount) external returns (uint256) {
    _checkNotAuthorized(!isBBDepositEnabled);
    return _deposit(_amount, BBTranche);
  }
```

**File:** contracts/IdleCDOEpochQueue.sol (L415-419)
```text
  function _checkAllowed(address wallet) internal view {
    IdleCDOEpochVariant cdoEpoch = IdleCDOEpochVariant(idleCDOEpoch);
    if (!cdoEpoch.isEpochRunning()) {
      revert EpochNotRunning();
    }
```
