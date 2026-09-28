### Title
Unprivileged ERC4626 vault liquidity withdrawal can indefinitely block `stopEpoch` and queued withdrawals - (File: `contracts/strategies/idle/ProgrammableBorrower.sol`)

### Summary

In programmable-borrower mode, an unprivileged user of the configured ERC4626 vault can exhaust the vault’s immediately withdrawable liquidity after an epoch ends. `ProgrammableBorrower.onStopEpoch` converts an economically covered withdrawal failure into a reverting `StopEpochVaultLiquidityUnavailable` error, so the honest manager’s `IdleCDOEpochVariant.stopEpoch` call cannot complete. Because all effects before the hook are reverted, the epoch remains running, `pendingWithdraws` remain unfunded, and LPs cannot reach the post-epoch claim phase until the attacker allows vault liquidity to return.

### Finding Description

`IdleCDOEpochVariant._stopEpoch` first computes the cash required from the programmable borrower as `_amountToPullFromBorrower + _pendingWithdraws`, then calls `IProgrammableBorrower.onStopEpoch` before attempting the borrower transfer. A hook revert is not caught, so the entire state-changing call reverts. [1](#0-0) 

Inside `ProgrammableBorrower.onStopEpoch`, the borrower checks whether its idle ERC20 balance covers the required amount. If not, it verifies that the economic value of its vault shares covers the shortfall, but then performs a single `vault.withdraw(shortfall, address(this), address(this))`. Any ERC4626 withdrawal failure—including temporary insufficient vault cash despite adequate share value—is caught and converted into `StopEpochVaultLiquidityUnavailable`. [2](#0-1) 

The frozen state is externally visible: the epoch is only marked stopped later in `IdleCDOEpochVariant`, after borrower funds have been collected, fees processed, accounting updated, and interest deposited. Consequently, a hook revert preserves `isEpochRunning`, `epochEndDate`, `pendingWithdraws`, and active borrower accounting. [3](#0-2) 

The repository already demonstrates the condition with a covered but liquidity-limited vault: `convertToAssets` exceeds `pendingWithdraws`, `withdraw` is capped below the requested amount, `stopEpoch` reverts, and both `isEpochRunning` and `epochAccountingActive` remain true. [4](#0-3) 

The attacker does not need to be the configured borrower or hold a privileged role. Any address able to reduce the chosen ERC4626 vault’s available underlying liquidity—for example, a borrower in an underlying Morpho market or another vault participant whose transaction moves the vault to a withdrawal-limited state—can repeatedly ensure that the requested stop-epoch shortfall cannot be withdrawn.

### Impact Explanation

All liquidity contractually backed by the programmable borrower’s ERC4626 position can be made temporarily unavailable for epoch settlement. Concretely, the attacker can freeze:

- `pendingWithdraws`: normal withdrawal receipts waiting for epoch-end funding.
- `_amountToPullFromBorrower`: interest or, in close-pool mode, principal plus interest that IdleCDO attempts to collect.
- The entire pool when `stopEpoch(0, 1)` is used to close the pool, because `_isRequestingAllFunds` adds the strategy-token principal to the amount required. [5](#0-4) 

The loss is therefore bounded by and quantifiable as:

```solidity
frozenAssets =
    _amountToPullFromBorrower + _pendingWithdraws;
```

In close-pool mode this can approach the full pool NAV plus resolved epoch interest. During the freeze, receipt holders cannot claim because `pendingWithdraws` is never cleared, and new buffer-phase deposits cannot begin because the epoch remains running.

This is not merely an ERC20 transfer failure. The code intentionally distinguishes an economically undercollateralized shortfall—where it returns `true` and lets the later pull default—from an economically covered withdrawal failure, which it makes retryable. [6](#0-5)  That retryable path is the exploitable stall: it assumes transient illiquidity is preferable to default, but gives no progress mechanism when a third-party vault remains liquidity-constrained.

### Likelihood Explanation

Likelihood depends on the selected ERC4626 vault and its market design. For MetaMorpho-style vaults, available asset liquidity can legitimately become zero or lower than a shareholder’s `convertToAssets` value because assets are allocated to lending markets or subject to withdrawal queue liquidity. An unprivileged borrower who can post sufficient collateral can borrow the remaining vault liquidity near an epoch boundary.

The attack is timing-sensitive: it is most effective after `epochEndDate`, when the honest manager is expected to call `stopEpoch`. However, it can be repeated. Unlike a one-time revert, the attacker can maintain low available liquidity or repeat the drain whenever liquidity returns, extending the freeze. The existing production-path test confirms that a covered position with an insufficient withdrawal limit deterministically produces this state. [7](#0-6) 

The configured borrower cannot trigger this through `ProgrammableBorrower.borrow` during the epoch because that function reserves `epochPendingWithdraws` before calculating `availableToBorrow`. [8](#0-7)  The bypass is external to the adapter: unrelated ERC4626 vault participants reduce liquidity without touching `epochPendingWithdraws`.

### Recommendation

`ProgrammableBorrower.onStopEpoch` should avoid making full epoch settlement depend on one all-or-nothing `vault.withdraw` call. Safer designs include:

- Withdraw only `min(shortfall, vault.maxWithdraw(address(this)))`, return the funded amount and unpaid deficit explicitly, and let IdleCDO choose between retry, default, or proportional settlement.
- Add a manager-controlled grace period after the first liquidity failure. Once it expires, classify the covered-but-illiquid position as a facility default or initiate the appropriate loss-adjusted stop instead of leaving `isEpochRunning` indefinitely.
- Prefer `redeem`/`maxRedeem`-aware partial exits where the ERC4626 semantics support them, while retaining precise accounting through `epochWithdrawnFromVault`.
- Expose a permissioned fallback that disables `epochAccountingActive`, snapshots the available recovered amount, and routes the residual vault-share shortfall through default or recovery accounting rather than repeatedly reverting.

The fix should preserve the distinction between a true economic shortfall and temporary illiquidity, but should not allow temporary illiquidity to permanently block the epoch state machine.

### Proof of Concept

A Foundry mainnet-fork PoC can use the existing programmable-borrower setup and a real MetaMorpho/ERC4626 vault:

```solidity
function testExternalVaultLiquidityDrainFreezesStopEpoch() external {
    _setUpProgrammableBorrowerCreditVault(GAUNTLET_FORK_BLOCK, GAUNTLET_USDC_PRIME);

    uint256 amount = 10_000 * oneScale;

    vm.prank(owner);
    cdoEpoch.setIsInterestMinted(true);

    // Create an LP position and a pending normal withdrawal.
    idleCDO.depositAA(amount);
    uint256 receipt = cdoEpoch.requestWithdraw(
        aaTranche.balanceOf(address(this)) / 2,
        address(aaTranche)
    );
    assertGt(receipt, 0);

    _startEpochAndCheckPrices(0);

    uint256 pending = strategy.pendingWithdraws();
    assertGt(pending, 0);
    assertGt(
        morphoVault.convertToAssets(morphoVault.balanceOf(address(programmableBorrower))),
        pending
    );

    // Attacker is an unprivileged user of the ERC4626 vault ecosystem.
    // On the selected fork, drain the vault's available USDC liquidity by
    // borrowing/withdrawing through the underlying Morpho market such that:
    // morphoVault.maxWithdraw(address(programmableBorrower)) < pending.
    //
    // The exact borrow call is vault/market-specific; the required condition is:
    assertLt(morphoVault.maxWithdraw(address(programmableBorrower)), pending);

    vm.warp(cdoEpoch.epochEndDate() + 1);

    vm.expectRevert(StopEpochVaultLiquidityUnavailable.selector);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    assertTrue(cdoEpoch.isEpochRunning());
    assertEq(strategy.pendingWithdraws(), pending);
    assertTrue(programmableBorrower.epochAccountingActive());
    assertFalse(cdoEpoch.defaulted());

    // Every manager retry reverts while the attacker maintains the vault's
    // withdrawable liquidity below `pending`.
    vm.expectRevert(StopEpochVaultLiquidityUnavailable.selector);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);
}
```

The deterministic local equivalent is already encoded in `testProgrammableBorrowerStopEpochRevertsWhenVaultLiquidityUnavailable`: the vault covers the shares economically, but the withdrawal cap causes `stopEpoch` to revert and leaves the epoch running. [4](#0-3)

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L366-376)
```text
    // special case where we get everything back from the borrower
    if (_isRequestingAllFunds) {
      // Recall gross strategy-token principal, including fee backing excluded from net CDO NAV.
      // Prefunded variants also add queue deposits already sent directly to the borrower.
      _totBorrowed += _contractTokenBalance(strategyToken);
      _expectedInterest += _totBorrowed;
    }
    uint256 _grossInterest = _isRequestingAllFunds ? _expectedInterest - _totBorrowed : _expectedInterest;
    bool _mintInterest = isInterestMinted && !_isRequestingAllFunds;
    // In minted mode IdleCDO only needs cash for withdraw requests, not for epoch interest itself.
    uint256 _amountToPullFromBorrower = _mintInterest ? 0 : _expectedInterest;
```

**File:** contracts/IdleCDOEpochVariant.sol (L391-404)
```text
    // Pending receipts have no tranche identity, so their aggregate loss is pro rata across all
    // receipts. The remaining active loss is applied BB-first after the stop succeeds.
    (_pendingWithdraws, _lossAmount) = _strategy.previewLossAdjustedWithdrawFunds(_lossAmount);

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

**File:** contracts/IdleCDOEpochVariant.sol (L408-476)
```text
    try this.getFundsFromBorrower(_amountToPullFromBorrower + _pendingWithdraws) {
      // transfer in strategy and decrease pendingWithdraws
        _strategy.collectWithdrawFunds(_pendingWithdraws);
      // Only settle borrower interest when CDO is fronting it (minted mode, not closing pool).
      // When requesting all funds (_interest == 1) the CDO pulls cash directly, no fronting.
      if (_mintInterest && isProgrammableBorrower) {
        IProgrammableBorrower(_borrower()).settleBorrowerInterest();
      }
      // Split pending withdraw fees before update accounting
      // NOTE: Fees are sent with 2 different transfer calls, here and after updateAccounting, to avoid complicated calculations
      if (!_mintInterest) {
        _transferFeeUnderlyings(_pendingWithdrawFees);
      }

      if (_isRequestingAllFunds) {
        // we already have strategyTokens equal to _totBorrowed in this contract
        // so we transfer _totBorrowed to the strategy to avoid double counting for getContractValue
        _transferUnderlyings(address(_strategy), _totBorrowed);
      }

      if (_mintInterest) {
        // if interest is not transferred we mint strategy tokens equal to the full epoch interest
        if (_grossInterest != 0) _strategy.mintStrategyTokens(_grossInterest);
        // and increase unclaimedFees by pending withdraw fees before _updateAccounting
        unclaimedFees += _pendingWithdrawFees;
      }

      // update tranche prices and unclaimed fees
      _updateAccounting();

      // transfer fees
      uint256 _fees = unclaimedFees;
      if (_mintInterest) {
        // If interest is minted then we mint new shares for fee receivers instead of transferring underlyings
        if (_fees != 0) {
          uint256 feeReceiverAmount = _feeReceiverAmount(_fees);
          if (feeReceiverAmount != 0) {
            _mintSharesAtCurrPrice(feeReceiverAmount, feeReceiver, AATranche);
          }
          _mintSharesAtCurrPrice(_fees - feeReceiverAmount, owner(), AATranche);
          _updateSplitRatio(_getAARatio(true));
        }
      } else {
        // Cash-funded fees can only use gross interest not already owed to pending withdrawals.
        uint256 _availableForFees = _grossInterest > _pendingWithdrawFees ? _grossInterest - _pendingWithdrawFees : 0;
        if (_fees > _availableForFees) {
          _fees = _availableForFees;
        }
        _transferFeeUnderlyings(_fees);
      }
      // Any fee that cannot be paid in cash remains accrued and continues reducing NAV.
      unclaimedFees -= _fees;

      uint256 _totalFees = _fees + (_mintInterest ? 0 : _pendingWithdrawFees);
      // save net gain (this does not include interest gained for pending withdrawals)
      uint256 netInterest = _grossInterest > _totalFees ? _grossInterest - _totalFees : 0;
      lastEpochInterest = netInterest;
      // mint strategyTokens equal to interest and send underlying to strategy to avoid double counting for NAV
      _strategy.deposit(_mintInterest ? 0 : netInterest);

      // save last apr, unscaled
      lastEpochApr = _strategy.unscaledApr();
      // set apr for next epoch
      _setScaledApr(_newApr);

      // stop epoch
      isEpochRunning = false;
      expectedEpochInterest = 0;
      pendingWithdrawFees = 0;
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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L349-355)
```text
  /// @notice current borrowable liquidity excluding reserved withdraw requests
  function availableToBorrow() public view returns (uint256) {
    uint256 totalAssets = underlyingToken.balanceOf(address(this)) + _currentVaultAssets();
    // Interest is minted (not pulled as cash), so only pending withdraw requests need reservation.
    uint256 reserved = epochPendingWithdraws;
    return totalAssets <= reserved ? 0 : totalAssets - reserved;
  }
```

**File:** test/foundry/ProgrammableBorrowerCreditVault.t.sol (L372-398)
```text
  function testProgrammableBorrowerStopEpochRevertsWhenVaultLiquidityUnavailable() external {
    MockStopEpochLiquidityVault limitedVault = new MockStopEpochLiquidityVault(USDC);
    uint256 amount = 10_000 * oneScale;

    vm.prank(owner);
    cdoEpoch.setIsInterestMinted(true);
    vm.prank(manager);
    programmableBorrower.setVault(address(limitedVault));

    idleCDO.depositAA(amount);
    cdoEpoch.requestWithdraw(aaTranche.balanceOf(address(this)) / 2, address(aaTranche));
    _startEpochAndCheckPrices(0);

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
