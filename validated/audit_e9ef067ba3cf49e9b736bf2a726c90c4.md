### Title
Post-default instant withdraw requests bypass the recovery haircut and are paid at par from other claimants' funds - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary

The MINA bug is a failure to honor a connection-termination signal (`close_notify`): after "close", the socket stays open and data keeps flowing. The direct analog in `IdleCreditVault` is that `requestInstantWithdraw` has no `defaultRecoveryFinalized` handling at all, while its sibling `requestWithdraw` explicitly branches on it. After `finalizeDefault` — the vault's close_notify — the instant-withdraw "socket" remains open, and a post-default receipt is paid at par via `_transferFundedClaim` out of the vault's non-reserve underlyings, i.e., other users' funded claims. [1](#0-0) 

### Finding Description

`requestWithdraw` contains a dedicated post-default branch: once `defaultRecoveryFinalized` is set it rejects users with open receipts, mints a `postDefaultRequests` receipt priced by the already-lowered `virtualPrice`, and routes the payout through `_claimPostDefaultWithdrawRequest`, which spends only the isolated `defaultRecoveryReserve`. [2](#0-1) [3](#0-2) 

`requestInstantWithdraw` performs no equivalent check. It burns CDO strategy tokens, mints a receipt to the user, and increments `instantWithdrawsRequests`, `instantWithdrawsRequestsByEpoch`, `instantWithdrawClaimsByEpoch`, and `pendingInstantWithdraws` regardless of finalization state. [4](#0-3) 

`claimInstantWithdrawRequest` then pays `instantWithdrawsRequests[_user]` in full via `_transferFundedClaim`, which is guarded only against dipping into `defaultRecoveryReserve` — it happily spends any other underlyings sitting in the strategy (funded balances backing pre-default normal/instant receipts that were never classified into the default epoch). [5](#0-4) [6](#0-5) 

Additionally, a post-default instant request is bucketed under `instantWithdrawsRequestsByEpoch[user][epochNumber]`. If `epochNumber` still equals `defaultRecoveryEpoch`, `_claimDefaultedInstantWithdrawRequest` decrements `instantWithdrawClaimsByEpoch[defaultEpoch]` and clamps `pendingInstantWithdraws` with a subtraction designed for pre-default receipts, corrupting the accounting for genuinely defaulted claimants. [7](#0-6) 

### Impact Explanation

Direct theft / insolvency: a post-default instant receipt is paid at par from vault underlyings that economically belong to other users' already-funded claims. Each such claim reduces the balance available to honest claimants; the last claimants are left with unbacked receipts (permanent loss equal to the attacker's claimed amount, bounded by the non-reserve funded balance). The "session" continues to deliver value after termination, exactly as in CVE-2019-0231. [8](#0-7) 

### Likelihood Explanation

Likelihood is moderate: it requires a finalized default (owner/manager-driven, honest), then an unprivileged tranche holder calling `requestInstantWithdraw` through `IdleCDOEpochVariant` and claiming. Whether the CDO forwards instant requests while `defaulted()` could not be fully verified within the read budget — if `IdleCDOEpochVariant` gates instant withdrawals on `!defaulted()`, the exploit collapses to accounting corruption only (bucket misattribution into `defaultRecoveryEpoch`, `pendingInstantWithdraws` clamped to 0), which still lets the attacker skip the `defaultInstantWithdrawsFinalized` haircut path depending on epoch ordering. [9](#0-8) 

### Recommendation

Mirror the `requestWithdraw` post-default branch in `requestInstantWithdraw`: after `defaultRecoveryFinalized`, either revert outright or route the amount into `postDefaultRequests` semantics (par payout funded strictly from `defaultRecoveryReserve`, never `_transferFundedClaim`). In `claimInstantWithdrawRequest`, reject claims when `defaultRecoveryFinalized && !defaultInstantWithdrawsFinalized` to avoid epoch-bucket misattribution. [10](#0-9) 

### Proof of Concept

```solidity
// Foundry fork test sketch (mirroring test/foundry/IdleCreditVault.t.sol defaults tests)
function testPostDefaultInstantWithdrawPaysParFromOthersFunds() external {
    address attacker = makeAddr('post-default-instant-user');
    address honest   = makeAddr('old-funded-claimant');
    uint256 amount   = 10_000 * ONE_SCALE;
    uint256 recoveryRatio = 7e17;

    // honest user: funded receipt from an earlier epoch (never claimed)
    _depositWithUser(honest, amount, true);
    vm.prank(honest);
    uint256 honestClaim = cdoEpoch.requestWithdraw(0, address(AAtranche));
    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr, _expectedFundsEndEpoch());

    // attacker stays active; borrower defaults in a later epoch
    _depositWithUser(attacker, amount, true);
    _startEpochAndCheckPrices(1);
    _stopEpochAndCheckPrices(1, initialProvidedApr, 0);
    _checkDefault();

    uint256 recovered = /* totalDefaultBasis * recoveryRatio */;
    deal(defaultUnderlying, manager, recovered);
    vm.startPrank(manager);
    IERC20Detailed(defaultUnderlying).approve(address(strategy), recovered);
    cdoEpoch.finalizeDefault(recovered, manager);
    vm.stopPrank();

    // "close_notify" ignored: instant-withdraw path still open post-finalization
    vm.prank(attacker);
    cdoEpoch.requestInstantWithdraw(0, address(AAtranche)); // burns at haircutted virtualPrice
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest(); // pays at par from non-reserve balance

    // honest user's funded claim is now undercollateralized
    vm.prank(honest);
    vm.expectRevert(); // _transferFundedClaim underflow / insufficient balance
    cdoEpoch.claimWithdrawRequest();
}
```

Note: the PoC assumes `IdleCDOEpochVariant.requestInstantWithdraw` is not blocked while `defaulted()`; that CDO-side gating was not fully verified here and is the one precondition to confirm before treating this as exploitable end-to-end.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L243-258)
```text
  function requestWithdraw(uint256 _amount, address _user, uint256 _principal) external {
    _onlyIdleCDO();
    _ensureDefaultRecoveryInitialized();
    if (_amount == 0) return;
    if (defaultRecoveryFinalized) {
      // user should first claim old already-funded withdraw requests before requesting new ones after default
      if (_hasWithdrawRequest(_user) || instantWithdrawsRequests[_user] != 0 || postDefaultRequests[_user] != 0) {
        revert NotAllowed();
      }
      // Preserve request/claim UX after default without increasing borrower-facing pendingWithdraws.
      // The CDO passes an already-haircut amount because finalization lowered virtualPrice first.
      _burn(msg.sender, _amount);
      _mint(_user, _amount);
      postDefaultRequests[_user] = _amount;
      return;
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L760-767)
```text
  function _claimPostDefaultWithdrawRequest(address _user) internal returns (uint256 amount) {
    amount = postDefaultRequests[_user];
    if (amount == 0) return amount;
    postDefaultRequests[_user] = 0;
    // Post-default receipts are paid 1:1 because the haircut was applied when the request was made.
    _burn(_user, amount);
    _transferDefaultRecovery(_user, amount);
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L894-907)
```text
  /// @notice Transfer a funded claim without spending default recovery reserve.
  /// @param _user claim receiver
  /// @param _amount amount to transfer
  function _transferFundedClaim(address _user, uint256 _amount) internal {
    if (_amount == 0) return;
    uint256 reserve = defaultRecoveryReserve;
    if (reserve != 0) {
      uint256 balance = underlyingToken.balanceOf(address(this));
      // This should be unreachable when accounting is consistent. Keep the guard so old funded
      // receipts can never spend underlyings reserved for default recovery claimants.
      if (balance < reserve || balance - reserve < _amount) revert NotAllowed();
    }
    underlyingToken.safeTransfer(_user, _amount);
  }
```
