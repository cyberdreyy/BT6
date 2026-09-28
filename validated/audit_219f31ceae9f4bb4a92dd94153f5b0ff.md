### Title
ERC4626 liquidity griefing can indefinitely delay programmable-borrower epoch settlement - (File: contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary
An unprivileged user of the ERC4626 vault used by `ProgrammableBorrower` can leave that vault economically solvent but temporarily illiquid, causing every `IdleCDOEpochVariant.stopEpoch` call to revert before epoch state is cleared. Because `ProgrammableBorrower.onStopEpoch` propagates an otherwise-covered `vault.withdraw` failure instead of returning `false`, the CDO remains in a running epoch and pending withdrawal proceeds stay unavailable until external vault liquidity returns. [1](#0-0) [2](#0-1) 

### Finding Description
During `stopEpoch`, `IdleCDOEpochVariant` calls `IProgrammableBorrower.onStopEpoch` before attempting to pull the required underlying. The CDO handles a `false` return as a borrower default, but it does not catch a revert from the hook. [2](#0-1) 

`ProgrammableBorrower.onStopEpoch` checks whether the borrower’s vault shares cover the cash shortfall using `_currentVaultAssets()`. If they do, it calls `vault.withdraw(shortfall, ...)`; any ERC4626 withdrawal failure is caught locally and converted into `StopEpochVaultLiquidityUnavailable`, which then propagates to `stopEpoch`. [1](#0-0) 

An ERC4626 depositor can consume the vault’s currently withdrawable liquidity while the programmable borrower’s shares still represent enough total assets to cover the required withdrawal. This is particularly relevant for MetaMorpho-style vaults where share value can include assets currently lent out while immediately available cash is insufficient. The existing regression test demonstrates the resulting state transition: `stopEpoch` reverts, `defaulted` stays false, `isEpochRunning` remains true, and the receipt amount remains pending. [3](#0-2) 

The privileged escape does not remove the dependency: `emergencyExitVault` still calls the same vault’s `redeem` function and will also fail while liquidity is unavailable. [4](#0-3) 

### Impact Explanation
This is temporary freezing of user funds, bounded by how long the attacker or ordinary market utilization keeps the ERC4626 vault illiquid.

For a normal stop, the blocked amount is `_amountToPullFromBorrower + pendingWithdraws`, including all matured withdrawal receipts that must be funded through `collectWithdrawFunds`. For a close-pool stop, the required amount includes the CDO’s full strategy-token balance as well, so effectively the entire active pool NAV plus pending receipts can remain locked. [5](#0-4) 

Because the revert occurs before `isEpochRunning` is cleared and before pending withdrawals are funded, users cannot claim matured receipts and the manager cannot complete settlement by retrying while the vault remains illiquid. [6](#0-5) 

### Likelihood Explanation
The attacker only needs to be an ordinary participant in the configured ERC4626 vault and cause its currently withdrawable liquidity to fall below the epoch-settlement shortfall. They do not need to control the owner, manager, guardian, borrower, Keyring, fee receiver, or queue.

The required condition is realistic for lending vaults: total share value can exceed immediately withdrawable cash because part of the vault is deployed into illiquid markets. The attacker can repeatedly consume restored liquidity, extending the freeze, although their cost depends on vault share acquisition and market conditions.

### Recommendation
Add a bounded settlement path instead of allowing the external vault withdrawal failure to block `stopEpoch` forever. For example, record the first liquidity failure and, after a protocol-defined grace period, perform a best-effort withdrawal and route the uncovered deficit through the existing default or loss-adjusted withdrawal accounting.

At minimum, `onStopEpoch` should distinguish economic insolvency from temporary withdrawal failure using `maxWithdraw`, but it should not rely on `maxWithdraw` alone because it can disagree with an actual `withdraw` call. The epoch should expose an explicit deadline after which persistent illiquidity becomes a default or realized loss rather than an indefinitely retryable revert.

### Proof of Concept
The following Foundry test extends the existing programmable-borrower harness. `LiquidityLimitingVault.redeem` represents a real ERC4626 depositor draining the vault’s available liquidity; on a mainnet fork, the same state can be produced by a large vault shareholder redeeming or by underlying-market borrowing until available liquidity is below the stop-epoch shortfall.

```solidity
function testERC4626LiquidityGriefingBlocksStopEpoch() external {
    _setUpProgrammableBorrowerCreditVault(FORK_BLOCK, STEAKHOUSE_USDC);

    uint256 depositAmount = 10_000 * oneScale;

    vm.prank(owner);
    cdoEpoch.setIsInterestMinted(true);

    idleCDO.depositAA(depositAmount);
    uint256 receipt = cdoEpoch.requestWithdraw(
        aaTranche.balanceOf(address(this)) / 2,
        address(aaTranche)
    );

    _startEpochAndCheckPrices(0);

    uint256 pending = strategy.pendingWithdraws();
    assertEq(pending, receipt);

    uint256 covered =
        morphoVault.convertToAssets(morphoVault.balanceOf(address(programmableBorrower)));
    assertGe(covered, pending);

    // Attacker is an ordinary ERC4626 vault user. They redeem enough vault
    // liquidity that the programmable borrower's shares still cover `pending`,
    // but the requested cash withdrawal is no longer executable.
    address attacker = makeAddr("vault-user");
    uint256 attackerShares = IERC4626(address(morphoVault)).convertToShares(pending);
    deal(address(morphoVault), attacker, attackerShares, true);

    vm.startPrank(attacker);
    uint256 withdrawable = morphoVault.maxWithdraw(attacker);
    if (withdrawable != 0) {
        morphoVault.withdraw(withdrawable, attacker, attacker);
    }
    vm.stopPrank();

    vm.warp(cdoEpoch.epochEndDate() + 1);

    // This succeeds only on a fork state where the shareholder drain has left
    // PB covered by convertToAssets but unable to execute `vault.withdraw`.
    vm.expectRevert(StopEpochVaultLiquidityUnavailable.selector);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    assertTrue(cdoEpoch.isEpochRunning());
    assertFalse(cdoEpoch.defaulted());
    assertEq(strategy.pendingWithdraws(), pending);
}
```

The deterministic version is already encoded by `MockStopEpochLiquidityVault`: after `setWithdrawLimit(pendingWithdraws - 1)`, the manager’s `stopEpoch` call reverts and the epoch stays running while the pending receipt remains unpaid. [7](#0-6) [3](#0-2)

### Citations

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

**File:** contracts/IdleCDOEpochVariant.sol (L364-376)
```text
    uint256 _pendingWithdraws = _strategy.pendingWithdraws();

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

**File:** contracts/IdleCDOEpochVariant.sol (L395-410)
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
        _strategy.collectWithdrawFunds(_pendingWithdraws);
```

**File:** test/foundry/ProgrammableBorrowerCreditVault.t.sol (L24-64)
```text
contract MockStopEpochLiquidityVault is ERC20 {
  IERC20Detailed public immutable assetToken;
  uint256 public withdrawLimit = type(uint256).max;

  constructor(address _asset) ERC20("Stop Epoch Liquidity Vault", "SELV") {
    assetToken = IERC20Detailed(_asset);
  }

  function asset() external view returns (address) {
    return address(assetToken);
  }

  function convertToAssets(uint256 shares) public view returns (uint256) {
    uint256 supply = totalSupply();
    if (supply == 0) return shares;
    return shares * assetToken.balanceOf(address(this)) / supply;
  }

  function maxWithdraw(address owner) external view returns (uint256) {
    uint256 assets = convertToAssets(balanceOf(owner));
    return assets < withdrawLimit ? assets : withdrawLimit;
  }

  function setWithdrawLimit(uint256 assets) external {
    withdrawLimit = assets;
  }

  function deposit(uint256 assets, address receiver) external returns (uint256 shares) {
    uint256 assetsBefore = assetToken.balanceOf(address(this));
    uint256 supply = totalSupply();
    shares = supply == 0 || assetsBefore == 0 ? assets : assets * supply / assetsBefore;
    assetToken.transferFrom(msg.sender, address(this), assets);
    _mint(receiver, shares);
  }

  function withdraw(uint256 assets, address receiver, address owner) external returns (uint256 shares) {
    require(assets <= withdrawLimit, "insufficient-liquidity");
    shares = _toSharesRoundUp(assets);
    if (msg.sender != owner) _spendAllowance(owner, msg.sender, shares);
    _burn(owner, shares);
    assetToken.transfer(receiver, assets);
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
