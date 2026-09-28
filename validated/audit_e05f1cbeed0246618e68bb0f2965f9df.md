### Title
Instant-withdraw claims skip the loss-adjustment and funding checks enforced on normal withdraw receipts - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
The TLS 1.3 bug class — a validation wired to the legacy path but not ported to a newer parallel path — maps directly onto `IdleCreditVault`. Normal withdraw receipts requested through `requestWithdraw` and claimed through `claimWithdrawRequest` are subjected to loss haircut accounting (`lossRecoveryPriceByEpoch`, `previewLossAdjustedWithdrawFunds`, `collectWithdrawFunds`) and to a guard in `requestWithdraw` that blocks a new request while a loss-adjusted receipt is unclaimed. The parallel instant-withdraw path (`requestInstantWithdraw` / `claimInstantWithdrawRequest` / `collectInstantWithdrawFunds`) implements none of these checks: it burns the user's full receipt and pays out at par, with no per-epoch loss price and no reconciliation that the receipt was actually funded.

### Finding Description
- Normal receipts: `collectWithdrawFunds` applies a pro-rata loss when the borrower under-funds `pendingWithdraws`, storing `lossRecoveryPriceByEpoch[epochNumber]` [1](#0-0) . `requestWithdraw` reverts if the user has an unclaimed loss-adjusted receipt so `lastWithdrawRequest` keeps pointing at the haircut epoch [2](#0-1) . `claimWithdrawRequest` routes through `_claimLossAdjustedWithdrawRequest`, which pays `claimBasis * lossRecoveryPrice / 1e18` [3](#0-2) .
- Instant receipts: `requestInstantWithdraw` mints a 1:1 receipt and bumps `instantWithdrawsRequests`/`pendingInstantWithdraws` with no loss-epoch guard at all [4](#0-3) . `collectInstantWithdrawFunds` only decrements `pendingInstantWithdraws` and pulls underlying — there is no `lossRecoveryPrice` equivalent, so partial funding is not tracked per epoch [5](#0-4) . `claimInstantWithdrawRequest` then burns the entire `instantWithdrawsRequests[_user]` and calls `_transferFundedClaim` for the full par amount, with no check that the request was funded and no haircut [6](#0-5) .

### Impact Explanation
When the CDO collects only part of the instant-withdraw bucket (e.g., the same `stopEpochWithDuration(_lossAmount)` scenario that haircuts normal receipts, or a partially funded `getInstantWithdrawFunds`), the strategy holds fewer underlyings than outstanding instant receipts. `claimInstantWithdrawRequest` still pays the full receipt. `_transferFundedClaim` only protects `defaultRecoveryReserve`, not other users' claims [7](#0-6) . The first claimants drain funded normal-withdraw underlyings at par; later claimants (normal receipt holders whose funds were collected via `collectWithdrawFunds`) are left unpaid — a direct redistribution/insolvency of the claim pool. Attacker needs only a tranche position and to race honest claimants, both unprivileged actions.

### Likelihood Explanation
Requires a loss or partial-funding event on an epoch with outstanding instant receipts — the same trigger condition the protocol itself handles for normal receipts via `previewLossAdjustedWithdrawFunds`, so the scenario is in the intended operating envelope, not exotic [8](#0-7) . The asymmetry is purely a missing port of the check onto the newer instant path.

### Recommendation
Mirror the normal-receipt accounting on the instant path: record a per-epoch recovery price in `collectInstantWithdrawFunds` when `_amount` under-funds `pendingInstantWithdraws`, store the request epoch for instant receipts, and apply the haircut in `claimInstantWithdrawRequest` / `_claimDefaultedInstantWithdrawRequest` analogously to `_claimLossAdjustedWithdrawRequest` [9](#0-8) .

### Proof of Concept
Foundry fork PoC outline (mirroring `test/foundry/IdleCreditVault.t.sol` helpers): deposit AA, run epoch 0, request an instant withdraw for attacker and a normal withdraw for victim; start epoch; fund only part of both buckets (`getInstantWithdrawFunds` partial / `stopEpochWithDuration` with `_lossAmount`); call `cdoEpoch.claimInstantWithdrawRequest()` as attacker — receives full par amount — then `claimWithdrawRequest()` as victim reverts or pays the reduced remainder on haircut price while the attacker was paid in full.

Caveat: I could not fully trace the CDO-side instant funding path (`getInstantWithdrawFunds` internals and whether partial instant funding is reachable in every epoch mode) within the search budget; the finding rests on `claimInstantWithdrawRequest`'s unconditional par payout and the absence of any instant-side loss price in `IdleCreditVault.sol`.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L259-271)
```text
    bool isClosed = IIdleCDOEpochVariant(idleCDO).epochEndDate() == 0;
    uint256 currentEpoch = epochNumber;
    uint256 lossEpoch = lastWithdrawRequest[_user];
    uint256 lossRecoveryPrice = lossRecoveryPriceByEpoch[lossEpoch];
    if (
      lossRecoveryPrice != 0 &&
      (withdrawsRequestsByEpoch[_user][lossEpoch] != 0 ||
      (apr0Users[_user].principal != 0 && apr0Users[_user].principalEpoch == lossEpoch))
    ) {
      // A loss-adjusted receipt must be claimed before opening a later request, otherwise
      // `lastWithdrawRequest` would stop pointing to the epoch that stores its haircut.
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L411-430)
```text
  function collectWithdrawFunds(uint256 _amount) external {
    _onlyIdleCDO();
    uint256 pendingBasis = pendingWithdraws;
    if (_amount < pendingBasis) {
      // Legacy receipts do not have per-epoch ownership data, so they can only be fully funded.
      if (!defaultRecoveryInitialized) revert NotAllowed();
      uint256 lossRecoveryPrice = _amount * RECOVERY_FULL / pendingBasis;
      // Avoid storing a zero price, which is indistinguishable from "no loss-adjusted epoch".
      if (lossRecoveryPrice == 0) revert NotAllowed();
      pendingWithdraws = 0;
      lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice;
    } else {
      // A plain implementation upgrade may leave legacy normal receipts pending. Their next
      // successful stop can fully fund the aggregate before lazy initialization occurs.
      pendingWithdraws = pendingBasis - _amount;
    }
    if (_amount != 0) {
      underlyingToken.safeTransferFrom(idleCDO, address(this), _amount);
    }
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L440-460)
```text
  function previewLossAdjustedWithdrawFunds(uint256 _lossAmount) external view returns (uint256 pendingToFund, uint256 activeLoss) {
    uint256 pendingBasis = pendingWithdraws;
    // Full zero-loss funding is safe for legacy aggregate receipts and needs no migration call.
    if (_lossAmount == 0) return (pendingBasis, _lossAmount);

    IIdleCDOEpochVariant cdo = IIdleCDOEpochVariant(idleCDO);
    uint256 activeBasis = _lossActiveBasis(cdo);
    if (pendingBasis == 0) {
      if (_lossAmount > activeBasis) revert NotAllowed();
      return (0, _lossAmount);
    }

    // Legacy pending receipts do not have the per-epoch ownership data needed to store a haircut.
    if (!defaultRecoveryInitialized) revert NotAllowed();
    uint256 totalBasis = activeBasis + pendingBasis;
    if (_lossAmount >= totalBasis) revert NotAllowed();

    uint256 pendingLoss = _lossAmount * pendingBasis / totalBasis;
    pendingToFund = pendingBasis - pendingLoss;
    activeLoss = _lossAmount - pendingLoss;
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L789-801)
```text
  function _claimLossAdjustedWithdrawRequest(address _user) internal returns (uint256 amount) {
    uint256 lossEpoch = lastWithdrawRequest[_user];
    uint256 lossRecoveryPrice = lossRecoveryPriceByEpoch[lossEpoch];
    if (lossRecoveryPrice == 0) return amount;

    (uint256 claimBasis, uint256 burnAmount) = _clearWithdrawClaimForEpoch(_user, lossEpoch, false);
    if (claimBasis == 0) return amount;

    // pendingWithdraws was already cleared when the borrower funded the loss-adjusted amount.
    _burn(_user, burnAmount);
    amount = (claimBasis * lossRecoveryPrice) / RECOVERY_FULL;
    _transferFundedClaim(_user, amount);
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
