### Title
Unsolicited ERC4626 share deposits can repeatedly block `ProgrammableBorrower.setVault` - (File: `contracts/strategies/idle/ProgrammableBorrower.sol`)

### Summary

`setVault` requires the current ERC4626 vault's raw share balance for `ProgrammableBorrower` to be zero before switching vaults. Any unprivileged user can mint or transfer shares to `ProgrammableBorrower`, making that check fail and repeatedly griefing a vault migration for the cost of a dust deposit. [1](#0-0) 

### Finding Description

The programmable-borrower vault migration performs two separate operations: the operator must first redeem the old position through `emergencyExitVault`, then call `setVault` after the share balance reaches zero. [2](#0-1) 

`setVault` checks `vault.balanceOf(address(this)) != 0`, rather than tracking only shares acquired through `ProgrammableBorrower`'s own deposits. [3](#0-2) 

ERC4626 `deposit` explicitly accepts a separate `receiver`, so an attacker can deposit their own underlying and mint the resulting shares directly to `ProgrammableBorrower`. [4](#0-3) 

Because `emergencyExitVault` and `setVault` are separate calls, the attacker can front-run or back-run the exit transaction with another dust deposit, restoring a nonzero share balance before `setVault` executes. [1](#0-0) 

This issue applies while the facility is in the buffer/settled phase: `epochAccountingActive` must already be false, and the borrower ledger must be settled enough that the operator is attempting a vault switch. [5](#0-4) 

### Impact Explanation

An attacker can repeatedly prevent the owner or manager from changing the vault used for idle-fund deployment. Each griefing iteration costs only enough underlying to mint a nonzero share amount, while forcing the honest operator to execute another privileged `emergencyExitVault` transaction.

If the current ERC4626 vault restricts or delays redemptions, the donated shares can block migration until the vault permits them to be redeemed or transferred out through another mechanism. This is a temporary freezing of a critical operational path and can be repeated; it does not directly steal pool assets.

### Likelihood Explanation

The attacker needs no protocol role, borrower approval, KYC status, or tranche position. They only need to be able to interact with the already-configured ERC4626 vault as an ordinary depositor.

The attack depends on observing the operator's exit/migration attempt and submitting another dust deposit before `setVault`. If the vault is redeemable and the operator can atomically bundle `emergencyExitVault` and `setVault` through an external multicall or contract account, the practical window is reduced; `ProgrammableBorrower` itself does not provide that atomic migration path.

### Recommendation

Make vault migration atomic inside `setVault`. For example, before assigning the new vault, redeem or transfer the full current vault-share balance in the same transaction, then assign the new vault only after that operation succeeds.

Alternatively, add an authorized `migrateVault(newVault)` path that performs the old-vault exit and vault assignment as one state transition. If unsolicited shares must be excluded instead of redeemed, track internally owned shares at deposit/withdrawal boundaries rather than relying on the raw ERC20 balance.

### Proof of Concept

The following Foundry test uses the existing mainnet-fork setup in `ProgrammableBorrowerCreditVault.t.sol`, where `STEAKHOUSE_USDC` is the configured ERC4626 vault and `USDC` is the underlying. [6](#0-5) 

```solidity
function testSetVaultCanBeGriefedByShareDonation() external {
  IERC4626 oldVault = IERC4626(STEAKHOUSE_USDC);
  MockStopEpochLiquidityVault newVault =
    new MockStopEpochLiquidityVault(USDC);

  address attacker = makeAddr("attacker");
  uint256 donationAssets = oneScale;

  deal(USDC, attacker, donationAssets, true);

  vm.startPrank(attacker);
  underlying.approve(address(oldVault), donationAssets);
  uint256 donatedShares =
    oldVault.deposit(donationAssets, address(programmableBorrower));
  vm.stopPrank();

  assertGt(donatedShares, 0);
  assertEq(oldVault.balanceOf(address(programmableBorrower)), donatedShares);

  vm.prank(manager);
  vm.expectRevert(NotAllowed.selector);
  programmableBorrower.setVault(address(newVault));

  // The operator can clear this unsolicited position, but another dust deposit
  // can again front-run the subsequent setVault transaction.
  vm.prank(manager);
  programmableBorrower.emergencyExitVault(0);
  assertEq(oldVault.balanceOf(address(programmableBorrower)), 0);

  deal(USDC, attacker, donationAssets, true);
  vm.startPrank(attacker);
  underlying.approve(address(oldVault), donationAssets);
  oldVault.deposit(donationAssets, address(programmableBorrower));
  vm.stopPrank();

  vm.prank(manager);
  vm.expectRevert(NotAllowed.selector);
  programmableBorrower.setVault(address(newVault));
}
```

### Citations

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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L357-371)
```text
  /// @notice Emergency escape: redeem vault shares back to this contract.
  /// @dev Owner or manager. Pass 0 to redeem all shares.
  /// @param _shares number of vault shares to redeem (0 = redeem all)
  /// @return assets amount of underlying redeemed
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

**File:** contracts/interfaces/IERC4626.sol (L101-112)
```text
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

**File:** test/foundry/ProgrammableBorrowerCreditVault.t.sol (L89-96)
```text
  address internal constant TL_MULTISIG = address(0xFb3bD022D5DAcF95eE28a6B07825D4Ff9C5b3814);
  address internal constant USDC = 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48;
  address internal constant MORPHO_BLUE = 0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb;
  address internal constant STEAKHOUSE_USDC = 0xBEEF01735c132Ada46AA9aA4c54623cAA92A64CB;
  address internal constant GAUNTLET_USDC_PRIME = 0x8c106EEDAd96553e64287A5A6839c3Cc78afA3D0;
  address internal constant MORPHO_AAVE_USDC = 0xA5269A8e31B93Ff27B887B56720A25F844db0529;
  uint256 internal constant FORK_BLOCK = 19225935;
  uint256 internal constant GAUNTLET_FORK_BLOCK = 24850150;
```
