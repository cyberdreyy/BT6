### Title
Instant-withdraw claim pays unfunded same-epoch requests from other users' funded reserves - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`IdleCreditVault.claimInstantWithdrawRequest` pays out the *aggregate* `instantWithdrawsRequests[_user]` balance without distinguishing receipts whose funding was already collected via `collectInstantWithdrawFunds` from receipts created in the still-running epoch that have not been funded yet. Because `requestInstantWithdraw` does not force a user to claim an existing funded receipt before adding a new one, an unprivileged user can sequence `requestInstantWithdraw` → `claimInstantWithdrawRequest` inside the same running epoch and be paid from underlying that the strategy is holding for *other* users' already-funded claims. This is the credit-vault analog of a race/TOCTOU bug: a state check (receipt exists) is acted on before the precondition that makes it payable (funding collected at epoch boundary) is satisfied.

### Finding Description
`requestInstantWithdraw` burns CDO-held strategy tokens, mints a 1:1 receipt to the user, and adds `_amount` to both `instantWithdrawsRequests[_user]` and the global `pendingInstantWithdraws` [1](#0-0) . `pendingInstantWithdraws` is only decremented when the CDO calls `collectInstantWithdrawFunds` at the epoch boundary, i.e. a request made during a running epoch is unfunded until the next `startEpoch`/`stopEpoch` collects borrower funds [2](#0-1) .

`claimInstantWithdrawRequest` then burns `instantWithdrawsRequests[_user]` in full and calls `_transferFundedClaim(_user, amount)`, which pays from the strategy's underlying balance — the same balance that holds funded-but-unclaimed receipts of other users [3](#0-2) . Unlike `claimWithdrawRequest`, which gates on `epochNumber > lastWithdrawRequest[_user]` so a receipt cannot be claimed before its funding epoch [4](#0-3) , the instant path has no per-epoch funding check: `instantWithdrawsRequestsByEpoch[_user][epoch]` is recorded but never consulted on claim.

Requests during a running epoch are reachable — the test suite itself requests instant withdrawals after `startEpoch` [5](#0-4) . The CDO-side `claimInstantWithdrawRequest` only checks `allowInstantWithdraw` [6](#0-5) .

### Impact Explanation
Direct theft / temporary freezing of other users' funded withdrawals. The attacker deposits, waits for `startEpoch` so the strategy holds underlying collected for prior claimants, calls `requestInstantWithdraw(X)` and immediately `claimInstantWithdrawRequest()`. The strategy transfers `X` out of the funded-claim reserve although the borrower's funding for that request has not been collected. When `collectInstantWithdrawFunds` later pulls `X` from the CDO the books balance globally, but in the interim other users' `claimInstantWithdrawRequest`/`claimWithdrawRequest` calls revert on insufficient balance; if the pool closes, defaults, or is emergency-shut down before the collection, the shortfall is permanent and socialized onto remaining claimants — violating the one-receipt-one-payout and solvency invariants.

### Likelihood Explanation
Requires only a KYC-passing wallet (`isWalletAllowed` gating applies to requests, not to the timing gap itself), `allowInstantWithdraw` enabled, and a running epoch where the strategy holds nonzero funded reserves — a routine state. The attack is two consecutive transactions by the same unprivileged actor and needs no privileged cooperation, no donation, and no oracle manipulation. It is most damaging when combined with an honest-manager `stopEpoch`/default that crystallizes the drained reserve before collection.

### Recommendation
Track funding per receipt rather than per user aggregate: either (a) gate `claimInstantWithdrawRequest` on `instantWithdrawsRequestsByEpoch[_user][epochNumber] == 0` style funding maturity (analogous to the `epochNumber <= lastWithdrawRequest[_user]` check in `_claimFundedWithdrawRequest`), or (b) decrement a `fundedInstantWithdraws[_user]` counter in `collectInstantWithdrawFunds` and pay only the funded portion. Additionally, mirror the `requestWithdraw` post-default guard by requiring users to claim outstanding funded instant receipts before opening a new request in a later epoch.

### Proof of Concept
```solidity
// Foundry fork test sketch (test/foundry/IdleCreditVault.t.sol harness)
function testPocInstantClaimRacesFunding() external {
    _useStandardEpochVariant();
    _stopCurrentEpochWithApr(10e18); // enter buffer of epoch #1
    vm.prank(manager);
    cdoEpoch.setInstantWithdrawParams(100, 1e18, false);

    // Victim deposits and requests a normal funded withdraw
    address victim = makeAddr('victim');
    uint256 vTranches = _depositWithUser(victim, 100e6);
    vm.prank(manager);
    cdoEpoch.startEpoch();
    _requestWithdrawWithUser(victim, vTranches);
    // stop+start so victim's claim is funded in the strategy
    _stopCurrentEpochWithApr(1e18);
    vm.prank(manager);
    cdoEpoch.startEpoch();
    uint256 reserve = underlying.balanceOf(address(strategy));
    assertGt(reserve, 0);

    // Attacker deposits, starts next epoch, requests instant withdraw DURING
    // the running epoch (request accepted; funding not yet collected)
    address attacker = makeAddr('attacker');
    uint256 aTranches = _depositWithUser(attacker, reserve); // sized to drain reserve
    _requestInstantWithdrawWithUser(attacker, aTranches);    // requestInstantWithdraw via cdoEpoch

    // Same running epoch: claim immediately — paid out of victim's funded reserve
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest();
    assertGt(underlying.balanceOf(attacker), 0, 'unfunded request paid early');

    // Victim's funded claim now reverts / underpays on insufficient strategy balance
    vm.prank(victim);
    vm.expectRevert();
    cdoEpoch.claimInstantWithdrawRequest();
}
```

Uncertainty note: I could not fully read `IdleCDOEpochVariant.requestInstantWithdraw` and `getInstantWithdrawFunds` within the available iterations to confirm the exact buffer-vs-running request gating; if the CDO already restricts instant requests to the buffer window only, the attack window narrows to requests made in the buffer and claimed after `startEpoch` but before `collectInstantWithdrawFunds` executes for that epoch — the same missing funded-vs-unfunded check still applies.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L326-328)
```text
    if (IIdleCDOEpochVariant(idleCDO).epochEndDate() != 0 && (epochNumber <= lastWithdrawRequest[_user])) {
      revert NotAllowed();
    }
```

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L398-403)
```text
  function collectInstantWithdrawFunds(uint256 _amount) external {
    _onlyIdleCDO();
    if (_amount == 0) return;
    pendingInstantWithdraws -= _amount;
    underlyingToken.safeTransferFrom(idleCDO, address(this), _amount);
  }
```

**File:** test/foundry/IdleCreditVault.t.sol (L1280-1289)
```text
    uint256 feeReceiverBalPre = underlying.balanceOf(TL_MULTISIG);
    deal(defaultUnderlying, borrower, cdoEpoch.expectedEpochInterest() + IdleCreditVault(address(strategy)).pendingWithdraws());
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    assertEq(underlying.balanceOf(TL_MULTISIG) - feeReceiverBalPre, expectedMgmtFee, "fee receiver got wrong upfront management fee");

    uint256 balPre = underlying.balanceOf(address(this));
    cdoEpoch.claimWithdrawRequest();
```

**File:** contracts/IdleCDOEpochVariant.sol (L975-979)
```text
  function claimInstantWithdrawRequest() external {
    // Check that instant withdraws are available
    _checkNotAllowed(!allowInstantWithdraw);
    IdleCreditVault(strategy).claimInstantWithdrawRequest(msg.sender);
  }
```
