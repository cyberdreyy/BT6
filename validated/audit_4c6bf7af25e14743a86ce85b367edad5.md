### Title
Instant-withdraw claims skip the unfunded-request gate and drain the funded normal-withdraw reserve — (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The SurrealDB bug is a "two read paths, one filter" bug: the direct `SELECT` path applied field-level permission filtering while the graph/reference traversal path materialized the same records through a shared helper that only enforced the coarser table-level check. The credit-vault analog is the same shape: funded-claim payout is split into two paths — `_claimFundedWithdrawRequest` for normal receipts and `claimInstantWithdrawRequest` for instant receipts — and only the normal path enforces the epoch/funding bookkeeping. The instant path burns and pays the full `instantWithdrawsRequests[_user]` aggregate without checking that the current-epoch instant bucket was actually funded (`pendingInstantWithdraws`), letting an instant receipt spend underlying that `collectWithdrawFunds` reserved for normal withdraw claimants.

### Finding Description
In `IdleCreditVault`, `requestInstantWithdraw` burns the CDO's strategy tokens, mints a receipt to the user, and records `instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch[_user][epoch]`, and `pendingInstantWithdraws` [1](#0-0) . Funding is a separate step: the CDO later calls `collectInstantWithdrawFunds`, which decrements `pendingInstantWithdraws` and pulls the underlying in [2](#0-1) . The code itself acknowledges that instant requests can be only partially funded ("partially prefunded instant requests … borrower-send funds that failed at epoch start") in `_defaultPrefundedInstantReserve` [3](#0-2) .

However, `claimInstantWithdrawRequest` pays out the entire `instantWithdrawsRequests[_user]` balance via `_transferFundedClaim` with no check that the claim's epoch slice was funded — it never reads `pendingInstantWithdraws` or `instantWithdrawClaimsByEpoch` on the non-default path [4](#0-3) . `_transferFundedClaim` only protects `defaultRecoveryReserve`, not the underlying collected by `collectWithdrawFunds` for normal pending receipts [5](#0-4) . So the "funding" filter applied to the normal path (`pendingWithdraws` / `collectWithdrawFunds` / per-epoch gating in `_claimFundedWithdrawRequest` [6](#0-5) ) is skipped entirely on the instant traversal of the same claim surface — the direct analog of field-level filtering being skipped on the graph-traversal read path.

### Impact Explanation
An unprivileged lender (KYC-passing, tranche holder) requests an instant withdraw during the buffer phase. If the epoch then starts with the instant queue only partially prefunded — which the codebase explicitly models as possible — the attacker calls `claimInstantWithdrawRequest` through the CDO and receives underlying drawn from the strategy's funded normal-withdraw reserve. The loss is bounded by the attacker's instant request size and is paid 1:1 out of funds owed to normal withdraw claimants: direct theft / temporary freezing of other users' funded claims, quantified as `min(instantRequest, fundedReserve)`. If the pool subsequently defaults, `defaultPendingClaimBasis` adds the still-pending instant basis again (`instantWithdrawClaimsByEpoch[epoch]` is only decremented inside `_claimDefaultedInstantWithdrawRequest`), so the same receipt is also counted in the recovery basis — a double-counting of the claim.

### Likelihood Explanation
Requires `allowInstantWithdraw` enabled and an epoch where the instant queue is underfunded at start (partial prefunding is an explicitly supported state per `_defaultPrefundedInstantReserve`). The attacker contributes only their own deposit; no privileged collusion is needed — the underfunding can arise from the honest sequence of borrower funding plus competing claims in the same epoch.

### Recommendation
Gate `claimInstantWithdrawRequest` on the funded portion: track a funded-vs-pending split per epoch (e.g., only allow claims up to `instantWithdrawClaimsByEpoch[epoch] - pendingInstantWithdraws` attribution, or mark per-epoch funded receipts when `collectInstantWithdrawFunds` runs) and revert or haircut unfunded instant claims, mirroring the `pendingWithdraws`/`collectWithdrawFunds` accounting used for normal receipts.

### Proof of Concept
Foundry fork sketch (against `test/foundry/IdleCreditVault.t.sol` harness):
1. Victim deposits AA, calls `requestWithdraw`; epoch stops successfully so the victim's receipt is funded via `collectWithdrawFunds` (strategy now holds their payout).
2. Attacker deposits AA, calls `requestInstantWithdraw` in the next buffer phase.
3. Epoch starts; instant queue is only partially funded (borrower transfer covers less than `instantWithdrawClaimsByEpoch`), leaving `pendingInstantWithdraws > 0` — a state the code explicitly supports.
4. Attacker calls `cdoEpoch.claimInstantWithdrawRequest()`; `claimInstantWithdrawRequest` burns the receipt and `_transferFundedClaim` pays the full amount out of the strategy balance, consuming the victim's funded reserve.
5. Assert victim's subsequent `claimWithdrawRequest` reverts or pays less than their funded basis; assert `defaultPendingClaimBasis()` still counts the attacker's epoch instant basis if a default is then finalized.

Caveat: I could not fully trace `IdleCDOEpochVariant.startEpoch`/`getInstantWithdrawFunds` to confirm every underfunding sequence reachable by an unprivileged attacker; if startEpoch guarantees full instant funding in all honest-borrower configurations, the residual impact reduces to the recovery-basis double-count noted above.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L319-350)
```text
  function _claimFundedWithdrawRequest(address _user) internal returns (uint256 amount) {
    // User should wait at least an epoch before claiming the withdraw. Once the epoch is over user can withdraw 
    // at any time even if a new epoch started. 
    // So if epochNumber is the same as the last withdraw request then we revert. Epoch number is increased at stopEpoch
    // NOTE: If a user does not claim a withdraw request and instead requests another withdraw, he will have to wait
    // for another epoch to claim both requests.
    // NOTE 2: if borrower defaults, old withdraw requests can still be claimed
    if (IIdleCDOEpochVariant(idleCDO).epochEndDate() != 0 && (epochNumber <= lastWithdrawRequest[_user])) {
      revert NotAllowed();
    }
    // settle APR=0 requests once the related epoch has ended
    _settleApr0(_user);
    Apr0UserData storage _apr0User = apr0Users[_user];
    // Claim includes:
    // - settled APR0 principal from finalized epochs
    // - still-open APR0 principal: if pool-close mode was used (_interest == 1), IdleCDO sets
    //   epochEndDate = 0 and claims can be immediate, while _settleApr0 can still skip settlement
    //   for the current request epoch (reqEpoch >= epochNumber).
    // - settled APR0 interest
    uint256 normalAmount = withdrawsRequests[_user];
    uint256 apr0PrincipalAmount = _apr0User.settledPrincipal + _apr0User.principal;
    uint256 apr0InterestAmount = _apr0User.settledInterest;
    amount = normalAmount + apr0PrincipalAmount + apr0InterestAmount;
    // burn strategy tokens 1:1 with the principal only (normal amount already includes interest)
    _burn(_user, normalAmount + apr0PrincipalAmount);
    withdrawsRequests[_user] = 0;
    lastWithdrawRequest[_user] = 0;
    if (apr0PrincipalAmount != 0 || apr0InterestAmount != 0) {
      delete apr0Users[_user];
    }
    _transferFundedClaim(_user, amount);
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L897-907)
```text
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
