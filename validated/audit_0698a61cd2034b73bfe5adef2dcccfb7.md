### Title
Stale per-epoch instant-withdraw basis is never cleared on normal claims, inflating default recovery and freezing claimant funds - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
The CVE analog is a resource leak on a non-default path: `requestInstantWithdraw` allocates per-epoch accounting (`instantWithdrawsRequestsByEpoch[user][epoch]`, `instantWithdrawClaimsByEpoch[epoch]`), but the normal claim path `claimInstantWithdrawRequest` only clears the aggregate `instantWithdrawsRequests[user]` — the per-epoch "job" entries are never released. Those leaked entries are later consumed by default-recovery accounting (`_defaultPrefundedInstantReserve`, `_claimDefaultedInstantWithdrawRequest`), corrupting the recovery price and the reserve.

### Finding Description
In `IdleCreditVault.requestInstantWithdraw`, three counters are incremented: `instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch[_user][currentEpoch]` and `instantWithdrawClaimsByEpoch[currentEpoch]` [1](#0-0) .

`claimInstantWithdrawRequest` burns and zeroes only `instantWithdrawsRequests[_user]`; neither per-epoch mapping is touched [2](#0-1) . The only place that clears `instantWithdrawsRequestsByEpoch`/`instantWithdrawClaimsByEpoch` is the default path `_claimDefaultedInstantWithdrawRequest` [3](#0-2) .

If a borrower default is finalized while `pendingInstantWithdraws != 0` for that same epoch, `defaultPendingClaimBasis()` adds the still-recorded `instantWithdrawClaimsByEpoch[epochNumber]` — including receipts already paid out [4](#0-3)  — and `_defaultPrefundedInstantReserve()` counts `instantBasis - pendingInstant` as cash held by the strategy even though claimed funds were already transferred out [5](#0-4) . `finalizeDefaultRecovery` then computes `recoveryPrice` over the inflated basis and credits the phantom cash into `defaultRecoveryReserve` [6](#0-5) .

Two concrete consequences once `defaultRecoveryFinalized && defaultInstantWithdrawsFinalized`:

1. A user who already claimed their funded instant receipt still has `instantWithdrawsRequestsByEpoch[user][defaultEpoch] != 0`. `claimInstantWithdrawRequest` calls `_claimDefaultedInstantWithdrawRequest` first, which executes `instantWithdrawsRequests[_user] -= claimBasis` where the aggregate is already 0 — the subtraction underflows and reverts, permanently blocking any other funded instant claims of that user.
2. The phantom `prefundedReserve` raises `defaultRecoveryPrice` without backing. Earlier recovery claimants (defaulted normal withdraws via `_claimDefaultedWithdrawRequest`, post-default requests, or other instant receipts) are paid at the inflated ratio and drain the real reserve, so later claimants' `_transferDefaultRecovery` reverts on insufficient underlying — permanent freeze of unclaimed recovery.

### Impact Explanation
Direct insolvency/freeze of user funds: recovery payouts exceed actual held cash, so a subset of defaulted-epoch receipt holders can never claim (unclaimed yield permanently frozen), and users with stale per-epoch basis have their funded instant claims bricked. Loss magnitude is bounded by the sum of instant receipts claimed in the default epoch, which can be the entire prefunded instant bucket (arbitrary size set by depositors during the buffer).

### Likelihood Explanation
Requires: an epoch where instant withdrawals are enabled, at least one instant requester gets funded and claims, `pendingInstantWithdraws` remains nonzero (another unfunded instant receipt in the same epoch), and the borrower defaults before that remainder is funded — e.g., `getInstantWithdrawFunds` pull fails or `stopEpoch` borrower repayment fails. All preconditions are reachable by unprivileged lenders plus honest borrower/manager sequencing; no privileged misbehavior needed.

### Recommendation
Clear `instantWithdrawsRequestsByEpoch[_user][epoch]` and decrement `instantWithdrawClaimsByEpoch[epoch]` inside `claimInstantWithdrawRequest` (and in any funded-claim path), so per-epoch claim basis always reflects only unclaimed receipts. Add a fork test asserting `instantWithdrawClaimsByEpoch` returns to zero after all current-epoch instant claims, then re-run a default finalization to confirm `recoveryPrice` is computed only over genuinely outstanding basis.

### Proof of Concept
Foundry fork sketch (mirror `test/foundry/IdleCreditVault.t.sol` helpers `_startEpochAndCheckPrices`, `_toggleEpoch`, `_getInstantFunds`):

```solidity
function testStaleInstantBasisCorruptsDefaultRecovery() external {
    // enable instant withdrawals
    vm.prank(manager);
    cdoEpoch.setInstantWithdrawParams(100, 1e18, false);

    // two AA depositors
    uint256 tranchesA = _depositWithUser(alice, 10_000e6);
    uint256 tranchesB = _depositWithUser(bob,   10_000e6);
    _startEpochAndCheckPrices(0);

    // stop with sharply lower APR so requestWithdraw routes to requestInstantWithdraw
    _stopCurrentEpochWithApr(initialProvidedApr / 2); // epoch N = epochNumber()

    vm.prank(alice); cdoEpoch.requestWithdraw(0, address(AAtranche)); // instant req A
    vm.prank(bob);   cdoEpoch.requestWithdraw(0, address(AAtranche)); // instant req B

    // startEpoch: leave CDO cash covering only A's request -> pendingInstantWithdraws = B's amount
    _startEpochAndCheckPrices(0); // partial collect via collectInstantWithdrawFunds(totUnderlyings)

    // Alice claims her funded receipt: aggregate cleared, per-epoch basis leaks
    vm.warp(block.timestamp + cdoEpoch.instantWithdrawDelay() + 1);
    vm.prank(alice); cdoEpoch.claimInstantWithdrawRequest();
    assertGt(IdleCreditVault(address(strategy)).instantWithdrawsRequestsByEpoch(alice, epochN), 0);

    // Borrower fails to fund remaining instant -> default in same epoch N
    vm.prank(manager);
    cdoEpoch.getInstantWithdrawFunds(); // getFundsFromBorrower reverts -> _handleBorrowerDefault

    // finalize: prefundedReserve counts Alice's already-paid cash; recoveryPrice inflated
    deal(defaultUnderlying, manager, recovered);
    vm.startPrank(manager);
    underlying.approve(address(strategy), recovered);
    cdoEpoch.finalizeDefault(recovered, manager);
    vm.stopPrank();

    // Alice's next claim reverts on instantWithdrawsRequests underflow -> frozen claims
    vm.prank(alice);
    vm.expectRevert();
    cdoEpoch.claimInstantWithdrawRequest();

    // Other claimants are paid at inflated price; last claimant's
    // _transferDefaultRecovery reverts -> recovery reserve insolvent
}
```

Relevant code: `requestInstantWithdraw` (`contracts/strategies/idle/IdleCreditVault.sol:356`), `claimInstantWithdrawRequest` (`:380`), `defaultPendingClaimBasis` (`:644`), `_defaultPrefundedInstantReserve` (`:716`), `finalizeDefaultRecovery` (`:661`), `_claimDefaultedInstantWithdrawRequest` (`:842`).

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L356-375)
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
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L380-393)
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
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L644-649)
```text
  function defaultPendingClaimBasis() public view returns (uint256 basis) {
    basis = pendingWithdraws;
    if (pendingInstantWithdraws != 0) {
      basis += instantWithdrawClaimsByEpoch[epochNumber];
    }
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L685-696)
```text
    uint256 prefundedReserve = _defaultPrefundedInstantReserve();
    uint256 reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve;
    // Recovery can be above par if the recovered funds exceed the computed basis.
    uint256 recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;

    defaultRecoveryFinalized = true;
    defaultRecoveryReserve = reserveAmount;
    defaultRecoveryPrice = recoveryPrice;
    defaultRecoveryEpoch = epochNumber;
    // A non-zero pending instant bucket means current-epoch instant receipts were not fully funded
    // and must be paid through the same recovery ratio as normal pending receipts.
    defaultInstantWithdrawsFinalized = pendingInstantWithdraws != 0;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L716-723)
