### Title
Programmable-borrower vault losses are floored as interest instead of reducing active NAV, allowing fully paid withdrawals to strand later LPs - ([File: contracts/IdleCDOEpochVariant.sol](contracts/IdleCDOEpochVariant.sol))

### Summary

`ProgrammableBorrower` correctly measures a vault principal loss through `vaultLoss()`, but `IdleCDOEpochVariant._stopEpoch()` consumes only `totalInterestDueNow()`. That function subtracts the loss from epoch gains and floors the result at zero, so a loss larger than gains produces zero epoch interest without reducing the pool’s strategy-token NAV. [1](#0-0) 

In minted-interest mode, the stop path still pulls the full unadjusted `pendingWithdraws` amount from the programmable borrower, while `_lossAmount` remains zero when the allowed `stopEpoch(0, 0)` path is used. [2](#0-1) 

### Finding Description

The programmable-borrower interface exposes two different values:

- `totalInterestDueNow()`: net epoch gain, floored at zero.
- `vaultLoss()`: principal loss of the ERC4626 sleeve.

`_resolveStopEpochInterest()` reads only the former when `isProgrammableBorrower` is enabled. [3](#0-2)  Because `isInterestMinted` is mandatory for programmable borrowers, `_amountToPullFromBorrower` is zero and only pending withdrawals are pulled as cash. [4](#0-3) 

When `_lossAmount` is zero, `previewLossAdjustedWithdrawFunds()` returns the entire pending-withdrawal basis as `pendingToFund`. [5](#0-4)  `collectWithdrawFunds()` then fully funds those receipts, while the missing vault assets are not burned through `burnStrategyTokens()` because that occurs only when `_lossAmount != 0`. [6](#0-5) 

A concrete sequence is:

1. Lenders deposit 100 underlying.
2. A lender requests withdrawal of 20, leaving active NAV of 80 and pending receipts of 20.
3. The epoch starts and the programmable borrower deposits the assets into the ERC4626 vault.
4. The vault loses 10 of principal.
5. `vaultLoss()` reports 10 and `totalInterestDueNow()` reports 0.
6. The manager calls the permitted `stopEpoch(0, 0)` path rather than separately supplying `_lossAmount`.
7. The protocol withdraws and funds the full 20 receipt amount.
8. Only 70 underlying remain for 80 active NAV.

The production test suite already demonstrates that `stopEpoch(0, 0)` is a supported successful path after a real programmable-vault loss. [7](#0-6) 

### Impact Explanation

Pending withdrawal receipts are paid at their pre-loss basis while the remaining vault loss stays invisible to active tranche NAV. This creates an undercollateralized active pool: first-funded or first-claiming users receive full value, while later tranche holders inherit the missing principal. [8](#0-7) 

Using the 100/20/10 example, the receipt holder receives 20, but the remaining 80 of active NAV is backed by only 70 underlying. The broken invariant is solvency: active strategy-token liabilities exceed the programmable borrower’s actual `underlyingToken + vault.convertToAssets()` backing. [9](#0-8) 

### Likelihood Explanation

The flaw requires an ERC4626 vault loss greater than the epoch’s combined vault and borrower interest, followed by use of the normal `stopEpoch(0, 0)` path. That path is explicitly permitted for programmable borrowers; `_interest` values above 1 are rejected, but no check requires `vaultLoss()` to be supplied as `_lossAmount`. [10](#0-9) 

The attacker does not need privileged access or to cause the market loss. A KYC-passing lender or existing receipt holder can simply claim a fully funded withdrawal before subsequent users discover the principal shortfall.

### Recommendation

In programmable-borrower mode, derive the realized active loss directly inside `_stopEpoch()` instead of relying on a separate optional `_lossAmount` argument. For example:

```solidity
uint256 externalLoss = IProgrammableBorrower(_borrower()).vaultLoss();
(_pendingWithdraws, _lossAmount) =
    _strategy.previewLossAdjustedWithdrawFunds(externalLoss);
```

Alternatively, make `stopEpoch(0, 0)` revert whenever `vaultLoss() != 0` and require the explicit loss-aware settlement path. The loss should be applied before funding pending receipts so pending claims receive their proportional recovery price rather than full pre-loss value.

The protocol should also add an invariant test asserting that after every successful programmable stop:

```solidity
programmableBorrower.totalUnderlying() + strategyHeldWithdrawFunds >= cdo.getContractValue()
```

within rounding tolerance.

### Proof of Concept

The following Foundry test follows the existing fork fixture in `test/foundry/ProgrammableBorrowerCreditVault.t.sol`. Moving vault shares with `rescueTokens()` is used only to deterministically simulate the real MetaMorpho loss already simulated by the production tests; it is not the attacker action. [11](#0-10) 

```solidity
function testVaultLossIsNotAppliedToPendingWithdrawals() external {
    uint256 amount = 100_000 * oneScale;
    uint256 pendingRequest = 20_000 * oneScale;
    address earlyClaimer = makeAddr("earlyClaimer");

    vm.prank(owner);
    cdoEpoch.setIsInterestMinted(true);

    // KYC/lender setup is inherited from the fork fixture.
    deal(USDC, earlyClaimer, amount, true);
    vm.startPrank(earlyClaimer);
    underlying.approve(address(idleCDO), amount);
    idleCDO.depositAA(amount);
    vm.stopPrank();

    // Active NAV becomes 80k and pending receipts become 20k.
    vm.prank(earlyClaimer);
    cdoEpoch.requestWithdraw(
        pendingRequest * ONE_TRANCHE / cdoEpoch.virtualPrice(address(aaTranche)),
        address(aaTranche)
    );

    _startEpochAndCheckPrices(0);

    uint256 activeNavBefore = cdoEpoch.getContractValue();
    uint256 borrowerAssetsBefore = programmableBorrower.totalUnderlying();
    assertEq(activeNavBefore, amount - pendingRequest);
    assertGe(borrowerAssetsBefore, activeNavBefore + pendingRequest);

    // Deterministically realize approximately 10k of external-vault principal loss.
    uint256 sharesToLose = morphoVault.balanceOf(address(programmableBorrower)) / 10;
    programmableBorrower.rescueTokens(
        address(morphoVault),
        makeAddr("externalLoss"),
        sharesToLose
    );

    uint256 realizedLoss = programmableBorrower.vaultLoss();
    assertGt(realizedLoss, 0);
    assertEq(
        programmableBorrower.totalInterestDueNow(),
        0,
        "loss must exceed all epoch gains for this scenario"
    );

    vm.warp(cdoEpoch.epochEndDate() + 1);

    // Permitted normal stop path: no explicit loss is supplied.
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    // The pending receipt is fully funded despite the vault principal loss.
    uint256 beforeClaim = underlying.balanceOf(earlyClaimer);
    vm.prank(earlyClaimer);
    cdoEpoch.claimWithdrawRequest();
    assertEq(
        underlying.balanceOf(earlyClaimer) - beforeClaim,
        pendingRequest,
        "receipt paid at pre-loss value"
    );

    // Active NAV remains 80k, but only about 70k remains in the borrower/vault sleeve.
    assertEq(cdoEpoch.getContractValue(), activeNavBefore);
    assertLt(
        programmableBorrower.totalUnderlying(),
        activeNavBefore,
        "active tranche NAV is undercollateralized"
    );
}
```

### Citations

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L330-346)
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
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L535-549)
```text
  /// @notice Return the total exposure in underlying terms (on-hand plus vault position).
  function totalUnderlying() external view returns (uint256) {
    return underlyingToken.balanceOf(address(this)) + _currentVaultAssets();
  }

  /// @notice Return the current vault share balance held by this contract.
  function vaultSharesBalance() external view returns (uint256) {
    return vault.balanceOf(address(this));
  }

  /// @notice Convert the current vault share position into underlying terms.
  function _currentVaultAssets() internal view returns (uint256) {
    uint256 shares = vault.balanceOf(address(this));
    return shares == 0 ? 0 : vault.convertToAssets(shares);
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L373-410)
```text
    uint256 _grossInterest = _isRequestingAllFunds ? _expectedInterest - _totBorrowed : _expectedInterest;
    bool _mintInterest = isInterestMinted && !_isRequestingAllFunds;
    // In minted mode IdleCDO only needs cash for withdraw requests, not for epoch interest itself.
    uint256 _amountToPullFromBorrower = _mintInterest ? 0 : _expectedInterest;
    if (_mintInterest && _interest > 1) {
      uint256 _maxApr = _strategy.maxApr();
      _checkNotAllowed(_maxApr != 0 && _grossInterest > _calcInterestWithApr(getContractValue(), _maxApr) + _pendingWithdrawFees);
    }

    // Checkpoint management fees before borrower funds are pulled in so the elapsed-period
    // accrual applies only to the pre-stop live NAV, not to newly received epoch interest.
    _accrueManagementFee();

    // Persist only resolved epoch interest for recovery accounting. In close-pool mode `_interest == 1`
    // is a sentinel: `_grossInterest` excludes the principal added to `_expectedInterest` above.
    expectedEpochInterest = _grossInterest;
    pendingWithdrawFees = _pendingWithdrawFees;

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

    // accrue interest to idleCDO, this will increase tranche prices.
    // Send also tot withdraw requests amount to the IdleCreditVault contract
    try this.getFundsFromBorrower(_amountToPullFromBorrower + _pendingWithdraws) {
      // transfer in strategy and decrease pendingWithdraws
        _strategy.collectWithdrawFunds(_pendingWithdraws);
```

**File:** contracts/IdleCDOEpochVariant.sol (L493-500)
```text
      }

      emit AccrueInterest(_expectedInterest - _totBorrowed, _totalFees);
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L411-429)
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
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L440-459)
```text
  function previewLossAdjustedWithdrawFunds(uint256 _lossAmount) external view returns (uint256 pendingToFund, uint256 activeLoss) {
    uint256 pendingBasis = pendingWithdraws;
    // Full zero-loss funding is safe for legacy aggregate receipts and needs no migration call.
    if (_lossAmount == 0) return (pendingBasis, _lossAmount);

    IIdleCDOEpochVariant cdo = IIdleCDOEpochVariant(idleCDO);
    uint256 activeBasis = _lossActiveBasis(cdo);
    if (pendingBasis == 0) {
      if (_lossAmount > activeBasis) revert NotAllowed();
      return (0, _lossAmount);
    }

    // Legacy pending receipts do not have the per-epoch ownership data needed to store a haircut.
    if (!defaultRecoveryInitialized) revert NotAllowed();
    uint256 totalBasis = activeBasis + pendingBasis;
    if (_lossAmount >= totalBasis) revert NotAllowed();

    uint256 pendingLoss = _lossAmount * pendingBasis / totalBasis;
    pendingToFund = pendingBasis - pendingLoss;
    activeLoss = _lossAmount - pendingLoss;
```

**File:** test/foundry/ProgrammableBorrowerCreditVault.t.sol (L282-300)
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
```
