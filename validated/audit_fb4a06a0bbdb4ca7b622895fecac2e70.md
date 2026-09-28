### Title
Unprivileged lender can force an honest borrower into default by requesting an instant withdrawal larger than the borrower's liquid balance - ([File: contracts/IdleCDOEpochVariant.sol](contracts/IdleCDOEpochVariant.sol))

### Summary
The external report describes a race where cheap, repeated unprivileged requests corrupt shared state and crash a controller, stalling everyone else's work. The closest credit-vault analog is the instant-withdraw path: any KYC'd tranche holder can open an instant-withdraw request of arbitrary size, and the only resolution path (`getInstantWithdrawFunds`) pulls that full amount from the borrower in a single `transferFrom`. If the borrower — though honest and solvent — does not hold enough liquid underlying at that moment, the `try/catch` in `getInstantWithdrawFunds` routes to `_handleBorrowerDefault`, which permanently pauses the pool, stops the epoch, and disables all withdraw requests. Like the Argo crash-loop, a cheap user transaction triggers a protocol-wide stall whose cause (a liquidity-timing mismatch, not borrower insolvency) is hard to attribute.

### Finding Description
`requestWithdraw` burns the caller's tranche tokens and forwards the request to `IdleCreditVault.requestInstantWithdraw` whenever `_isInstantWithdrawEnabled()` and `lastEpochApr > currentApr + instantWithdrawAprDelta` (i.e., after the manager lowers APR — a routine honest action the attacker sequences around). There is no cap relating the request size to borrower liquidity. `instantWithdrawsRequests` and `pendingInstantWithdraws` grow unboundedly by user demand: [1](#0-0) 