```text
  function _defaultPrefundedInstantReserve() internal view returns (uint256 prefundedReserve) {
    uint256 pendingInstant = pendingInstantWithdraws;
    if (pendingInstant == 0) return prefundedReserve;
    uint256 instantBasis = instantWithdrawClaimsByEpoch[epochNumber];
    if (instantBasis > pendingInstant) {
      prefundedReserve = instantBasis - pendingInstant;
    }
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L842-856)
```text
  function _claimDefaultedInstantWithdrawRequest(address _user) internal returns (uint256 claimBasis) {
    uint256 defaultEpoch = defaultRecoveryEpoch;
    claimBasis = instantWithdrawsRequestsByEpoch[_user][defaultEpoch];
    if (claimBasis == 0) return claimBasis;

    instantWithdrawsRequestsByEpoch[_user][defaultEpoch] = 0;
    instantWithdrawsRequests[_user] -= claimBasis;
    uint256 pending = pendingInstantWithdraws;
    // `pendingInstantWithdraws` is only the unfunded remainder. If this user's claim is larger,
    // the extra amount was already counted as prefunded reserve during default finalization.
    pendingInstantWithdraws = claimBasis >= pending ? 0 : pending - claimBasis;
    instantWithdrawClaimsByEpoch[defaultEpoch] -= claimBasis;
    _burn(_user, claimBasis);
    _transferDefaultRecovery(_user, (claimBasis * defaultRecoveryPrice) / RECOVERY_FULL);
  }
```
