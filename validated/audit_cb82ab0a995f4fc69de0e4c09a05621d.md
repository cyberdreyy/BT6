### Title
Unprefunded queue deposits permanently brick `stopEpochWithDuration` on the prefunded CDO variant — epoch can never close, all pool funds frozen - (File: contracts/IdleCDOEpochVariantPrefunded.sol)

### Summary
The prefunded epoch variant contains two mutually inconsistent liveness conditions, analogous to the Asciidoctor bug where two regexes disagree and the line is pushed back forever: the queue allows users to deposit underlying for epoch `N+1` while epoch `N` is running (`requestDeposit` writes `epochPendingDeposits[N+1]`), but the only code path that clears that bucket — `processDepositsToBorrower` — can only move `epochPendingDeposits[epochNumber()+1]` *during* epoch `N`. Once epoch `N` ends without prefunding, `epochPendingDeposits[N+1]` can never be cleared by anyone (only the depositor can `deleteRequest`, and only for their own deposit), while `prefundedDepositsToProcess` hard-reverts whenever `epochPendingDeposits[_epoch] != 0`. Since `_afterStopEpochWithDuration` calls it unconditionally when `epochQueue != 0`, every `stopEpoch`/`stopEpochWithDuration` for epoch `N+1` reverts permanently — the state machine loops on the same unresolvable state exactly like the CVE's pushed-back line.

