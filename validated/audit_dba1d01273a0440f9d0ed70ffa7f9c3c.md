### Title
Late instant-withdraw requests after `getInstantWithdrawFunds` steal already-funded claims of earlier withdrawers - (File: contracts/IdleCDOEpochVariant.sol / contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The upstream bug is a delayed work item that can still be scheduled *after* `flush_workqueue()` returns, so it runs on freed state. The direct analog in idle-tranches is `requestWithdraw` → `requestInstantWithdraw`: instant-withdraw receipts can be created **after** the one-shot funding event (`getInstantWithdrawFunds` / `startEpoch`'s `collectInstantWithdrawFunds`) has already snapshotted and funded `pendingInstantWithdraws`. Because `allowInstantWithdraw` stays `true` and `claimInstantWithdrawRequest` pays out from the vault's aggregate underlying balance rather than a per-epoch/per-receipt bucket, a receipt created after the funding flush is paid out of underlyings that were collected to satisfy *earlier* instant and normal withdraw receipts.

### Finding Description
- `getInstantWithdrawFunds()` pulls `pendingInstantWithdraws` from the borrower once, sets `allowInstantWithdraw = true`, and drives `pendingInstantWithdraws` to 0 via `collectInstantWithdrawFunds` [1](#0-0) [2](#0-1) .
- `requestWithdraw` has no `isEpochRunning` gate — only `allowAA/BBWithdrawRequest` and `isWalletAllowed` — so during a running epoch with a decreased APR it routes to `requestInstantWithdraw`, minting a receipt and increasing `pendingInstantWithdraws` again [3](#0-2) [4](#0-3) .
- `claimInstantWithdrawRequest` burns the user's receipt and calls `_transferFundedClaim` for the full `instantWithdrawsRequests[_user]` with no check that this receipt was actually funded — the vault's underlying balance is a shared pool of funds collected for prior claimants and matured normal receipts [5](#0-4) .
- The same gap exists at `startEpoch`: `collectInstantWithdrawFunds(pendingInstant)` funds only the snapshot taken at that block; any request made in the same epoch afterwards relies on `getInstantWithdrawFunds`, which is only callable once per epoch window and only by owner/manager — yet nothing stops a request *after* it has run [6](#0-5) .

This is exactly the upstream pattern: `getInstantWithdrawFunds`/`collectInstantWithdrawFunds` is the `flush_workqueue()` — it only covers requests queued before it ran — while `requestInstantWithdraw` remains "schedulable" afterwards and `claimInstantWithdrawRequest` executes the late work against funds belonging to earlier claimants.

### Impact Explanation
An unprivileged KYC'd tranche holder deposits (or already holds tranches), waits for `getInstantWithdrawFunds` to fund all pending instant receipts, then calls `requestWithdraw(0, tranche)` in the same running epoch (APR-decreased mode) and immediately `claimInstantWithdrawRequest()`. The claim transfers vault underlyings earmarked for earlier instant withdrawers who have not yet claimed, and for matured normal receipts funded via `collectWithdrawFunds`. Net effect: direct theft of other users' funded-but-unclaimed withdrawals up to the attacker's deposit size; victims' later claims revert on insufficient balance (permanent loss for them until the next funding event, if any).

### Likelihood Explanation
Requires instant-withdraw mode active (APR decreased by more than `instantWithdrawAprDelta` at stopEpoch), an epoch running past `instantWithdrawDeadline`, and the attacker holding or depositing tranches. All attacker actions are ordinary user calls; no privileged cooperation needed beyond the manager's routine `getInstantWithdrawFunds` call. The stolen amount is bounded by the attacker's tranche value and the vault's funded-but-unclaimed balance.

### Recommendation
Analogous to the kernel fix (`disable_delayed_work_sync` before freeing), close the scheduling window after the funding flush:
- In `requestWithdraw`/`requestInstantWithdraw`, reject new instant requests once `allowInstantWithdraw` is true (funding already collected for this epoch), or timestamp receipts by epoch and make `claimInstantWithdrawRequest` only pay receipts whose epoch was funded.
- Alternatively gate `requestWithdraw` to the buffer period (`!isEpochRunning`) for the instant path.

### Proof of Concept
Foundry fork test sketch (extend `test/foundry/IdleCreditVault.t.sol` pattern):

```solidity
function testLateInstantRequestStealsFundedClaims() external {
    uint256 amount = 10_000 * ONE_SCALE;
    idleCDO.depositAA(amount);          // victim
    idleCDO.depositBB(amount);          // attacker liquidity

    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr / 2, _expectedFundsEndEpoch());

    // victim requests instant withdraw (APR dropped)
    uint256 victimReq = cdoEpoch.requestWithdraw(0, address(AAtranche));

    _startEpochAndCheckPrices(1);
    vm.warp(block.timestamp + cdoEpoch.instantWithdrawDelay() + 1);
    _getInstantFunds();                 // funding "flush": pendingInstantWithdraws -> 0
    // victim does NOT claim yet

    // attacker (second user with BB tranches) requests AFTER funding
    uint256 attackerReq = cdoEpoch.requestWithdraw(0, address(BBtranche));
    assertGt(IdleCreditVault(address(strategy)).pendingInstantWithdraws(), 0);

    // attacker claims immediately, paid from funds collected for the victim
    cdoEpoch.claimInstantWithdrawRequest();

    // victim's funded claim now reverts / underpays
    vm.expectRevert();
    cdoEpoch.claimInstantWithdrawRequest(); // victim: strategy balance short by attackerReq
}
```

Note: I could not fully verify whether a separate guard elsewhere (e.g., `allowAAWithdrawRequest` being cleared during a running epoch, or per-epoch claim funding inside `_transferFundedClaim`) already blocks the post-funding request; the visible code in `requestWithdraw`, `requestInstantWithdraw`, and `claimInstantWithdrawRequest` contains no such check, so the PoC above should be run to confirm exploitability before reporting.

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L279-292)
```text
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

**File:** contracts/IdleCDOEpochVariant.sol (L739-770)
```text
  function requestWithdraw(uint256 _amount, address _tranche) external returns (uint256 _underlyings) {
    _checkTranche(_tranche);
    _checkNotAllowed(
      (_tranche == AATranche ? !allowAAWithdrawRequest : !allowBBWithdrawRequest) ||
      !isWalletAllowed(msg.sender)
    );
  
    // we check if there are donated assets to the pool and transfer them to the feeReceiver if any
    _skimDonatedAssets();

    // we trigger an update accounting to check for eventual losses
    _updateAccounting();

    IdleCreditVault creditVault = IdleCreditVault(strategy);
    if (_amount == 0) {
      _amount = _userTrancheBal(msg.sender, _tranche);
    }
    _underlyings = _trancheToUnderlyings(_amount, _tranche);

    // Programmable borrower deployments do not support instant withdrawals.
    // If apr decresed wrt last epoch, request instant withdraw and burn tranche tokens directly
    // we compare unscaled aprs
    if (_isInstantWithdrawEnabled()) {
      uint256 currentApr = creditVault.unscaledApr();
      if (lastEpochApr > (currentApr + instantWithdrawAprDelta)) {
        // burn strategy tokens from cdo and mint an equal amount to msg.sender as receipt
        creditVault.requestInstantWithdraw(_underlyings, msg.sender);
        // burn tranche tokens and decrease NAV
        _withdrawOps(_amount, _underlyings, _tranche);
        return _underlyings;
      }
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
