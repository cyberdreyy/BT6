### Title
An external ERC4626 vault user can repeatedly block epoch settlement and freeze pending withdrawals - ([File: contracts/strategies/idle/ProgrammableBorrower.sol])

### Summary
In programmable-borrower mode, `IdleCDOEpochVariant.stopEpoch` relies on `ProgrammableBorrower.onStopEpoch` to synchronously withdraw the full cash shortfall from the configured ERC4626 vault. The adapter considers the shortfall covered whenever `convertToAssets(shares)` covers it, but a third-party vault user can reduce the vault's immediately withdrawable liquidity below that amount, causing `vault.withdraw` to revert and the entire `stopEpoch` transaction to fail. Repeating the liquidity drain prevents epoch settlement while keeping the LP funds economically represented by vault shares. [1](#0-0) [2](#0-1) 

### Finding Description
The vault liquidity check uses share value rather than executable withdrawal capacity:

```solidity
if (shortfall > _currentVaultAssets()) return true;
try vault.withdraw(shortfall, address(this), address(this)) { ... }
catch {
  revert StopEpochVaultLiquidityUnavailable();
}
``` [1](#0-0) 

`_currentVaultAssets` is only `convertToAssets(balanceOf(programmableBorrower))`; it does not represent how much underlying can currently be withdrawn from a shared or utilization-constrained ERC4626 vault. [3](#0-2) 

A concrete sequence is:

1. LP deposits into the credit vault and requests withdrawal during the buffer period, creating `pendingWithdraws = W`.
2. The manager starts the epoch; `ProgrammableBorrower` deposits idle cash into the ERC4626 vault and reserves `epochPendingWithdraws = W`. [4](#0-3) 
3. An attacker who is also a user/shareholder of that ERC4626 vault withdraws, borrows, or otherwise consumes underlying liquidity until the vault can pay out less than `W`, while the programmable borrower's shares still value to at least `W`.
4. After `epochEndDate`, the manager calls `stopEpoch`. `_currentVaultAssets()` reports the position as covered, so the adapter attempts `vault.withdraw(W)`.
5. The vault reverts because executable liquidity is below `W`; `onStopEpoch` converts that failure to `StopEpochVaultLiquidityUnavailable`, and the CDO's stop transaction reverts before state is finalized. [1](#0-0) [5](#0-4) 
6. The attacker repeats step 3 whenever liquidity returns, preventing settlement without requiring a privileged role.

The withdrawal claimant cannot bypass this by calling `claimWithdrawRequest` because the claim path requires `epochNumber > lastWithdrawRequest`; `epochNumber` is only advanced by a successful stop. [6](#0-5) 

### Impact Explanation
The attacker can repeatedly prevent epoch closure and temporarily freeze `W` of matured withdrawal receipts, plus the active NAV that remains invested in the epoch. For example, with `W = 1,000,000 USDC`, leaving only `999,999 USDC` of executable vault liquidity makes every stop attempt revert even though `convertToAssets` reports coverage of `1,000,000 USDC`. The impact is denial of settlement rather than direct theft: receipt holders cannot receive the funded payout until either vault liquidity recovers or the trusted operators perform an emergency recovery/default action. [7](#0-6) [8](#0-7) 

### Likelihood Explanation
This requires a programmable-borrower deployment, at least one pending normal withdrawal, and an external ERC4626 vault whose executable liquidity can be reduced by an unprivileged participant. Such liquidity conditions are normal for shared vaults and lending-market vaults; the attacker only needs to maintain utilization/illiquidity around the stop call. No malicious privileged role is required. [9](#0-8) [1](#0-0) 

### Recommendation
Do not allow a transient ERC4626 withdrawal failure to make the epoch-settlement path permanently retry-dependent without an escape. Suitable changes include:

- use `maxWithdraw`/`maxRedeem` as an additional coverage check and explicitly classify an economically covered but unwithdrawable position as a settlement failure/default after a bounded retry policy;
- support partial liquidity recalls where safe, while preserving exact receipt funding;
- provide a dedicated manager path that settles a vault-liquidity default without requiring the failing `vault.withdraw` call;
- document and bound how long `StopEpochVaultLiquidityUnavailable` may leave `isEpochRunning` true.

Any fallback must avoid misclassifying a true valuation loss as temporary illiquidity and must preserve `pendingWithdraws` and receipt accounting. [10](#0-9) [11](#0-10) 

### Proof of Concept
```solidity
// test/foundry/ProgrammableBorrowerVaultLiquidityGrief.t.sol
function testVaultUserCanRepeatedlyBlockStopEpoch() external {
    // Existing fork setup used by ProgrammableBorrowerCreditVault.t.sol.
    _setUpProgrammableBorrowerCreditVault(GAUNTLET_FORK_BLOCK, GAUNTLET_USDC_PRIME);
    IERC4626 externalVault = IERC4626(GAUNTLET_USDC_PRIME);

    uint256 amount = 10_000 * oneScale;
    vm.prank(owner);
    cdoEpoch.setIsInterestMinted(true);

    idleCDO.depositAA(amount);

    // LP creates a matured withdrawal liability W before the epoch starts.
    uint256 shares = aaTranche.balanceOf(address(this)) / 2;
    uint256 W = cdoEpoch.requestWithdraw(shares, address(aaTranche));
    assertGt(W, 0);

    _startEpochAndCheckPrices(0);

    // Attacker is an ordinary external-vault shareholder/liquidity user.
    // On the selected fork, choose an account with enough vault shares to leave
    // less than W of executable underlying liquidity while PB's share value >= W.
    address vaultLp = GAUNTLET_LP_WHALE;
    uint256 available = externalVault.maxWithdraw(address(programmableBorrower));
    vm.prank(vaultLp);
    externalVault.withdraw(available - W + 1, vaultLp, vaultLp);

    assertGe(
        externalVault.convertToAssets(externalVault.balanceOf(address(programmableBorrower))),
        W
    );
    assertLt(externalVault.maxWithdraw(address(programmableBorrower)), W);

    vm.warp(cdoEpoch.epochEndDate() + 1);

    vm.expectRevert(StopEpochVaultLiquidityUnavailable.selector);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    assertTrue(cdoEpoch.isEpochRunning());
    assertEq(strategy.epochNumber(), strategy.lastWithdrawRequest(address(this)));
    assertEq(strategy.pendingWithdraws(), W);

    // The claimant still cannot settle the matured receipt.
    vm.expectRevert(NotAllowed.selector);
    cdoEpoch.claimWithdrawRequest();

    // Repeating the external-vault liquidity drain before every manager retry
    // keeps this state unchanged.
}
```

The failed stop consumes no receipt and leaves `pendingWithdraws`, `lastWithdrawRequest`, `epochNumber`, and `isEpochRunning` unchanged, so the same liquidity condition can be reapplied to subsequent settlement attempts. [5](#0-4) [12](#0-11)

### Citations

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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L239-267)
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
    }

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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L357-371)
```text
  /// @notice Emergency escape: redeem vault shares back to this contract.
  /// @dev Owner or manager. Pass 0 to redeem all shares.
  /// @param _shares number of vault shares to redeem (0 = redeem all)
  /// @return assets amount of underlying redeemed
  function emergencyExitVault(uint256 _shares) external nonReentrant returns (uint256 assets) {
    _checkOnlyOwnerOrManager();
    if (_shares == 0) {
      _shares = vault.balanceOf(address(this));
    }
    if (_shares == 0) revert InvalidAmount();
    assets = vault.redeem(_shares, address(this), address(this));
    if (epochAccountingActive) {
      epochWithdrawnFromVault += assets;
    }
    emit RedeemedFromVault(_shares, assets, address(this));
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L545-549)
```text
  /// @notice Convert the current vault share position into underlying terms.
  function _currentVaultAssets() internal view returns (uint256) {
    uint256 shares = vault.balanceOf(address(this));
    return shares == 0 ? 0 : vault.convertToAssets(shares);
  }
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L319-349)
```text
  function _claimFundedWithdrawRequest(address _user) internal returns (uint256 amount) {
    // User should wait at least an epoch before claiming the withdraw. Once the epoch is over user can withdraw 
    // at any time even if a new epoch started. 
    // So if epochNumber is the same as the last withdraw request then we revert. Epoch number is increased at stopEpoch
    // NOTE: If a user does not claim a withdraw request and instead requests another withdraw, he will have to wait
    // for another epoch to claim both requests.
    // NOTE 2: if borrower defaults, old withdraw requests can still be claimed
    if (IIdleCDOEpochVariant(idleCDO).epochEndDate() != 0 && (epochNumber <= lastWithdrawRequest[_user])) {
      revert NotAllowed();
    }
    // settle APR=0 requests once the related epoch has ended
    _settleApr0(_user);
    Apr0UserData storage _apr0User = apr0Users[_user];
    // Claim includes:
    // - settled APR0 principal from finalized epochs
    // - still-open APR0 principal: if pool-close mode was used (_interest == 1), IdleCDO sets
    //   epochEndDate = 0 and claims can be immediate, while _settleApr0 can still skip settlement
    //   for the current request epoch (reqEpoch >= epochNumber).
    // - settled APR0 interest
    uint256 normalAmount = withdrawsRequests[_user];
    uint256 apr0PrincipalAmount = _apr0User.settledPrincipal + _apr0User.principal;
    uint256 apr0InterestAmount = _apr0User.settledInterest;
    amount = normalAmount + apr0PrincipalAmount + apr0InterestAmount;
    // burn strategy tokens 1:1 with the principal only (normal amount already includes interest)
    _burn(_user, normalAmount + apr0PrincipalAmount);
    withdrawsRequests[_user] = 0;
    lastWithdrawRequest[_user] = 0;
    if (apr0PrincipalAmount != 0 || apr0InterestAmount != 0) {
      delete apr0Users[_user];
    }
    _transferFundedClaim(_user, amount);
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