### Finding Description
1. `IdleCDOEpochQueue.requestDeposit` lets any KYC'd wallet queue deposits for `epochNumber() + 1` while the epoch is running, gated only by the optional `prefundedDepositWindow` (which can be 0, and even when set leaves the whole early part of the epoch open). [1](#0-0) 
2. `processDepositsToBorrower` converts `epochPendingDeposits` → `epochPrefundedDeposits`, but only for `_epoch = epochNumber() + 1` and only while `isEpochRunning()` — i.e., only during the epoch *before* the deposits' target epoch. [2](#0-1) 
3. `prefundedDepositsToProcess` reverts with `NotAllowed` whenever `epochPendingDeposits[_epoch] != 0` for the epoch being settled. [3](#0-2) 
4. `_afterStopEpochWithDuration` in `IdleCDOEpochVariantPrefunded` unconditionally calls `prefundedDepositsToProcess()` whenever `epochQueue` is set, so a revert propagates and the entire stop transaction fails. [4](#0-3) 
5. `deleteRequest` can only clear `msg.sender`'s own deposit, and `epochPrice[_epoch]` is never set (deposits were never processed), so the block at line 187 doesn't help — but the attacker simply never deletes. [5](#0-4) 

The disagreement: "deposits pending for epoch X" is a legal state per the queue, but "epoch X has pending deposits" is a hard-revert state per the settlement path, and after epoch X-1 ends there exists no transaction that transitions between the two. The epoch-ending call loops forever on the same unresolvable state.

### Impact Explanation
Permanent freezing of all user funds in the vault. If `stopEpochWithDuration` can never execute for epoch `N+1`, the epoch never ends, `epochNumber` never advances past it, `claimWithdrawRequest`'s `epochNumber <= lastWithdrawRequest` gate never opens for that epoch's requesters, withdraw receipts are never funded, and all AA/BB tranche holders' principal plus the stuck queued deposits are frozen indefinitely (barring an upgrade/emergency shutdown, which itself realizes losses). Loss magnitude: full pool TVL plus queued deposits, locked by a dust deposit (e.g., 1 wei of underlying, subject only to KYC which the attacker passes as a normal lender).

### Likelihood Explanation
High. The attacker only needs to be a KYC-passing wallet calling `queue.requestDeposit(1)` on the AA tranche of a prefunded vault during any running epoch, timed late enough that the honest manager does not prefund it (prefunding is optional and manual — `processDepositsToBorrower` is owner/manager-called, not automatic). Even with `prefundedDepositWindow` set, the attacker deposits just before the window cutoff; the manager then cannot prefund if the cutoff has passed (`processDepositsToBorrower` requires `block.timestamp + window < epochEndDate` at line 164), and the window blocks... the deposit lands before the cutoff but prefunding may race the same cutoff — in the worst configuration (window = 0) deposits are accepted until `epochEndDate` with no prefunding guarantee at all. No privileged misbehavior required; the attacker is an ordinary lender.

### Recommendation
Add a permissionless escape that transitions `epochPendingDeposits[_epoch]` out of the revert-inducing state once its processing window has passed. Concretely, either:
- allow `processDepositsToBorrower` (or a new `sweepPendingDeposits`) to move `epochPendingDeposits[_epoch]` back to users / mark it refundable once epoch `_epoch` has started or ended, or
- make `prefundedDepositsToProcess`/`_afterStopEpochWithDuration` treat residual `epochPendingDeposits[_epoch]` for an already-started epoch as refundable rather than reverting (e.g., set a zero-price `epochPrice` sentinel and let users `deleteRequest`/`claimDepositRequest` a refund).

The invariant to enforce: any state writable by an unprivileged user must have a guaranteed forward transition that no single user's inaction can block.

### Proof of Concept
Foundry fork PoC sketch (prefunded variant, e.g. `IdleCDOEpochVariantPrefunded` + AA `IdleCDOEpochQueue`):

```solidity
// setup: prefunded CDO with epochQueue set, epoch N running
vm.prank(attacker); // KYC'd wallet
underlying.approve(address(queueAA), 1);
// near end of epoch N (before window cutoff, or any time if window == 0)
vm.prank(attacker);
queueAA.requestDeposit(1); // epochPendingDeposits[N+1] = 1

// manager does NOT call processDepositsToBorrower (optional, manual step)

// epoch N ends and is stopped normally -> epochNumber becomes N+1... 
// (stopEpoch for N succeeds: _prefundedEpochToProcess = N, epochPendingDeposits[N] == 0)
vm.prank(manager);
cdo.startEpoch(); // epoch N+1 runs

// epoch N+1 matures; borrower repays principal+interest
vm.warp(cdo.epochEndDate() + 1);
vm.prank(borrower);
underlying.approve(address(cdo), repayAmount);

vm.prank(manager);
// _afterStopEpochWithDuration -> prefundedDepositsToProcess()
// -> epochPendingDeposits[N+1] != 0 -> revert NotAllowed
vm.expectRevert(NotAllowed.selector);
cdo.stopEpochWithDuration(0);

// repeat forever: every subsequent stop attempt reverts identically.
// Attacker never calls deleteRequest; manager cannot clear attacker's deposit.
// All tranche holders' funds remain locked in the running epoch.
```

Caveat I could not fully verify within index limits: whether `stopEpochWithDuration`'s buffer mechanics allow a later admin workaround (e.g., `setEpochQueue(0)` then re-enabling) — `setEpochQueue` is owner/manager-only, so governance could technically disable the queue to unstick the epoch, but that is a manual rescue, not a protocol-level guard, and stranding/orphaning the queue's accounting (queued deposits become unprocessable, `epochPrice` never set) still permanently freezes attacker-adjacent honest deposits.

### Citations

**File:** contracts/IdleCDOEpochQueue.sol (L103-127)
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
  }
```

**File:** contracts/IdleCDOEpochQueue.sol (L149-180)
```text
  function processDepositsToBorrower() external {
    IdleCDOEpochVariant _cdo = IdleCDOEpochVariant(idleCDOEpoch);
    IdleCreditVault _strategy = IdleCreditVault(strategy);
    // only owner or strategy manager can move queued funds to the borrower
    _checkOnlyOwnerOrManager();
    // prefunded flow is supported only for AA queue
    // queue must be explicitly enabled on the prefunded CDO variant
    _checkNotAllowed(tranche != _cdo.AATranche() || !_isPrefundedQueueEnabled());

    // prefunding can be done only while epoch is running
    if (!_cdo.isEpochRunning()) {
      revert EpochNotRunning();
    }
    // Keep prefunding aligned with the same cutoff enforced for new queued deposits.
    uint256 _prefundedWindow = prefundedDepositWindow;
    _checkNotAllowed(_prefundedWindow == 0 || block.timestamp + _prefundedWindow < _cdo.epochEndDate());

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
  }
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

**File:** contracts/IdleCDOEpochQueue.sol (L277-282)
```text
  function prefundedDepositsToProcess() external view returns (uint256 _prefunded) {
    uint256 _epoch = _prefundedEpochToProcess(IdleCDOEpochVariant(idleCDOEpoch));
    _prefunded = epochPrefundedDeposits[_epoch];
    // Prefunded queues must not reach stopEpochWithDuration with raw underlyings still sitting in the queue.
    _checkNotAllowed(epochPendingDeposits[_epoch] != 0);
  }
```

**File:** contracts/IdleCDOEpochVariantPrefunded.sol (L72-89)
```text
  function _afterStopEpochWithDuration() internal override {
    address _queue = epochQueue;
    if (_queue == address(0)) return;

    IIdleCDOEpochQueuePrefunded _epochQueue = IIdleCDOEpochQueuePrefunded(_queue);
    uint256 _prefunded = _epochQueue.prefundedDepositsToProcess();
    if (_prefunded == 0) return;
    // A zero post-loss AA price cannot safely mint new shares into the same tranche token.
    _checkNotAllowed(priceAA == 0);

    // Prefunded deposits already reached the borrower, so they must join AA even if stop defaulted.
    // Mint tranche shares at the post-stop price and mirror the same amount in strategy tokens,
    // so the queue can later distribute shares to users at the epoch price.
    uint256 _prefundedMinted = _mintSharesAtCurrPrice(_prefunded, _queue, AATranche);
    IdleCreditVault(strategy).mintStrategyTokens(_prefunded);
    // Finalize the prefunded epoch in the queue by storing the epoch price and clearing state.
    _epochQueue.processPrefundedDeposits(_prefundedMinted);
  }
```
