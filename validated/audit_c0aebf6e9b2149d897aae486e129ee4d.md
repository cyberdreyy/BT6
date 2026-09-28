### Title
Queued deposits over-credit fee-on-transfer tokens and allow refunds above the amount received - (File: contracts/IdleCDOEpochQueue.sol)

### Summary

`IdleCDOEpochQueue.requestDeposit` records the caller-supplied `amount` instead of the underlying balance actually received by the queue. If the vault underlying charges a transfer fee, rebases down, or otherwise transfers less than the requested amount, the depositor’s queued balance and `epochPendingDeposits` are still credited for the full requested value. `deleteRequest` later refunds the recorded amount, allowing the depositor to withdraw more underlying than was supplied and drain other users’ queued deposits.

### Finding Description

During a running epoch, a KYC-allowed depositor calls `requestDeposit(amount)`. The queue performs `safeTransferFrom(msg.sender, address(this), amount)` and then unconditionally increments both `userDepositsEpochs[msg.sender][nextEpoch]` and `epochPendingDeposits[nextEpoch]` by `amount`, without measuring the balance delta. [1](#0-0) 

Before the epoch is processed or prefunded, the same depositor can call `deleteRequest`. That function reads the recorded amount, decreases pending deposits by it, and transfers the full recorded amount back to the depositor. [2](#0-1) 

If `amount` is `100` and the underlying token delivers only `90` to the queue, the queue ledger nevertheless records `100`. The subsequent refund transfers `100`, so the attacker receives the original `90` economic value plus `10` belonging to other queued depositors. Unlike the CDO deposit path, which computes minted shares from the post-transfer balance delta, the queue trusts the requested parameter. [3](#0-2) 

### Impact Explanation

This is a direct theft of queued user funds. Each deflationary deposit can create an accounting surplus equal to:

```text
requested amount - actual amount received
```

The attacker can repeatedly deposit and delete requests before manager processing, extracting tokens contributed by other users until the queue’s actual underlying balance is exhausted. The loss is bounded only by the transferable fee/rebase shortfall and available queued liquidity. In a queue holding `N` victim deposits and an attacker request transferring `N - X`, the attacker can withdraw `N`, causing an `X` loss to the remaining requesters.

The same inflated `epochPendingDeposits` value is also used by the prefunded path. Manager-driven `processDepositsToBorrower` sends the recorded `_pending` amount to the borrower, so inflated accounting can also cause the queue to forward funds contributed by other users or revert if the queue lacks the recorded balance. [4](#0-3) 

### Likelihood Explanation

The attack requires the credit vault’s configured underlying to deliver less than the transfer amount, such as a fee-on-transfer or deducted-transfer asset, and requires at least one third-party queued deposit or other queue-held balance to absorb the shortage. The attacker only needs to be a normal allowed depositor and does not require a privileged role, borrower misbehavior, price manipulation, or default. The absence of a received-amount check makes the exploit deterministic whenever such an underlying is configured.

### Recommendation

Record deposits using the measured balance delta rather than the requested amount:

```solidity
uint256 balanceBefore = IERC20Detailed(underlying).balanceOf(address(this));
IERC20Detailed(underlying).safeTransferFrom(msg.sender, address(this), amount);
uint256 received = IERC20Detailed(underlying).balanceOf(address(this)) - balanceBefore;

userDepositsEpochs[msg.sender][nextEpoch] += received;
epochPendingDeposits[nextEpoch] += received;
```

The same received amount should be used for prefunding checks, prefunded accounting, epoch pricing, and refunds. Alternatively, explicitly reject non-exact transfers when `received != amount`.

### Proof of Concept

A reproducible Foundry fork PoC can use the deployed vault underlying if it supports deducted transfers, or a configured deducted-transfer underlying in the existing fork harness:

```solidity
// Epoch is running; attacker and victim are KYC-allowed.
uint256 requestAmount = 100e6;
uint256 receivedByQueue = 90e6; // 10% transfer deduction

// Victim queues 10e6 normally.
vm.prank(victim);
queue.requestDeposit(10e6);

// Attacker requests 100e6, but the token delivers only 90e6.
vm.prank(attacker);
underlying.approve(address(queue), requestAmount);
vm.prank(attacker);
queue.requestDeposit(requestAmount);

assertEq(queue.userDepositsEpochs(attacker, nextEpoch), requestAmount);
assertEq(underlying.balanceOf(address(queue)), receivedByQueue + 10e6);

// Refund pays the recorded 100e6, not the 90e6 actually received.
uint256 attackerBefore = underlying.balanceOf(attacker);
vm.prank(attacker);
queue.deleteRequest(nextEpoch);

assertEq(underlying.balanceOf(attacker) - attackerBefore, requestAmount);
// Queue retains only the victim's remaining 0e6 after paying the attacker
// from the attacker's 90e6 contribution plus the victim's 10e6.
assertEq(underlying.balanceOf(address(queue)), 0);
```

The violated invariant is one recorded queued deposit equals one unit of underlying actually received; refunding the requested amount instead of the received amount converts that invariant break into theft of other queued deposits.

### Citations

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

**File:** contracts/IdleCDOEpochQueue.sol (L166-179)
```text
    uint256 _epoch = _strategy.epochNumber() + 1;
    // prefunding can happen only once for an epoch
    _checkNotAllowed(epochPrefundedDeposits[_epoch] != 0);
    uint256 _pending = epochPendingDeposits[_epoch];
    if (_pending == 0) {
      return;
    }
    // Recheck after the cutoff because emergency state or the TVL limit can change while queued.
    IdleCDOEpochVariantPrefunded(idleCDOEpoch).checkPrefunding(_pending);

    // Switch the epoch from "queue-held" to "already at borrower" before transferring funds.
    epochPendingDeposits[_epoch] = 0;
    epochPrefundedDeposits[_epoch] = _pending;
    IERC20Detailed(underlying).safeTransfer(_strategy.borrower(), _pending);
```

**File:** contracts/IdleCDOEpochQueue.sol (L184-199)
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
  }
```

**File:** contracts/IdleCDO.sol (L249-253)
```text
    address _token = token;
    uint256 _preBal = _contractTokenBalance(_token);
    _transferUnderlyingsFrom(msg.sender, address(this), _amount);
    // mint tranche tokens according to the current tranche price
    _minted = _mintSharesAtCurrPrice(_contractTokenBalance(_token) - _preBal, msg.sender, _tranche);
```