At/after `instantWithdrawDeadline`, `getInstantWithdrawFunds` attempts `getFundsFromBorrower(_instantWithdraws)` for the *entire* pending amount in one transfer. On any failure of that single transfer it calls `_handleBorrowerDefault(_instantWithdraws)`: [2](#0-1) [3](#0-2) 

`_handleBorrowerDefault` sets `defaulted = true`, pauses the contract, sets `isEpochRunning = false`, and clears `allowAAWithdrawRequest`/`allowBBWithdrawRequest`. From then on `stopEpoch` reverts (`!isEpochRunning`), `startEpoch` reverts on a defaulted pool, `unpause` is blocked, and every user fund is frozen until `finalizeDefault` — which applies an aggregate recovery haircut that discards AA seniority. The test `testGetInstantWithdrawFundsDefault` and `testClaimWithdrawRequestWithInstantDefault` confirm that simply not dealing the borrower enough underlying before `getInstantWithdrawFunds` produces exactly this default and freezes withdrawals.

There is also a partial-fill trap: `startEpoch` sends `min(pendingInstant, totUnderlyings)` to the strategy and `collectInstantWithdrawFunds` decrements `pendingInstantWithdraws` accordingly, so an unfunded remainder persists into the running epoch, blocks `_stopEpoch` via the `_pendingInstant() != 0` check, and again resolves only through `getInstantWithdrawFunds` → borrower transfer → default.

### Impact Explanation
- **Temporary freezing of all pool funds**: after the forced default, deposits, withdraw requests, `stopEpoch`, and `startEpoch` are all disabled until governance/manager runs `finalizeDefault`; `testDefaultedVaultCannotBeUnpausedOrRestartedBeforeFinalization` shows there is no other recovery path.
- **Loss socialization**: `finalizeDefault` prices all tranches with one aggregate recovery multiplier, so the attacker's forced default crystallizes a haircut on honest AA/BB holders even though the borrower never actually failed to repay — the loss basis `defaultPendingClaimBasis` includes the attacker's own receipt.
- The attacker spends only the opportunity cost of one withdraw request (their receipt is haircut like everyone else's, but they can size the request small — even a dust instant request forces the same single-shot `transferFrom` for the *aggregate* `pendingInstantWithdraws`, and the default fires regardless of whether the attacker profits).

### Likelihood Explanation
- **Requirements**: instant-withdraw mode active (`disableInstantWithdraw == false`, non-programmable borrower) and an epoch where the manager lowers APR by more than `instantWithdrawAprDelta` — a normal treasury action. Any KYC'd tranche holder qualifies as the attacker.
- **Trigger reliability**: the check is `transferFrom(borrower, cdo, pendingInstantWithdraws)` all-or-nothing. A real credit borrower deploys funds into loans and keeps limited liquid underlying; an attacker can observe `underlying.balanceOf(borrower)` on-chain and request slightly more than that balance, or stack requests across users of the same wallet set (sybil lenders) to exceed it. There is no rate limit, cooldown, or per-request liquidity cap — the same "unbounded concurrent requests crash shared state" shape as the Argo advisory.
- **Detection asymmetry** mirrors the report: the panic/default log (`BorrowerDefault` event) suggests borrower insolvency, not a liquidity-timing attack, so admins cannot easily identify the responsible user.

### Recommendation
- Cap instant-withdraw funding attempts and treat partial failures as unfunded requests rather than borrower default: pull `min(pendingInstantWithdraws, borrowerAvailable)` and leave the remainder claimable next epoch instead of calling `_handleBorrowerDefault`.
- Alternatively, only declare default after a grace period or repeated failed funding attempts, and mark the epoch so pending instant requests settle at `lossRecoveryPriceByEpoch` rather than halting the whole pool.
- Bound `requestInstantWithdraw` to a fraction of currently available/committed liquidity, or require manager confirmation (`processInstantWithdrawRequests`-style batching) before requests become borrower obligations.

### Proof of Concept
Foundry fork test (pattern follows `testGetInstantWithdrawFundsDefault` / `testClaimWithdrawRequestWithInstantDefault` in `test/foundry/IdleCreditVault.t.sol`):

```solidity
function testInstantRequestForcesDefault() external {
    uint256 amountWei = 10000 * ONE_SCALE;
    idleCDO.depositAA(amountWei);           // attacker is an ordinary AA holder
    _startEpochAndCheckPrices(0);
    // manager honestly lowers APR -> instant window opens for the NEXT epoch
    _stopEpochAndCheckPrices(0, initialProvidedApr / 2, _expectedFundsEndEpoch());

    // attacker requests instant withdraw for more than borrower's liquid underlying
    uint256 borrowerBal = underlying.balanceOf(borrower);
    cdoEpoch.requestWithdraw(0, address(AAtranche)); // request sized so pendingInstantWithdraws > borrowerBal
    assertGt(IdleCreditVault(address(strategy)).pendingInstantWithdraws(), borrowerBal);

    _startEpochAndCheckPrices(1);
    vm.warp(block.timestamp + cdoEpoch.instantWithdrawDelay() + 1);

    // honest manager attempts to fund instant withdraws; single transfer fails -> forced default
    vm.prank(manager);
    cdoEpoch.getInstantWithdrawFunds();

    assertTrue(cdoEpoch.defaulted());
    assertFalse(cdoEpoch.isEpochRunning());
    assertTrue(cdoEpoch.paused());
    // all withdrawals and epoch progression are frozen until finalizeDefault haircut
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.claimWithdrawRequest();
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpoch(0, 0);
}
```

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L356-374)
```text
  function requestInstantWithdraw(uint256 _amount, address _user) external {
    _onlyIdleCDO();
    _ensureDefaultRecoveryInitialized();
    // burn strategy tokens from cdo
    _burn(msg.sender, _amount);
  
    // mint equal amount of strategy tokens to the user as receipt, useful in case of default
    _mint(_user, _amount);

    // increase the instant withdraw requests for the user
    instantWithdrawsRequests[_user] += _amount;
    uint256 currentEpoch = epochNumber;
    // we record both per-user (old, kept for compatibility) and per-epoch so on
    // finalization we can distinguish "default-epoch pending instant receipts"
    // from old funded instant receipts.
    instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
    instantWithdrawClaimsByEpoch[currentEpoch] += _amount;
    // increase the total instant withdraw requests
    pendingInstantWithdraws += _amount;
```

**File:** contracts/IdleCDOEpochVariant.sol (L558-574)
```text
  function getInstantWithdrawFunds() external {
    _checkOnlyOwnerOrManager();
    // Check that programmable mode is disabled, the epoch is running and the deadline passed.
    _checkNotAllowed(isProgrammableBorrower || !isEpochRunning || block.timestamp < instantWithdrawDeadline);

    IdleCreditVault _strategy = IdleCreditVault(strategy);
    uint256 _instantWithdraws = _pendingInstant();
    // transfer funds for instant withdraw to this contract
    try this.getFundsFromBorrower(_instantWithdraws) {
      // transfer funds to IdleCreditVault and decrease pendingInstantWithdraws
      _strategy.collectInstantWithdrawFunds(_instantWithdraws);
      // allow instant withdraws
      allowInstantWithdraw = true;
    } catch {
      _handleBorrowerDefault(_instantWithdraws);
    }
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L577-598)
```text
  function _handleBorrowerDefault(uint256 funds) internal {
    defaulted = true;
    // Do not reopen instant claims here. They remain disabled when funding is pending;
    // successful full funding is the only path that enables them before finalization.

    if (isProgrammableBorrower) {
      IProgrammableBorrower(_borrower()).onDefault();
    }

    // deposits should be already prevented
    if (!paused()) {
      _pause();
    }

    // stop the current epoch
    isEpochRunning = false;

    // prevent withdrawals requests
    allowAAWithdrawRequest = false;
    allowBBWithdrawRequest = false;

    emit BorrowerDefault(funds);
```
