### Title
Unbounded ERC4626 deposit allows a first-depositor to steal pooled epoch principal - (File: `contracts/strategies/idle/ProgrammableBorrower.sol`)

### Summary
`ProgrammableBorrower._depositToVault` performs an exact-asset ERC4626 `deposit` without checking that a minimum number of shares or equivalent asset value was received. [1](#0-0)  When an epoch starts, `onStartEpoch` deposits the borrower contract’s entire idle balance through this unbounded path. [2](#0-1)  An unprivileged user of the configured ERC4626 vault can inflate that vault’s share price before `startEpoch`, cause the pool’s deposit to mint zero or dust shares, and redeem the donated assets plus the pool deposit. [3](#0-2) 

### Finding Description
The external issue’s “exact input with no minimum output” bug maps to `ProgrammableBorrower._depositToVault`, which calls `vault.deposit(_assetAmount, address(this))` and accepts whatever share amount the external vault returns. [1](#0-0)  The function emits the returned `shares`, but never requires `shares > 0`, compares the minted shares with `previewDeposit`, or verifies that the vault position increased by approximately `_assetAmount`. [4](#0-3) 

`onStartEpoch` snapshots the combined cash and vault-asset baseline, deposits the entire on-hand underlying balance, and then activates epoch accounting. [5](#0-4)  The accounting later derives vault PnL from `convertToAssets(vault.balanceOf(address(this)))`, so minted-share loss immediately appears as missing vault backing. [6](#0-5) 

The vulnerable sequence is:

1. The attacker deposits a minimal amount, such as 1 wei, into a susceptible, initially empty configured ERC4626 vault and receives the initial share supply.
2. The attacker donates at least `P - 1` underlying directly to that vault, making one share worth approximately `P`.
3. An LP deposits `P` into the credit vault during the buffer phase.
4. The honest manager calls `IdleCDOEpochVariant.startEpoch`, which transfers the surplus underlying to the programmable borrower and invokes `onStartEpoch`. [3](#0-2) 
5. `onStartEpoch` deposits `P` through `_depositToVault`; with share supply `1` and vault assets `P`, a floor-rounded ERC4626 mints `P * 1 / P = 0` shares, while the assets remain inside the vault. [2](#0-1) 
6. The attacker redeems the single outstanding share for the vault’s entire balance, recovering the donation and stealing the pool deposit.

The same unbounded deposit path is reached for active-epoch repayments through `_repay`, but the first epoch deposit is the cleanest attack because the entire idle pool balance is committed atomically by an honest manager. [7](#0-6) 

### Impact Explanation
For a deposit of `P`, a donation of at least `P - 1` can make the borrower receive zero shares under a susceptible floor-rounded ERC4626 implementation, and the attacker can redeem the initial share for approximately `2P`, yielding a profit of approximately `P`. [1](#0-0)  The programmable borrower records `epochStartVaultAssets = P` while holding zero vault shares, so `_vaultNetInterest` reports a loss of `P` and `totalInterestDueNow` becomes zero. [6](#0-5) 

This is a direct theft of LP principal rather than merely poor execution pricing: the transferred assets remain in the external vault, the borrower owns no corresponding claim, and the attacker can withdraw them through the attacker-owned initial share. [8](#0-7) 

### Likelihood Explanation
Likelihood is conditional on the configured ERC4626 vault being susceptible to share-price inflation or another economically adverse share mint. [9](#0-8)  `setVault` is trusted and cannot migrate an existing position, but it does not require or establish a private seed position, a minimum share mint, or a post-deposit value invariant. [10](#0-9)  Once such a vault is configured, the attack needs only ordinary, unprivileged vault transactions sequenced before the manager’s `startEpoch`; no privileged Idle role needs to misbehave. [11](#0-10) 

Existing skim checks do not prevent the loss because they isolate raw donations sent to `IdleCDOEpochVariant`, not donations made to the external ERC4626 vault before `ProgrammableBorrower` deposits. [12](#0-11)  The borrower’s own accounting also does not detect the theft until after it treats the missing vault shares as a vault loss. [6](#0-5) 

### Recommendation
Require a minimum acceptable share output or equivalent asset value for every vault deposit. [1](#0-0)  For example, snapshot `vault.convertToAssets(vault.balanceOf(address(this)))`, call `deposit`, then require that `convertToAssets` of the newly received shares is at least `_assetAmount` minus a tightly bounded rounding tolerance. [13](#0-12)  Reverting before `epochAccountingActive` and `epochStartVaultAssets` are updated lets an honest manager retry after the manipulation is removed. [14](#0-13)  When configuring a new vault, prefer an implementation with inflation-resistant virtual shares/assets or require a private initial deposit before connecting it. [10](#0-9) 

### Proof of Concept
The following Foundry test uses the repository’s programmable-borrower setup pattern and a minimal susceptible ERC4626 to reproduce the missing minimum-output check. [15](#0-14) 

```solidity
function testEpochDepositShareInflationStealsPrincipal() external {
    uint256 amount = 10_000 * oneScale;
    address attacker = makeAddr("vaultUser");

    // Use a newly deployed susceptible ERC4626 as the configured vault.
    MockStopEpochLiquidityVault vulnerableVault =
        new MockStopEpochLiquidityVault(USDC);

    vm.prank(manager);
    programmableBorrower.setVault(address(vulnerableVault));

    vm.prank(owner);
    cdoEpoch.setIsInterestMinted(true);

    // Attacker owns the only vault share and inflates it to >= amount.
    deal(USDC, attacker, amount + 1, true);
    vm.startPrank(attacker);
    underlying.approve(address(vulnerableVault), 1);
    vulnerableVault.deposit(1, attacker);
    underlying.transfer(address(vulnerableVault), amount);
    vm.stopPrank();

    // LP funds the pool and the honest manager starts the epoch.
    idleCDO.depositAA(amount);
    vm.prank(manager);
    cdoEpoch.startEpoch();

    // P * 1 / (P + 1) rounds to zero shares.
    assertEq(
        vulnerableVault.balanceOf(address(programmableBorrower)),
        0,
        "borrower received zero vault shares"
    );

    // The attacker's only share redeems its deposit, donation, and pool deposit.
    uint256 attackerBefore = underlying.balanceOf(attacker);
    vm.prank(attacker);
    vulnerableVault.redeem(1, attacker, attacker);

    assertApproxEqAbs(
        underlying.balanceOf(attacker) - attackerBefore,
        2 * amount + 1,
        2,
        "attacker did not steal pool deposit"
    );
    assertEq(
        programmableBorrower.totalUnderlying(),
        0,
        "pool principal remains backed"
    );
}
```

For `P = 10,000 USDC`, the attacker spends approximately `P + 1`, causes the pool to mint zero shares, and redeems approximately `2P + 1`, producing an approximately `P` USDC profit. [1](#0-0)  The same assertion pattern can be run on a mainnet fork by substituting an actually configured susceptible ERC4626 vault for the test vault. [16](#0-15)

### Citations

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L126-134)
```text
    vault = IERC4626(_vault);
    idleCDO = _idleCDO;
    manager = _manager;
    borrower = _borrower;
    borrowerApr = _borrowerApr;

    // Set approvals for vault and IdleCDO
    _allowUnlimitedSpend(_underlyingToken, _vault);
    _allowUnlimitedSpend(_underlyingToken, _idleCDO);
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L153-169)
```text
  /// @notice Set the vault used to deploy idle funds.
  /// @dev Owner or manager. This does not migrate an existing position. The operator must first
  /// withdraw from the old vault and wait until epoch accounting is inactive, otherwise assets can
  /// remain stranded there and the live accounting views will stop including them after the switch.
  /// @param _vault new ERC4626 vault address
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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L212-222)
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
    epochWithdrawnFromVault = 0;
    epochAccountingActive = true;
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L337-348)
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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L523-528)
```text
    emit Repaid(interestPaid + principalPaid, interestPaid, principalPaid);
    if (epochAccountingActive) {
      // Current-epoch borrower interest must remain visible as profit at stopEpoch, while principal,
      // previously-fronted debt, and any excess repayment should only extend the epoch principal baseline.
      _depositToVault(totalRepaidAssets, totalRepaidAssets - currentEpochInterestPaid);
    } else if (currentEpochInterestPaid != 0) {
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L545-549)
```text
  /// @notice Convert the current vault share position into underlying terms.
  function _currentVaultAssets() internal view returns (uint256) {
    uint256 shares = vault.balanceOf(address(this));
    return shares == 0 ? 0 : vault.convertToAssets(shares);
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L233-243)
```text
  function startEpoch() external {
    _checkOnlyOwnerOrManager();

    // Check that buffer period passed (and epoch is not running as epochEndDate is set)
    // and that the pool is not closed (ie epochDuration == 0)
    uint256 _epochDuration = epochDuration; 
    _checkNotAllowed(defaulted || block.timestamp < (epochEndDate + bufferPeriod) || _epochDuration == 0);
    _checkProgrammableBorrowerMode();
    // Remove raw donated underlyings before calculating epoch interest or borrower transfer amounts.
    _skimDonatedAssets();

```

**File:** contracts/IdleCDOEpochVariant.sol (L294-303)
```text
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

**File:** contracts/interfaces/IERC4626.sol (L85-112)
```text
     * @dev Allows an on-chain or off-chain user to simulate the effects of their deposit at the current block, given
     * current on-chain conditions.
     *
     * - MUST return as close to and no more than the exact amount of Vault shares that would be minted in a deposit
     *   call in the same transaction. I.e. deposit should return the same or more shares as previewDeposit if called
     *   in the same transaction.
     * - MUST NOT account for deposit limits like those returned from maxDeposit and should always act as though the
     *   deposit would be accepted, regardless if the user has enough tokens approved, etc.
     * - MUST be inclusive of deposit fees. Integrators should be aware of the existence of deposit fees.
     * - MUST NOT revert.
     *
     * NOTE: any unfavorable discrepancy between convertToShares and previewDeposit SHOULD be considered slippage in
     * share price or some other type of condition, meaning the depositor will lose assets by depositing.
     */
    function previewDeposit(uint256 assets) external view returns (uint256 shares);

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
    function deposit(uint256 assets, address receiver) external returns (uint256 shares);
```

**File:** test/foundry/ProgrammableBorrowerCreditVault.t.sol (L120-183)
```text
  function setUp() public {
    _setUpProgrammableBorrowerCreditVault(FORK_BLOCK, STEAKHOUSE_USDC);
  }

  function _setUpProgrammableBorrowerCreditVault(uint256 forkBlock, address vaultAddress) internal {
    vm.createSelectFork("mainnet", forkBlock);

    strategy = new IdleCreditVault();
    stdstore.target(address(strategy)).sig(strategy.token.selector).checked_write(address(0));
    strategy.initialize(USDC, owner, manager, placeholderBorrower, BORROWER_NAME, initialProvidedApr);

    cdoEpoch = new IdleCDOEpochVariant();
    stdstore.target(address(cdoEpoch)).sig(cdoEpoch.token.selector).checked_write(address(0));
    cdoEpoch.initialize(0, USDC, address(this), owner, rebalancer, address(strategy), 100000);
    idleCDO = IdleCDO(address(cdoEpoch));

    underlying = IERC20Detailed(USDC);
    aaTranche = IdleCDOTranche(idleCDO.AATranche());
    bbTranche = IdleCDOTranche(idleCDO.BBTranche());
    oneScale = 10 ** underlying.decimals();

    vm.prank(owner);
    strategy.setWhitelistedCDO(address(cdoEpoch));
    vm.prank(owner);
    strategy.setMaxApr(0);

    vm.startPrank(owner);
    cdoEpoch.setIsAYSActive(false);
    cdoEpoch.setFeeParams(TL_MULTISIG, 0, 100000, 0);
    cdoEpoch.setInstantWithdrawParams(3 days, 1.5e18, false);
    cdoEpoch.setEpochParams(36.5 days, 5 days);
    cdoEpoch.setKeyringParams(address(0), 0);
    vm.stopPrank();

    deal(USDC, address(this), 1_000_000 * oneScale, true);
    underlying.approve(address(cdoEpoch), type(uint256).max);

    ProgrammableBorrower programmableBorrowerImplementation = new ProgrammableBorrower();
    programmableBorrower = ProgrammableBorrower(address(new TransparentUpgradeableProxy(
      address(programmableBorrowerImplementation),
      makeAddr("programmableBorrowerProxyAdmin"),
      abi.encodeWithSelector(
        ProgrammableBorrower.initialize.selector,
        vaultAddress,
        address(cdoEpoch),
        address(this),
        manager,
        revolvingBorrower,
        365e18
      )
    )));
    morphoVault = IMMVault(vaultAddress);

    vm.prank(owner);
    strategy.setBorrower(address(programmableBorrower));

    vm.prank(owner);
    cdoEpoch.setIsProgrammableBorrower(true);

    vm.prank(manager);
    strategy.setAprs(0, 0);

    vm.prank(revolvingBorrower);
    underlying.approve(address(programmableBorrower), type(uint256).max);
```
