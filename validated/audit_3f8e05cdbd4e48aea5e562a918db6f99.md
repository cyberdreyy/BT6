### Title
Instant-withdraw receipts pay out without checking that funds were collected, draining underlyings reserved for funded withdraw claims - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary

`claimInstantWithdrawRequest` burns the user's instant-withdraw receipt and transfers `_amount` of underlyings from the vault's raw token balance without checking that `pendingInstantWithdraws` has actually been funded via `collectInstantWithdrawFunds`. This mirrors the external bug class: the payout path reads a limiter (the receipt balance) but never validates the allowance side (funded balance), so an unfunded receipt can be settled against underlyings that belong to other users' funded normal withdraw requests.

### Finding Description

When an epoch starts with a sufficiently lower APR, `IdleCDOEpochVariant._requestWithdraw` routes the withdrawal through `IdleCreditVault.requestInstantWithdraw`, which mints receipt tokens and increases `instantWithdrawsRequests[_user]` and `pendingInstantWithdraws` immediately [1](#0-0) . The borrower funds these receipts only later, when the manager calls `getInstantWithdrawFunds` → `collectInstantWithdrawFunds`, which decrements `pendingInstantWithdraws` and pulls tokens from the CDO [2](#0-1) .

The claim path, however, never consults `pendingInstantWithdraws` or any per-user funded marker. It burns the receipt and calls `_transferFundedClaim`, which only guards `defaultRecoveryReserve` and otherwise sends whatever balance the vault holds [3](#0-2) [4](#0-3) . The CDO-side wrapper only checks the global `allowInstantWithdraw` flag [5](#0-4) .

Meanwhile, funded normal withdraw receipts sit as raw underlyings in the same vault between `collectWithdrawFunds` (at `stopEpoch`) and the moment users call `claimWithdrawRequest`; `pendingWithdraws` tracks the obligation but `_transferFundedClaim` does not segregate it [6](#0-5) .

Attack sequence (running epoch, instant-withdraw mode):

1. Epoch N ends with `stopEpoch`; borrower funds pending normal receipts via `collectWithdrawFunds`. Vault now holds underlyings earmarked for pending claimants who have not yet claimed.
2. Epoch N+1 starts (`startEpoch`) with a lower APR so `lastEpochApr > currentApr + instantWithdrawAprDelta` [7](#0-6) .
3. An attacker holding tranche tokens calls `requestWithdraw`, which mints an instant receipt and increases `pendingInstantWithdraws` — no funds moved to the vault.
4. Before the next `startEpoch`/`getInstantWithdrawFunds` collection, the attacker calls `claimInstantWithdrawRequest`. The receipt is burned and the vault transfers underlyings that were funded for other users' epoch-N receipts.

### Impact Explanation

Direct theft / insolvency: the attacker is paid from underlyings reserved for funded pending withdraw receipts. When those users later call `claimWithdrawRequest`, `_transferFundedClaim` either transfers less than owed or reverts on insufficient balance, breaking the "one receipt, one payout" and solvency invariants. Loss equals the attacker's instant receipt amount, capped by the vault's unclaimed funded balance.

### Likelihood Explanation

Requires instant-withdraw mode to be enabled and an APR drop across `startEpoch`, plus at least one unclaimed funded receipt in the vault at the moment of the attack — a common state since claims are lazy. The attacker only needs tranche tokens (an ordinary KYC'd lender). No privileged role is involved; the gap exists purely because the claim path doesn't verify `pendingInstantWithdraws` coverage.

### Recommendation

Track a funded-instant counter (e.g., increase a `fundedInstantWithdraws` in `collectInstantWithdrawFunds`) and make `claimInstantWithdrawRequest` revert unless the user's receipt is covered by collected funds, or settle instant claims against `pendingInstantWithdraws` only after funding. Alternatively, segregate balances per bucket so instant claims can never spend normal-receipt reserves.

### Proof of Concept

A Foundry fork test would: (1) run an epoch to `stopEpoch` with an unclaimed pending normal withdraw request funded via `collectWithdrawFunds`; (2) `startEpoch` with `setAprs` lowering `unscaledApr` by more than `instantWithdrawAprDelta`; (3) have a second tranche holder call `cdoEpoch.requestWithdraw` (routed to `requestInstantWithdraw`) and immediately `claimInstantWithdrawRequest`; (4) assert the attacker received underlyings while `pendingInstantWithdraws` remains unfunded and the first user's `claimWithdrawRequest` now reverts or underpays. I was unable to fully verify within this session whether `allowInstantWithdraw` or an upstream guard blocks claims before funding is collected (e.g., a flag flipped only after `getInstantWithdrawFunds`); if such a gate exists, this path is already protected and the finding does not hold.

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

**File:** contracts/IdleCDOEpochVariant.sol (L761-769)
```text
    if (_isInstantWithdrawEnabled()) {
      uint256 currentApr = creditVault.unscaledApr();
      if (lastEpochApr > (currentApr + instantWithdrawAprDelta)) {
        // burn strategy tokens from cdo and mint an equal amount to msg.sender as receipt
        creditVault.requestInstantWithdraw(_underlyings, msg.sender);
        // burn tranche tokens and decrease NAV
        _withdrawOps(_amount, _underlyings, _tranche);
        return _underlyings;
      }
```

**File:** contracts/IdleCDOEpochVariant.sol (L973-979)
```text
  /// @notice Claim an instant withdraw request from the vault. Can be done when epoch is running
  /// as funds will get transferred from borrower when epoch starts
  function claimInstantWithdrawRequest() external {
    // Check that instant withdraws are available
    _checkNotAllowed(!allowInstantWithdraw);
    IdleCreditVault(strategy).claimInstantWithdrawRequest(msg.sender);
  }
```
