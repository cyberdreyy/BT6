### Title
ERC4626 withdrawal losses after `totalInterestDueNow()` are erased from epoch accounting - (File: `contracts/strategies/idle/ProgrammableBorrower.sol`)

### Summary
`ProgrammableBorrower.onStopEpoch()` recalls ERC4626 liquidity only after `IdleCDOEpochVariant` has already snapshotted `totalInterestDueNow()` as epoch interest. The borrower values its remaining shares with `convertToAssets()`, which explicitly excludes withdrawal fees and redemption slippage in the ERC4626 interface. If `vault.withdraw()` burns more share value than the assets received, the redemption loss is neither included in the stopped epoch's interest nor preserved in the next buffer baseline because `bufferedVaultDelta` is cleared and `bufferStartVaultAssets` is set to the post-withdrawal value. Pending withdrawers can therefore be paid at par while the remaining active tranches retain an unhaircut NAV despite less backing.

### Finding Description
During `stopEpoch`, the CDO resolves programmable-borrower interest before calling the borrower hook, with `_resolveStopEpochInterest()` returning `IProgrammableBorrower(...).totalInterestDueNow()` [1](#0-0) . `totalInterestDueNow()` computes vault PnL from `_vaultNetInterest()`, which combines `_currentVaultAssets()` with already withdrawn assets [2](#0-1) .

`_currentVaultAssets()` is `vault.convertToAssets(balanceOf(this))` [3](#0-2) . The ERC4626 interface used by the contract states that `convertToAssets()` must exclude withdrawal fees and slippage, while `withdraw()` must be fee-inclusive [4](#0-3) [5](#0-4) .

After the interest value has already been resolved, `onStopEpoch()` calls `vault.withdraw(shortfall, address(this), address(this))` and records only `shortfall`, not the value of the shares consumed [6](#0-5) . It then clears `bufferedVaultDelta`, sets `bufferStartVaultAssets` to the already-depleted post-withdrawal `_currentVaultAssets()`, disables accounting, and returns success [7](#0-6) . Consequently, the extra share value consumed by the withdrawal is not charged to the ending epoch and becomes the baseline for the next buffer period.

The CDO then pulls the requested cash, calls `collectWithdrawFunds(_pendingWithdraws)`, updates accounting, and finishes the epoch without rebasing the borrower-side loss [8](#0-7) [9](#0-8) .

A concrete sequence is:

1. During the buffer phase, a KYC-passing AA holder deposits `100` underlying and requests `10` for withdrawal.
2. The manager starts an epoch; `onStartEpoch()` deposits the adapter's idle assets into the configured ERC4626 vault [10](#0-9) .
3. At epoch end, `totalInterestDueNow()` is read while `convertToAssets()` still reports `100`.
4. `onStopEpoch(10, false)` calls `vault.withdraw(10, ...)`. A fee-bearing or slippage-bearing vault sends `10` assets but burns shares worth `11` under `convertToAssets`.
5. `_currentVaultAssets()` falls to `89`, but that value is written as `bufferStartVaultAssets` after `bufferedVaultDelta` is reset, so the `1` unit loss is never carried forward.
6. The pending receipt receives the full `10`, while the CDO's remaining active claim is still priced against `90` strategy tokens even though only `89` of external-vault backing remains.

### Impact Explanation
This breaks the solvency and fair-loss-accounting invariant. The withdrawal receipt can be paid at par, while active tranche prices remain overstated by the redemption loss. In the `100/10` example, active tranches retain `90` claim value against `89` of remaining backing, creating an immediate `1` unit deficit. Larger withdrawal losses scale linearly and can exhaust junior backing or leave bad debt after the BB-first waterfall is bypassed. Because `stopEpoch` succeeds and epoch accounting is deactivated, the shortfall is not handled through the default path or through `stopEpochWithDuration(_lossAmount)` loss accounting [11](#0-10) [12](#0-11) .

### Likelihood Explanation
The issue requires the configured ERC4626 vault to have a redemption fee, redemption slippage, or another gap between ideal `convertToAssets()` value and shares consumed by `withdraw()`. No privileged role needs to misbehave: the normal lender withdrawal request and manager `stopEpoch()` sequence is sufficient once such a vault is configured. The existing coverage tests ordinary vault losses and liquidity failures, but not a successful withdrawal whose share burn exceeds the requested assets [13](#0-12) [14](#0-13) . Plain MetaMorpho deployments without withdrawal fees are unlikely to trigger the issue; fee-bearing ERC4626 implementations remain within the interface's allowed semantics.

### Recommendation
Measure the actual share-value cost of the stop-epoch withdrawal and include it in the epoch result before interest is finalized. For example, snapshot `beforeAssets = _currentVaultAssets()`, execute `vault.withdraw()`, snapshot `afterAssets`, calculate `realizedWithdrawLoss = beforeAssets - afterAssets - shortfall`, and report that loss to `IdleCDOEpochVariant` or apply `stopEpochWithDuration(..., realizedWithdrawLoss)`-style loss accounting. Alternatively, use `previewWithdraw(shortfall)` or share deltas to estimate and bound the share cost before the withdrawal, and require `maxLoss` / loss-cap parameters suitable for the configured vault. Do not reset `bufferedVaultDelta` and establish the next baseline in a way that erases a realized redemption deficit.

### Proof of Concept
A Foundry regression can reuse the programmable-borrower deployment from `ProgrammableBorrowerCreditVault.t.sol` and replace the configured vault with a minimal fee-on-withdraw ERC4626:

```solidity
contract FeeWithdrawVault is ERC20 {
    IERC20 public immutable assetToken;
    uint256 public constant FEE_BPS = 1_000; // 10%

    constructor(IERC20 asset_) ERC20("Fee Vault", "FV") {
        assetToken = asset_;
    }

    function asset() external view returns (address) {
        return address(assetToken);
    }

    function convertToAssets(uint256 shares) public view returns (uint256) {
        uint256 supply = totalSupply();
        return supply == 0 ? shares : shares * assetToken.balanceOf(address(this)) / supply;
    }

    function deposit(uint256 assets, address receiver) external returns (uint256 shares) {
        shares = totalSupply() == 0 ? assets : assets * totalSupply() / assetToken.balanceOf(address(this));
        assetToken.transferFrom(msg.sender, address(this), assets);
        _mint(receiver, shares);
    }

    // ERC4626-style withdraw: receiver gets exactly `assets`, but 10% is taken
    // from the owner's position by charging fee-inclusive shares.
    function withdraw(uint256 assets, address receiver, address owner)
        external
        returns (uint256 shares)
    {
        shares = assets * 10_000 / (10_000 - FEE_BPS);
        _burn(owner, shares);
        assetToken.transfer(receiver, assets);
    }

    function redeem(uint256 shares, address receiver, address owner)
        external
        returns (uint256 assets)
    {
        assets = convertToAssets(shares) * (10_000 - FEE_BPS) / 10_000;
        _burn(owner, shares);
        assetToken.transfer(receiver, assets);
    }
}
```

Test sequence:

```solidity
function testStopEpochHidesVaultWithdrawalFee() external {
    uint256 amount = 100 * oneScale;
    uint256 request = 10 * oneScale;

    FeeWithdrawVault feeVault = new FeeWithdrawVault(IERC20(USDC));

    vm.prank(borrowerManager);
    programmableBorrower.setVault(address(feeVault));

    vm.prank(owner);
    cdoEpoch.setIsInterestMinted(true);

    idleCDO.depositAA(amount);
    cdoEpoch.requestWithdraw(request, address(aaTranche));
    _startEpochAndCheckPrices(0);

    vm.warp(cdoEpoch.epochEndDate() + 1);

    uint256 activeBasisBefore = strategy.balanceOf(address(cdoEpoch));
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    // The vault sent 10 underlying but burned 11.111 share-value.
    assertEq(feeVault.balanceOf(address(programmableBorrower)), 88.888888e6);
    assertEq(programmableBorrower.bufferStartVaultAssets(), 88.888888e6);

    // The pending receipt was paid at par.
    assertEq(strategy.pendingWithdraws(), 0);

    // The CDO still prices the remaining active position as though 90 units
    // of backing remain, although the external position is worth 88.888...
    assertEq(strategy.balanceOf(address(cdoEpoch)), activeBasisBefore);
    assertApproxEqAbs(cdoEpoch.virtualPrice(address(aaTranche)), ONE_SCALE, 2);
}
```

The expected deficit is `request * FEE_BPS / (10_000 - FEE_BPS)`, or approximately `1.111111` underlying for a `10` underlying withdrawal with a 10% fee.

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L391-399)
```text
    // Pending receipts have no tranche identity, so their aggregate loss is pro rata across all
    // receipts. The remaining active loss is applied BB-first after the stop succeeds.
    (_pendingWithdraws, _lossAmount) = _strategy.previewLossAdjustedWithdrawFunds(_lossAmount);

    if (isProgrammableBorrower) {
      // Ask the programmable borrower to recall ERC4626 liquidity before IdleCDO pulls funds.
      // Hook reverts bubble so transient ERC4626 liquidity failures can be retried.
      if (!IProgrammableBorrower(_borrower()).onStopEpoch(_amountToPullFromBorrower + _pendingWithdraws, _isRequestingAllFunds)) {
        // Emit the exact cash liability requested from the borrower, including recalled principal
```

**File:** contracts/IdleCDOEpochVariant.sol (L406-436)
```text
    // accrue interest to idleCDO, this will increase tranche prices.
    // Send also tot withdraw requests amount to the IdleCreditVault contract
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
```

**File:** contracts/IdleCDOEpochVariant.sol (L473-486)
```text
      // stop epoch
      isEpochRunning = false;
      expectedEpochInterest = 0;
      pendingWithdrawFees = 0;

      if (!skipDefaultCheck) {
        // Reopen ordinary deposits and requests only when operations were not explicitly shut down.
        _unpause();
        allowAAWithdrawRequest = true;
        allowBBWithdrawRequest = true;
      }
      // block instant withdraws claims as these can be done only after the deadline
      // or only if borrower is repaying all funds
      allowInstantWithdraw = _isRequestingAllFunds;
```

**File:** contracts/IdleCDOEpochVariant.sol (L496-500)
```text
      if (_lossAmount != 0) {
        _strategy.burnStrategyTokens(_lossAmount);
        // Realize the active loss immediately through the ordinary BB-first waterfall.
        _forceUpdateAccounting();
      }
```

**File:** contracts/IdleCDOEpochVariant.sol (L997-1005)
```text
  /// @notice Resolve the stop-epoch interest value, optionally sourcing it from a programmable borrower.
  /// @dev Programmable borrowers are always the source of truth for epoch interest.
  /// In that mode `_interest` values `0` and `1` both resolve to the realized epoch interest,
  /// while `1` still separately signals the close-pool path to the caller.
  function _resolveStopEpochInterest(uint256 _interest) internal view returns (uint256 _resolvedInterest) {
    if (isProgrammableBorrower) {
      _checkNotAllowed(_interest > 1);
      return IProgrammableBorrower(_borrower()).totalInterestDueNow();
    }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L201-223)
```text
  function onStartEpoch(uint256 _pendingWithdraws) external nonReentrant {
    _checkOnlyIdleCDO();
    _accrueBorrowerInterest();
    // Reserve the amount IdleCDO expects to pull back at stopEpoch before the real borrower can draw again.
    epochPendingWithdraws = _pendingWithdraws;
    uint256 currentVaultAssets = _currentVaultAssets();
    uint256 bufferStartAssets = bufferStartVaultAssets;
    // Carry vault PnL generated while the pool was in the buffer into the new active epoch so it
    // is eventually realized in tranche prices at the next stopEpoch.
    bufferedVaultDelta = int256(currentVaultAssets) - int256(bufferStartAssets);
    bufferStartVaultAssets = 0;
    // Snapshot total assets before re-depositing idle cash so the epoch principal baseline uses the
    // exact pre-deposit amount instead of a post-deposit share-conversion round-down.
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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L256-267)
```text
    // `totalInterestDueNow()` was already read by IdleCDO before calling this hook, so once the
    // stop flow begins we can clear the previous carry and snapshot the remaining vault sleeve
    // as the baseline for measuring buffer-period vault PnL before the next epoch starts.
    bufferedVaultDelta = 0;
    bufferInterest = 0;
    bufferStartVaultAssets = _currentVaultAssets();

    // Stop reserving epoch-end withdraw liquidity once IdleCDO has started the stop flow.
    epochPendingWithdraws = 0;
    epochAccountingActive = false;
    emit EpochAccountingStopped();
    success = true;
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L330-347)
```text
  function totalInterestDueNow() external view returns (uint256) {
    (uint256 vaultInterest, uint256 loss) = _vaultNetInterest();
    uint256 totalGain = vaultInterest + borrowerInterestAccruedNow() + bufferInterest;
    return totalGain > loss ? totalGain - loss : 0;
  }

  /// @notice Compute the net vault delta split into interest and loss (mutually exclusive).
  function _vaultNetInterest() internal view returns (uint256 interest, uint256 loss) {
    if (!epochAccountingActive) return (0, 0);
    uint256 earnedAssets = _currentVaultAssets() + epochWithdrawnFromVault;
    uint256 principalAssets = epochStartVaultAssets + epochDepositedToVault;
    int256 netDelta = bufferedVaultDelta + int256(earnedAssets) - int256(principalAssets);
    if (netDelta > 0) {
      interest = uint256(netDelta);
    } else {
      loss = uint256(-netDelta);
    }
  }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L545-549)
```text
  /// @notice Convert the current vault share position into underlying terms.
  function _currentVaultAssets() internal view returns (uint256) {
    uint256 shares = vault.balanceOf(address(this));
    return shares == 0 ? 0 : vault.convertToAssets(shares);
  }
```

**File:** contracts/interfaces/IERC4626.sol (L61-72)
```text
     * scenario where all the conditions are met.
     *
     * - MUST NOT be inclusive of any fees that are charged against assets in the Vault.
     * - MUST NOT show any variations depending on the caller.
     * - MUST NOT reflect slippage or other on-chain conditions, when performing the actual exchange.
     * - MUST NOT revert.
     *
     * NOTE: This calculation MAY NOT reflect the “per-user” price-per-share, and instead should reflect the
     * “average-user’s” price-per-share, meaning what the average user should expect to see when exchanging to and
     * from.
     */
    function convertToAssets(uint256 shares) external view returns (uint256 assets);
```

**File:** contracts/interfaces/IERC4626.sol (L179-195)
```text
    /**
     * @dev Burns shares from owner and sends exactly assets of underlying tokens to receiver.
     *
     * - MUST emit the Withdraw event.
     * - MAY support an additional flow in which the underlying tokens are owned by the Vault contract before the
     *   withdraw execution, and are accounted for during withdraw.
     * - MUST revert if all of assets cannot be withdrawn (due to withdrawal limit being reached, slippage, the owner
     *   not having enough shares, etc).
     *
     * Note that some implementations will require pre-requesting to the Vault before a withdrawal may be performed.
     * Those methods should be performed separately.
     */
    function withdraw(
        uint256 assets,
        address receiver,
        address owner
    ) external returns (uint256 shares);
```

**File:** test/foundry/ProgrammableBorrowerCreditVault.t.sol (L282-307)
```text
    // Simulate a real loss on the MetaMorpho position by moving actual vault shares away from the
    // programmable borrower. This keeps the test on a real fork with the live deployed vault while
    // exercising the accounting branch where the vault sleeve is down.
    uint256 sharesToRescue = morphoVault.balanceOf(address(programmableBorrower)) / 5;
    assertGt(sharesToRescue, 0, "expected programmable borrower shares");
    programmableBorrower.rescueTokens(address(morphoVault), makeAddr("shareSink"), sharesToRescue);

    uint256 expectedBorrowerInterest = programmableBorrower.borrowerInterestAccruedNow();
    uint256 expectedVaultLoss = programmableBorrower.vaultLoss();
    uint256 expectedTotalInterest = programmableBorrower.totalInterestDueNow();

    assertGt(expectedBorrowerInterest, 0, "borrower interest should accrue");
    assertGt(expectedVaultLoss, 0, "vault loss should be recognized");
    assertLt(expectedTotalInterest, expectedBorrowerInterest, "pool interest should be net of vault loss");

    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    assertApproxEqAbs(cdoEpoch.lastEpochInterest(), expectedTotalInterest, 10, "epoch interest mismatch");
    assertApproxEqAbs(
      programmableBorrower.borrowerInterestDebt(),
      expectedBorrowerInterest,
      5,
      "contractual borrower debt should not be reduced by vault loss"
    );
  }
```

**File:** test/foundry/ProgrammableBorrowerCreditVault.t.sol (L372-399)
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
  }
```
