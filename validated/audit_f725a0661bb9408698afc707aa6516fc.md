### Title
Changing the whitelisted CDO strands funded withdrawal receipts - ([File: `contracts/strategies/idle/IdleCreditVault.sol`])

### Summary
`IdleCreditVault.setWhitelistedCDO` lets the owner replace the only contract allowed to move or claim vault funds at any time, without checking for active positions or pending receipts. [1](#0-0)  If the CDO pointer is changed while users have tranche positions or funded withdrawal receipts, every user-facing vault operation routed through the old CDO reverts at `_onlyIdleCDO`, leaving the funds locked until the old CDO is restored or a compatible migration is performed. [2](#0-1) 

### Finding Description
`IdleCreditVault` mints withdrawal receipts to users and later pays them through `claimWithdrawRequest`, but that function is only callable by the currently configured `idleCDO`. [3](#0-2)  The CDO exposes this as `claimWithdrawRequest()`, forwarding the user's address to the strategy. [4](#0-3) 

The same authorization gate protects normal withdrawal requests, instant withdrawal claims, funding collection, strategy-token burns, and deposits. [5](#0-4) [6](#0-5)  The CDO itself stores the strategy during initialization, so the two contracts form a fixed accounting pair from the CDO's perspective. [7](#0-6) 

Unlike `ProgrammableBorrower.setVault`, which explicitly prevents replacement while an epoch or old vault position exists, `setWhitelistedCDO` has no epoch, balance, pending-request, or migration checks. [8](#0-7) 

### Impact Explanation
A routine owner migration to a replacement CDO can temporarily freeze all user value represented by the strategy.

For example, in the buffer phase:

1. A KYC-passing lender deposits through `depositAA`.
2. The lender calls `requestWithdraw`, burning tranche exposure and receiving a strategy-token receipt.
3. The epoch is stopped normally, causing the borrower-funded withdrawal amount to be held by `IdleCreditVault`.
4. The owner calls `setWhitelistedCDO(replacementCDO)`.
5. The lender calls the old CDO's `claimWithdrawRequest`, but the strategy sees `msg.sender == oldCDO != idleCDO` and reverts.
6. The receipt remains burned into the user's strategy-token balance, while the funded underlying stays in the vault.

This violates the one-receipt-one-funded-payout invariant: the user's receipt is valid and funded, but the sole authorized withdrawal path has been repointed. The affected amount is the full funded receipt amount, plus any active deposits whose later epoch lifecycle depends on the old CDO.

### Likelihood Explanation
The issue requires an owner or governance migration to change the strategy's CDO pointer while users still have live positions or claims. The privileged actor is not malicious; this can happen during a routine CDO replacement or an incorrect deployment/migration sequence.

The impact is temporary rather than irrecoverable because restoring the old CDO address re-enables the old withdrawal path. However, no on-chain migration guard prevents the intermediate state, and users cannot bypass `_onlyIdleCDO` themselves.

### Recommendation
Restrict `setWhitelistedCDO` so it cannot be changed while the vault has active strategy-token supply attributable to the current CDO, pending normal or instant withdrawals, post-default claims, or an active epoch.

Alternatively, implement an explicit migration that atomically registers the replacement CDO, preserves receipt ownership and pending balances, and provides a claim forwarding path for users of the old CDO.

### Proof of Concept
The following test extends `TestIdleCreditVault`, whose fixture already deploys an `IdleCDOEpochVariant`, configures `IdleCreditVault`, disables Keyring, and approves the borrower. [9](#0-8) 

```solidity
function testChangeWhitelistedCDOStrandsFundedReceipt() external {
  address user = makeAddr("user");
  uint256 amount = 10_000e6;

  deal(USDC, user, amount);

  vm.startPrank(user);
  IERC20Detailed(USDC).approve(address(cdoEpoch), amount);
  idleCDO.depositAA(amount);
  uint256 receipt = cdoEpoch.requestWithdraw(0, idleCDO.AATranche());
  vm.stopPrank();

  assertGt(receipt, 0);

  vm.prank(manager);
  cdoEpoch.startEpoch();

  vm.warp(cdoEpoch.epochEndDate() + 1);
  deal(
    USDC,
    borrower,
    cdoEpoch.expectedEpochInterest() + strategy.pendingWithdraws()
  );

  vm.prank(manager);
  cdoEpoch.stopEpoch(0, 0);

  assertEq(strategy.withdrawsRequests(user), receipt);
  assertGe(IERC20Detailed(USDC).balanceOf(address(strategy)), receipt);

  address replacementCDO = makeAddr("replacementCDO");
  vm.prank(owner);
  strategy.setWhitelistedCDO(replacementCDO);

  vm.prank(user);
  vm.expectRevert(NotAllowed.selector);
  cdoEpoch.claimWithdrawRequest();

  assertEq(strategy.withdrawsRequests(user), receipt);
  assertEq(IERC20Detailed(USDC).balanceOf(address(strategy)), receipt);
}
```

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L243-245)
```text
  function requestWithdraw(uint256 _amount, address _user, uint256 _principal) external {
    _onlyIdleCDO();
    _ensureDefaultRecoveryInitialized();
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L301-314)
```text
  function claimWithdrawRequest(address _user) external returns (uint256 amount) {
    _onlyIdleCDO();
    if (defaultRecoveryFinalized) {
      // Post-default requests are already priced after the haircut and backed by the reserve,
      // so they must not fall through to the defaulted-epoch receipt logic.
      amount = _claimPostDefaultWithdrawRequest(_user);
      if (amount != 0) return amount;
      // Only receipts created in the defaulted epoch are haircutted here; old fulfilled
      // receipts are handled below at par if they were already funded before default.
      amount = _claimDefaultedWithdrawRequest(_user);
    }
    amount += _claimLossAdjustedWithdrawRequest(_user);
    return amount + _claimFundedWithdrawRequest(_user);
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L380-392)
```text
  function claimInstantWithdrawRequest(address _user) external {
    _onlyIdleCDO();
    if (defaultRecoveryFinalized && defaultInstantWithdrawsFinalized) {
      // Clear the defaulted-epoch instant receipt first, then continue so the same call can
      // also pay any older instant receipt that was already funded before default finalization.
      _claimDefaultedInstantWithdrawRequest(_user);
    }
    uint256 amount = instantWithdrawsRequests[_user];
    // burn strategy tokens from user
    _burn(_user, amount);

    instantWithdrawsRequests[_user] = 0;
    _transferFundedClaim(_user, amount);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L953-957)
```text
  /// @notice allow to update whitelisted address
  function setWhitelistedCDO(address _cdo) external onlyOwner {
    require(_cdo != address(0), "IS_0");
    idleCDO = _cdo;
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L967-972)
```text
  /// @notice Modifier to make sure that caller os only the idleCDO contract
  function _onlyIdleCDO() internal view {
    if (msg.sender != idleCDO) {
      revert NotAllowed();
    }
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L965-970)
```text
  /// @notice Claim a withdraw request from the vault. Can be done when at least 1 epoch passed
  /// since last withdraw request
  function claimWithdrawRequest() external {
    // underlyings requested, here we check that user waited at least one epoch and that borrower
    // did not default upon repayment (old requests can still be claimed)
    IdleCreditVault(strategy).claimWithdrawRequest(msg.sender);
```

**File:** contracts/IdleCDOCreditVault.sol (L68-79)
```text
    token = _guardedToken;
    strategy = _strategy;
    strategyToken = _strategyToken;
    trancheAPRSplitRatio = _trancheAPRSplitRatio;
    uint256 _oneToken = 10**(IERC20Detailed(_guardedToken).decimals());
    oneToken = _oneToken;
    priceAA = _oneToken;
    priceBB = _oneToken;
    // skipDefaultCheck = false is the default value
    // Set allowance for strategy
    _allowUnlimitedSpend(_guardedToken, _strategy);
    _allowUnlimitedSpend(_strategyToken, _strategy);
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L153-168)
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
```

**File:** test/foundry/IdleCreditVault.t.sol (L46-117)
```text
  function setUp() public override {
    vm.createSelectFork("mainnet", FORK_BLOCK);
    super.setUp();
  }

  function _deployCDO() internal override returns (IdleCDO _cdo) {
    _cdo = IdleCDO(address(new IdleCDOEpochVariant()));
  }

  function _deployStrategy(address _owner) internal override returns (address _strategy, address _underlying) {
    _underlying = defaultUnderlying;
    strategy = new IdleCreditVault();
    strategyToken = IERC20Detailed(address(strategy));
    _strategy = address(strategy);

    stdstore.target(_strategy).sig(strategy.token.selector).checked_write(address(0));
    IdleCreditVault(_strategy).initialize(_underlying, _owner, manager, borrower, borrowerName, initialProvidedApr);
  }

  function _deployLocalContracts() internal override returns (IdleCDO _cdo) {
    // We modified only the initial apr split ratio
    address _owner = address(0xdeadbad);
    address _rebalancer = address(0xbaddead);
    (address _strategy, address _underlying) = _deployStrategy(_owner);
    
    // deploy idleCDO and tranches
    _cdo = _deployCDO();
    stdstore.target(address(_cdo)).sig(_cdo.token.selector).checked_write(address(0));
    _cdo.initialize(
      0,
      _underlying,
      address(this), // governanceFund,
      _owner, // owner,
      _rebalancer, // rebalancer,
      _strategy, // strategyToken
      100000 // apr split: 100000 is 100% to AA
    );

    vm.startPrank(_owner);
    _cdo.setIsAYSActive(true);
    IdleCDOEpochVariant(address(_cdo)).setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, 0);
    vm.stopPrank();

    _postDeploy(address(_cdo), _owner);
  }

  function _postDeploy(address _cdo, address _owner) internal override {
    vm.prank(_owner);
    IdleCreditVault cv = IdleCreditVault(address(strategy));
    cv.setWhitelistedCDO(_cdo);
    creditVaultBase = IdleCDOCreditVault(_cdo);
    /// borrower must approve CDO to withdraw funds
    vm.prank(borrower);
    IERC20Detailed(defaultUnderlying).approve(_cdo, type(uint256).max);
    cdoEpoch = IdleCDOEpochVariant(_cdo);
    uint256 epochDuration = 36.5 days;
    uint256 buffer = 5 days;
    // For testing let's support both tranches with AYS
    vm.startPrank(_owner);
    cdoEpoch.setBBDepositEnabled(true);
    cdoEpoch.setIsAYSActive(true);
    cdoEpoch.setInstantWithdrawParams(3 days, 1.5e18, false);
    cdoEpoch.setEpochParams(epochDuration, buffer); // set this to have an epoch during 1/10 of the year
    cdoEpoch.setKeyringParams(address(0), 0); // deactivate keyring
    vm.stopPrank();

    // we set the apr again manually for tests because we changed epoch params
    // scaled by the buffer period
    vm.startPrank(cv.manager());
    cv.setApr(_scaleAprWithBuffer(initialProvidedApr));
    vm.stopPrank();
  }
```
