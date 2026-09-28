### Title
A single unfunded instant-withdraw request permanently bricks `finalizeDefaultRecovery` on an uninitialized vault, blocking all default recovery - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`_ensureDefaultRecoveryInitialized()` reverts whenever `pendingInstantWithdraws != 0`, and every path that could clear that counter (`collectInstantWithdrawFunds`, `claimInstantWithdrawRequest` post-finalization, or default recovery claims) is unreachable until initialization succeeds or the borrower fully funds it. An unprivileged attacker can keep a dust-sized instant receipt permanently unfunded, so `finalizeDefaultRecovery()` — the only way to crystallize and distribute borrower-default recovery — reverts forever on a vault where `defaultRecoveryInitialized` is still `false`.

### Finding Description
The JOJO analog is: a position entry that cannot be removed (delisted perp) keeps `openPositions[]` non-empty, so `handleBadDebt()` can never run. In idle-tranches, the equivalent "removable-by-design but attacker-pinnable" state is `pendingInstantWithdraws` in `IdleCreditVault`.

`requestInstantWithdraw` lets any tranche holder create an instant receipt during a running epoch and bumps `pendingInstantWithdraws` [1](#0-0) . The counter is only decremented by `collectInstantWithdrawFunds` (called from `IdleCDOEpochVariant.getInstantWithdrawFunds`, which requires the borrower to actually transfer) or cleared per-user via `_claimDefaultedInstantWithdrawRequest` — which only runs after `defaultRecoveryFinalized` [2](#0-1) .

The lazy initializer hard-reverts while that counter is non-zero [3](#0-2) :

```solidity
if (pendingInstantWithdraws != 0) revert NotAllowed();
```

and it is invoked at the top of `finalizeDefaultRecovery` [4](#0-3) . So on any vault that has not yet initialized recovery accounting (freshly upgraded `IdleCreditVault` where `defaultRecoveryInitialized == false`), the sequence is:

1. Epoch running, instant withdraws enabled. Attacker (KYC'd tranche holder) calls `requestInstantWithdraw` for 1 wei of shares → `pendingInstantWithdraws = 1`.
2. Borrower fails to fund instant requests → `getInstantWithdrawFunds` catches the failure and `_handleBorrowerDefault` sets `defaulted = true` and pauses [5](#0-4) .
3. Owner/manager calls `finalizeDefault(recovered, source)` → `finalizeDefaultRecovery` → `_ensureDefaultRecoveryInitialized` → revert on `pendingInstantWithdraws != 0`.
4. `pendingInstantWithdraws` can never return to 0: `collectInstantWithdrawFunds` is only reachable pre-default via `getInstantWithdrawFunds` (now blocked: `isEpochRunning == false` makes its `_checkNotAllowed` revert), and the defaulted-claim path is gated behind `defaultRecoveryFinalized`.

Recovery is permanently impossible; `defaultRecoveryReserve` stays 0, tranche prices never crystallize the loss, and every LP claim path (`_claimDefaultedWithdrawRequest`, `_claimDefaultedInstantWithdrawRequest`, post-default `requestWithdraw`) is dead.

### Impact Explanation
Permanent freezing of all recovered funds and all depositor claims after a borrower default. Even a 1-wei instant receipt bricks `finalizeDefault`, so the entire recovery reserve (potentially the whole pool NAV) can never be distributed — a strict analog of JOJO's "insurance can never collect bad debt."

### Likelihood Explanation
Requires (a) `defaultRecoveryInitialized == false` (true until the first call that initializes it — i.e., any upgraded vault that hasn't yet touched a recovery-aware path) and (b) an unfunded instant request at default time. The attacker controls the dust request; the only honest-actor dependency is a default occurring while the request is still pending, which is exactly the scenario `pendingInstantWithdraws != 0` in `defaultPendingClaimBasis` was designed to handle — but that handling is unreachable because initialization is checked first.

### Recommendation
Allow `_ensureDefaultRecoveryInitialized` to proceed when `pendingInstantWithdraws != 0` once `cdo.defaulted()` is true (the instant bucket is already handled by `defaultPendingClaimBasis`/`_defaultPrefundedInstantReserve`), or add a manager path to write off/clear pending instant requests so initialization — and therefore finalization — cannot be pinned by an unclaimed dust receipt.

### Proof of Concept
Foundry fork PoC sketch (mirroring `testFinalizeDefaultWithZeroRecoveryPrice` setup, with instant withdraws enabled via `setInstantWithdrawParams`):

```solidity
// assume strategy.defaultRecoveryInitialized() == false (upgraded vault)
cdoEpoch.setInstantWithdrawParams(delay, delta, true); // manager
_depositWithUser(attacker, amount, true);
// buffer end; manager starts epoch
cdoEpoch.startEpoch();                                 // manager
vm.prank(attacker);
cdoEpoch.requestInstantWithdraw(1, address(AAtranche)); // pendingInstantWithdraws = 1
// borrower cannot fund instant withdrawals
deal(underlying, borrower, 0);
vm.warp(cdoEpoch.instantWithdrawDeadline() + 1);
cdoEpoch.getInstantWithdrawFunds();                    // manager -> default
assertTrue(cdoEpoch.defaulted());
// recovery finalization is now impossible forever
vm.expectRevert(NotAllowed.selector);
cdoEpoch.finalizeDefault(recovered, manager);          // owner
// pendingInstantWithdraws can never be cleared:
// getInstantWithdrawFunds reverts (epoch not running),
// claim paths revert (defaultRecoveryFinalized == false)
```

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L382-403)
```text
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

  /// @notice collect the instant withdraw funds
  /// @dev only IdleCDO can call this function
  /// @param _amount number of tokens to collect
  function collectInstantWithdrawFunds(uint256 _amount) external {
    _onlyIdleCDO();
    if (_amount == 0) return;
    pendingInstantWithdraws -= _amount;
    underlyingToken.safeTransferFrom(idleCDO, address(this), _amount);
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L661-667)
```text
  function finalizeDefaultRecovery(uint256 _recoveredAmount, address _recoverySource) external returns (uint256 defaultBBNav) {
    _onlyIdleCDO();
    _ensureDefaultRecoveryInitialized();

    IIdleCDOEpochVariant cdo = IIdleCDOEpochVariant(idleCDO);
    if (defaultRecoveryFinalized || !cdo.defaulted()) revert NotAllowed();
    if (_recoveredAmount != 0 && _recoverySource == address(0)) revert NotAllowed();
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L924-935)
```text
  function _ensureDefaultRecoveryInitialized() internal {
    if (defaultRecoveryInitialized) return;
    if (pendingInstantWithdraws != 0) revert NotAllowed();
    if (pendingWithdraws != 0) {
      IIdleCDOEpochVariant cdo = IIdleCDOEpochVariant(idleCDO);
      if (cdo.epochEndDate() != 0 || cdo.defaulted()) revert NotAllowed();
      pendingWithdraws = 0;
      apr0TotalPrincipal = 0;
    }
    defaultRecoveryInitialized = true;
    canTransfer = false;
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L566-599)
```text
    try this.getFundsFromBorrower(_instantWithdraws) {
      // transfer funds to IdleCreditVault and decrease pendingInstantWithdraws
      _strategy.collectInstantWithdrawFunds(_instantWithdraws);
      // allow instant withdraws
      allowInstantWithdraw = true;
    } catch {
      _handleBorrowerDefault(_instantWithdraws);
    }
  }

  /// @notice Handle borrower default
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
  }
```
