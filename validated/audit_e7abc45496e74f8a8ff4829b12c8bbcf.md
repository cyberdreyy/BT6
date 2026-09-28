### Title
Unvalidated ERC4626 share minting lets a vault depositor steal programmable-borrower capital - (File: `contracts/strategies/idle/ProgrammableBorrower.sol`)

### Summary

`ProgrammableBorrower.onStartEpoch` deposits the full idle underlying balance into the configured ERC4626 vault, but `_depositToVault` accepts however many shares the vault returns without a nonzero-share or minimum-share check. A permissionless depositor in that vault can inflate its assets-per-share before `startEpoch`, causing the pool deposit to mint zero or dust shares, then redeem their shares and extract the pool's capital. [1](#0-0) [2](#0-1) 

### Finding Description

The vulnerable sequence is:

1. During the buffer phase, the attacker opens or controls the initial position in the configured ERC4626 vault and directly donates underlying to inflate `totalAssets / totalSupply`.
2. The honest manager calls `IdleCDOEpochVariant.startEpoch`, which sends pool funds to the programmable borrower and invokes `onStartEpoch`.
3. `onStartEpoch` snapshots the pre-deposit balance and calls `_depositToVault` with the entire underlying balance. [1](#0-0) 
4. `_depositToVault` calls `vault.deposit(_assetAmount, address(this))` and only emits the returned share count; it never compares that count against `previewDeposit`, `convertToShares`, or a caller-supplied minimum. [2](#0-1) 
5. With an inflated share price, the pool receives zero or economically negligible shares while its assets remain in the vault. The attacker redeems their dominant share position and receives both their donation and the pool deposit.
6. Subsequent accounting relies on `vault.convertToAssets(vault.balanceOf(address(this)))`, so the missing shares translate directly into vault loss rather than backed pool assets. [3](#0-2) [4](#0-3) 

The only access check on the deposit path is `_checkOnlyIdleCDO` in `onStartEpoch`; there is no defense against manipulated external vault accounting at the moment the trusted manager call is sequenced. [5](#0-4) [6](#0-5) 

### Impact Explanation

For a pool deposit `A`, an attacker who owns the vault's initial `1` share and donates approximately `A` underlying can cause the deposit formula `floor(A * 1 / (donation + 1))` to mint zero shares where the vault permits zero-share deposits. The attacker then redeems the sole share for approximately `2A`, yielding a net profit of approximately `A`; the entire pool deposit is stolen.

For ERC4626 implementations that revert on zero-share mints, the attacker can choose the donation so the pool mints exactly one share. With a donation slightly above `A / 2`, the attacker captures approximately `A / 4` net from the deposit. Either outcome breaks the fair-mint and solvency invariants: the CDO retains strategy-token claims while the programmable borrower's actual vault backing is missing or sharply reduced.

The loss may remain hidden during ordinary epochs because interest accounting is separate from the CDO's minted strategy-token principal. When principal or pending withdrawals must be recalled, `onStopEpoch` determines that the vault position does not cover the shortfall, the subsequent `transferFrom` cannot be satisfied, and `IdleCDOEpochVariant` records a borrower default. [7](#0-6) [8](#0-7) [9](#0-8) 

### Likelihood Explanation

The attack requires the configured ERC4626 vault to derive share issuance from manipulable total assets and either to permit zero-share deposits or to have sufficiently low initial supply for economically meaningful rounding inflation. The attacker only needs to be an ordinary depositor/shareholder in that vault and can sequence the donation before the manager's `startEpoch`; they do not need the borrower, manager, owner, or CDO roles.

Likelihood is therefore deployment-dependent but realistic for a freshly configured, dedicated, or low-liquidity ERC4626 vault. Existing guards do not mitigate it: the vault address being owner-selected does not prevent later share-price manipulation by external depositors, and the contract explicitly treats `convertToAssets` as authoritative. [10](#0-9) [3](#0-2) 

### Recommendation

Add explicit slippage protection to every ERC4626 deposit:

```solidity
uint256 expectedShares = vault.convertToShares(_assetAmount);
uint256 shares = vault.deposit(_assetAmount, address(this));
if (shares == 0 || shares < expectedShares * MIN_SHARES_BPS / 10_000) {
    revert InvalidAmount();
}
```

Preferably make the minimum shares an owner-configurable parameter or require `shares >= previewDeposit(_assetAmount)` with an explicit tolerance. The contract should also verify that `vault.convertToAssets(shares)` is within an accepted tolerance of `_assetAmount`, not merely emit the returned share count. For critical start-epoch deposits, consider an owner/manager-supplied `minShares` derived from the observed state at transaction construction.

### Proof of Concept

The following Foundry fork test targets a deployed programmable-borrower configuration whose vault has no initial supply and where the programmable borrower has no existing vault position.

```solidity
// test/foundry/ProgrammableBorrowerShareInflation.t.sol
function testFork_shareInflationStealsStartEpochDeposit() public {
    vm.createSelectFork(vm.envString("FORK_RPC_URL"), vm.envUint("FORK_BLOCK"));

    IdleCDOEpochVariant cdo = IdleCDOEpochVariant(vm.envAddress("CDO"));
    IdleCreditVault strategy = IdleCreditVault(cdo.strategy());
    ProgrammableBorrower pb = ProgrammableBorrower(strategy.borrower());
    IERC4626 vault = pb.vault();
    IERC20Detailed asset = IERC20Detailed(address(vault.asset()));

    // Eligible fixture: fresh vault and no retained programmable-borrower position.
    require(vault.totalSupply() == 0, "vault already initialized");
    require(vault.balanceOf(address(pb)) == 0, "PB already has vault shares");

    uint256 poolDeposit =
        asset.balanceOf(address(cdo)) +
        asset.balanceOf(address(strategy)) +
        asset.balanceOf(address(pb));
    require(poolDeposit > 0, "no start-epoch deposit");

    address attacker = makeAddr("attacker");
    uint256 seed = 1;
    uint256 donation = poolDeposit;

    deal(address(asset), attacker, seed + donation);
    uint256 attackerBefore = asset.balanceOf(attacker);

    // First depositor creates one share, then inflates assets per share.
    vm.startPrank(attacker);
    asset.approve(address(vault), seed);
    vault.deposit(seed, attacker);
    asset.transfer(address(vault), donation);
    vm.stopPrank();

    // Honest manager starts the epoch; PB deposits all idle assets.
    vm.prank(strategy.manager());
    cdo.startEpoch();

    // Zero-share ERC4626 implementations leave PB with no vault claim.
    assertEq(vault.balanceOf(address(pb)), 0);

    // The attacker owns all vault shares and recovers donation + PB deposit.
    vm.prank(attacker);
    vault.redeem(vault.balanceOf(attacker), attacker, attacker);

    assertEq(asset.balanceOf(attacker), attackerBefore + poolDeposit);
}
```

On a vault that rejects zero-share deposits, reduce the donation so the deposit mints exactly one share and assert `vault.balanceOf(address(pb)) == 1`; the attacker then redeems half the vault supply and still extracts a material fraction of the start-epoch deposit.

### Citations

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L153-168)
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
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L201-205)
```text
  function onStartEpoch(uint256 _pendingWithdraws) external nonReentrant {
    _checkOnlyIdleCDO();
    _accrueBorrowerInterest();
    // Reserve the amount IdleCDO expects to pull back at stopEpoch before the real borrower can draw again.
    epochPendingWithdraws = _pendingWithdraws;
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L214-219)
```text
    uint256 startAssets = underlyingToken.balanceOf(address(this)) + currentVaultAssets;
    // Any idle balance left on the contract between epochs is parked immediately into the vault.
    _depositToVault(underlyingToken.balanceOf(address(this)), 0);
    // Reset the epoch accounting baseline from the exact pre-start total assets so the new epoch
    // does not treat the just-deposited idle balance as fresh vault profit or loss.
    epochStartVaultAssets = startAssets;
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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L545-548)
```text
  /// @notice Convert the current vault share position into underlying terms.
  function _currentVaultAssets() internal view returns (uint256) {
    uint256 shares = vault.balanceOf(address(this));
    return shares == 0 ? 0 : vault.convertToAssets(shares);
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L581-584)
```text
  /// @notice Revert unless the caller is the configured IdleCDO.
  function _checkOnlyIdleCDO() internal view {
    if (msg.sender != idleCDO) revert NotAllowed();
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

**File:** contracts/IdleCDOEpochVariant.sol (L577-598)
```text
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
```
