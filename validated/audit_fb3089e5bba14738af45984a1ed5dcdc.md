### Title
External ERC4626 deposit failure can indefinitely delay epoch start - (File: `contracts/strategies/idle/ProgrammableBorrower.sol`)

### Summary

`ProgrammableBorrower.onStartEpoch` unconditionally deposits the borrower adapter's full idle balance into the configured ERC4626 vault. If that vault is paused, capped, or otherwise rejects deposits, `vault.deposit` reverts and the entire `IdleCDOEpochVariant.startEpoch` transaction is rolled back. An unprivileged user of the ERC4626 vault can force this condition by consuming the remaining deposit capacity. While the condition persists, the credit vault cannot transition from buffer to running, the borrower cannot draw funds, and withdrawal requests submitted for the next settlement remain frozen.

### Finding Description

`IdleCDOEpochVariant.startEpoch` transfers the pool's available underlying to the configured borrower and then invokes the programmable-borrower start hook at `contracts/IdleCDOEpochVariant.sol:294-297`. [1](#0-0) 

`onStartEpoch` calculates the epoch baseline, then calls `_depositToVault` for the entire on-hand balance at `contracts/strategies/idle/ProgrammableBorrower.sol:214-216`. [2](#0-1) 

`_depositToVault` unconditionally calls `vault.deposit(_assetAmount, address(this))` at `contracts/strategies/idle/ProgrammableBorrower.sol:378-384`. [3](#0-2) 

There is no `try/catch`, `maxDeposit` check, or fallback path that permits the epoch to start while retaining undepositable cash. Because the hook reverts after the CDO has logically transferred funds to the borrower, the whole external transaction reverts and `isEpochRunning`, pause flags, and borrower accounting remain unchanged.

The same contract already recognizes that external-vault operations can fail transiently: `onStopEpoch` wraps `vault.withdraw` and deliberately preserves epoch state so the stop can be retried later at `contracts/strategies/idle/ProgrammableBorrower.sol:239-253`. [4](#0-3)  No equivalent handling exists for the deposit side.

### Impact Explanation

This is a temporary freezing of funds and protocol operation, not a direct theft.

Let `D` be the idle underlying that `startEpoch` attempts to deposit, `P` the aggregate value of withdrawal requests queued for the next settlement, and `T` the duration for which the attacker keeps ERC4626 deposits unavailable. During `T`, `startEpoch` cannot complete and `epochAccountingActive` remains false, so borrower access through `_borrow` remains disabled by its `epochAccountingActive` check at `contracts/strategies/idle/ProgrammableBorrower.sol:435-445`. [5](#0-4) 

Withdrawal requests made during the buffer remain pending until a subsequent epoch is started and stopped, extending their normal lockup by at least `T`. The frozen amount is `P`, while undeployed capital `D` loses approximately `D * externalVaultAPR * T / 365 days` of expected yield compared with successful deployment.

An attacker can maintain the condition by consuming newly available ERC4626 deposit capacity. For a capped vault, the required attack capital is the remaining `maxDeposit`; the assets remain withdrawable under normal vault operation, so the attack does not require burning the full amount.

### Likelihood Explanation

The affected path is reachable whenever `isProgrammableBorrower` is enabled and the configured ERC4626 vault can reject deposits. ERC4626 permits `deposit` to revert for paused markets, deposit caps, controller limits, or insufficient downstream liquidity.

An ordinary vault user can exploit supply-cap mechanics without any privileged role in the credit vault. The attacker deposits the remaining capacity immediately before an honest manager calls `startEpoch`; the manager's transaction then reverts inside `vault.deposit`. This can be repeated or maintained until the attacker chooses to withdraw.

The impact is constrained to temporary freezing and missed yield because the transaction reverts atomically rather than leaving the CDO in a partially started state.

### Recommendation

Make vault deployment at epoch start best-effort rather than mandatory:

1. In `onStartEpoch`, query `vault.maxDeposit(address(this))`.
2. Deposit at most `min(onHand, maxDeposit)`.
3. Wrap `vault.deposit` in `try/catch`.
4. Keep any undeposited balance as cash in `ProgrammableBorrower` and still activate epoch accounting.
5. Preserve the existing pre-deposit `startAssets = cash + currentVaultAssets` baseline so retained cash is not misclassified as vault PnL.

The deposit retry can be performed later through a dedicated owner/manager function or automatically on future repayments once capacity returns. A failed deposit should not block the entire epoch transition when retaining the assets as cash preserves solvency and accounting.

### Proof of Concept

The following Foundry test can be added to `test/foundry/ProgrammableBorrowerCreditVault.t.sol`. It runs on the suite's mainnet fork and uses an ERC4626 vault with a public deposit cap. The attacker only calls the external vault's public `deposit`.

```solidity
contract PublicDepositCapVault is ERC20 {
    IERC20Detailed public immutable assetToken;
    uint256 public immutable depositCap;

    constructor(address asset_, uint256 cap_)
        ERC20("Capped Vault", "CAP")
    {
        assetToken = IERC20Detailed(asset_);
        depositCap = cap_;
    }

    function asset() external view returns (address) {
        return address(assetToken);
    }

    function maxDeposit(address) external view returns (uint256) {
        uint256 assets = assetToken.balanceOf(address(this));
        return assets >= depositCap ? 0 : depositCap - assets;
    }

    function convertToAssets(uint256 shares) public view returns (uint256) {
        uint256 supply = totalSupply();
        return supply == 0 ? shares : shares * assetToken.balanceOf(address(this)) / supply;
    }

    function deposit(uint256 assets, address receiver) external returns (uint256) {
        require(assets <= this.maxDeposit(receiver), "cap reached");
        uint256 assetsBefore = assetToken.balanceOf(address(this));
        assetToken.transferFrom(msg.sender, address(this), assets);
        uint256 shares = assetsBefore == 0 ? assets : assets * totalSupply() / assetsBefore;
        _mint(receiver, shares);
        return shares;
    }

    function withdraw(uint256 assets, address receiver, address owner)
        external
        returns (uint256)
    {
        uint256 shares = assets * totalSupply() / assetToken.balanceOf(address(this));
        if (msg.sender != owner) _spendAllowance(owner, msg.sender, shares);
        _burn(owner, shares);
        assetToken.transfer(receiver, assets);
        return shares;
    }

    function balanceOf(address account)
        public
        view
        override(ERC20, IERC20Detailed)
        returns (uint256)
    {
        return ERC20.balanceOf(account);
    }
}
```

```solidity
function testVaultDepositCapBlocksEpochStart() external {
    uint256 poolDeposit = 100_000 * oneScale;
    uint256 vaultCap = 1_000_000 * oneScale;

    PublicDepositCapVault cappedVault =
        new PublicDepositCapVault(USDC, vaultCap);

    // The configured vault has no position yet, so the honest manager may switch it.
    vm.prank(manager);
    programmableBorrower.setVault(address(cappedVault));

    // A KYC-passing lender deposits into the credit vault during the buffer period.
    idleCDO.depositAA(poolDeposit);

    // Unprivileged external-vault user consumes all deposit capacity.
    address vaultUser = makeAddr("vault-user");
    deal(USDC, vaultUser, vaultCap, true);
    vm.startPrank(vaultUser);
    IERC20Detailed(USDC).approve(address(cappedVault), vaultCap);
    cappedVault.deposit(vaultCap, vaultUser);
    vm.stopPrank();

    assertEq(cappedVault.maxDeposit(address(programmableBorrower)), 0);

    // Honest manager cannot start the epoch while external deposits are unavailable.
    vm.prank(manager);
    vm.expectRevert("cap reached");
    cdoEpoch.startEpoch();

    // The failure is atomic: the epoch did not start and borrowing remains disabled.
    assertFalse(cdoEpoch.isEpochRunning());
    assertFalse(programmableBorrower.epochAccountingActive());

    vm.prank(revolvingBorrower);
    vm.expectRevert(NotAllowed.selector);
    programmableBorrower.borrow(1);

    // Once the attacker withdraws vault capacity, the same manager call succeeds.
    vm.prank(vaultUser);
    cappedVault.withdraw(vaultCap, vaultUser, vaultUser);

    vm.prank(manager);
    cdoEpoch.startEpoch();

    assertTrue(cdoEpoch.isEpochRunning());
    assertTrue(programmableBorrower.epochAccountingActive());
}
```

This demonstrates that an attacker can repeatedly front-run epoch starts and keep the credit vault in the buffer phase for as long as the external deposit limitation is maintained.

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L294-297)
```text
    uint256 _toBorrower = totUnderlyings - pendingInstant;
    try this.sendFundsToBorrower(_toBorrower) {
      // funds transferred correctly
      _startEpochProgrammableBorrower(_pendingWithdraws);
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L214-216)
```text
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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L435-445)
```text
  function _borrow(uint256 assets) internal returns (uint256 withdrawnShares) {
    if (!epochAccountingActive) revert NotAllowed();
    // `0` is treated as "draw the full currently borrowable amount" after reserving epoch-end obligations.
    uint256 borrowable = availableToBorrow();
    if (assets == 0) {
      if (borrowable == 0) revert InvalidAmount();
      assets = borrowable;
    }

    // Borrows are capped by the live "reserved vs free" view so epoch-end obligations always stay covered.
    if (assets > borrowable) revert InsufficientBorrowable();
```
