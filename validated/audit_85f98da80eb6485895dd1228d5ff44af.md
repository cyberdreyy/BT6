### Title
Claimed instant-withdraw receipts are never removed from `instantWithdrawClaimsByEpoch`, inflating the default-recovery claim basis and diluting/locking recovery funds - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The CVE-2017-8348 analog is a resource/state "leak": data that should be released on use is retained forever. In `IdleCreditVault`, the normal-path claim `claimInstantWithdrawRequest` burns the user's aggregate receipt and zeroes `instantWithdrawsRequests[_user]`, but it never clears `instantWithdrawsRequestsByEpoch[_user][epoch]` and never decrements the epoch aggregate `instantWithdrawClaimsByEpoch[epoch]` [1](#0-0) . That epoch aggregate is only decreased inside `_claimDefaultedInstantWithdrawRequest` [2](#0-1) . As a result, `instantWithdrawClaimsByEpoch[epochNumber]` permanently accumulates every instant receipt ever created in the epoch — including ones already fully paid out.

### Finding Description
`defaultPendingClaimBasis` adds `instantWithdrawClaimsByEpoch[epochNumber]` to the defaulted-claim basis whenever `pendingInstantWithdraws != 0` at finalization [3](#0-2) . `finalizeDefaultRecovery` then computes `recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis`, where `totalBasis = activeBasis + pendingBasis` [4](#0-3) .

Sequence in a running epoch that later defaults:

1. Attacker (any KYC-passing lender / tranche holder) calls `requestInstantWithdraw` via the CDO, creating an instant receipt. `instantWithdrawClaimsByEpoch[N] += amount` [5](#0-4) .
2. The borrower funds instant withdrawals; `collectInstantWithdrawFunds` pulls cash and decrements `pendingInstantWithdraws` [6](#0-5) . Attacker calls `claimInstantWithdrawRequest` and is paid in full — but `instantWithdrawClaimsByEpoch[N]` and `instantWithdrawsRequestsByEpoch[attacker][N]` stay non-zero.
3. Later in the same epoch, honest users create instant requests that remain partially unfunded (`pendingInstantWithdraws != 0`). Borrower defaults; the CDO calls `finalizeDefaultRecovery`.
4. `defaultPendingClaimBasis()` counts the attacker's already-paid receipt again, inflating `totalBasis` and lowering `recoveryPrice` for every defaulted receipt and for active NAV (`activeFinalNAV = activeBasis * recoveryPrice / RECOVERY_FULL`).
5. Because each honest claimant's payout is `claimBasis * recoveryPrice / RECOVERY_FULL` drawn from a fixed `defaultRecoveryReserve` [7](#0-6) , the phantom basis leaves an amount equal to `staleClaims * recoveryPrice / RECOVERY_FULL` permanently stranded as unclaimable recovery dust in the strategy.

The same leak also inflates `_defaultPrefundedInstantReserve` (`instantBasis - pendingInstant`) [8](#0-7) , double-counting underlyings already paid to claimed users as if they were still reserved — further distorting `reserveAmount` and the minted/burned active-NAV adjustment at lines 699–705.

### Impact Explanation
- Recovery price is depressed proportionally to the stale (already-paid) instant claims: `recoveryPrice` falls by roughly `staleInstantClaims / totalBasis`, transferring recovery value away from honest defaulted receipt holders and active LPs.
- The corresponding reserve share is never claimed — the phantom claimants no longer hold receipts (`instantWithdrawsRequestsByEpoch[user][epoch]` for the paid user is only cleared on the defaulted-claim path for `defaultRecoveryEpoch`, and their balance there still registers a basis they already consumed). The leftover `defaultRecoveryReserve` dust is permanently frozen in the contract, matching the "permanent freezing of unclaimed funds" acceptance criterion.
- Quantified loss: up to the full cumulative claimed instant volume of the default epoch × recovery price; e.g., with 1M of claimed instant receipts and 1M of genuine basis, the recovery price is halved and ~half of the instant-share reserve is stranded.

### Likelihood Explanation
- Attacker needs no privilege: instant withdraw requests and claims are open to any allowed wallet during a running epoch.
- The trigger is ordinary protocol flow — funded-and-claimed instant withdrawals followed by a same-epoch borrower default with some instant requests still unfunded. No malicious privileged role is required; the honest manager/borrower sequence (fund instant queue → borrower default) is sufficient.
- No existing guard stops it: `_ensureDefaultRecoveryInitialized`, `_transferFundedClaim` reserve checks, and epoch gating all operate on the stale values as-is.

Caveat: I confirmed the missing decrement by inspection — `instantWithdrawClaimsByEpoch` is written only at lines 372 (increment) and 853 (default-path decrement), and `instantWithdrawsRequestsByEpoch` is only cleared at line 847 for `defaultRecoveryEpoch`. If `collectInstantWithdrawFunds`/CDO-side flow guarantees `pendingInstantWithdraws == 0` whenever any instant claim has been paid (i.e., funding is strictly all-or-nothing per epoch), the inflated-basis branch would be unreachable; a fork PoC should confirm partial-funding ordering before default.

### Recommendation
- In `claimInstantWithdrawRequest`, locate the request epoch (store it alongside `instantWithdrawsRequests`, or iterate the user's epoch key) and clear `instantWithdrawsRequestsByEpoch[_user][epoch]` and decrement `instantWithdrawClaimsByEpoch[epoch]` by the claimed amount, mirroring `_claimDefaultedInstantWithdrawRequest`.
- Alternatively, decrement `instantWithdrawClaimsByEpoch` inside `collectInstantWithdrawFunds` so the epoch aggregate only ever reflects unfunded receipts.
- Add a regression test: instant request → fund → claim → borrower default in the same epoch → assert `defaultPendingClaimBasis` excludes the claimed receipt and `defaultRecoveryReserve` is fully consumable.

### Proof of Concept
```solidity
// SPDX-License-Identifier: MIT
// Foundry fork test against deployed IdleCDOEpochVariant + IdleCreditVault
// Flow (all unprivileged calls through the CDO):
function testStaleInstantClaimsInflateDefaultBasis() public {
    // 1. depositAA as attacker, epoch running
    // 2. attacker -> cdoEpoch.requestInstantWithdraw -> requestInstantWithdraw on strategy
    //    assert instantWithdrawClaimsByEpoch[epochNumber] == amt
    // 3. manager/borrower funds instant queue: borrower repays -> getInstantWithdrawFunds
    //    -> collectInstantWithdrawFunds (pendingInstantWithdraws -= amt)
    // 4. attacker -> claimInstantWithdrawRequest: paid in full
    //    assert instantWithdrawClaimsByEpoch[epochNumber] == amt   // BUG: not decremented
    // 5. honest user repeats step 2 for amt2, left unfunded (pendingInstantWithdraws = amt2)
    // 6. borrower defaults: cdoEpoch.stopEpoch path -> _handleBorrowerDefault
    // 7. owner/manager calls finalizeDefault / finalizeDefaultRecovery(r, source)
    //    assert strategy.defaultPendingClaimBasis() == pendingWithdraws + amt + amt2
    //    // expected: pendingWithdraws + amt2 only
    // 8. assert defaultRecoveryPrice < expectedPrice
    //    and after all honest claims, underlyingToken.balanceOf(strategy) > 0  // stranded dust
}
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L644-649)
```text
  function defaultPendingClaimBasis() public view returns (uint256 basis) {
    basis = pendingWithdraws;
    if (pendingInstantWithdraws != 0) {
      basis += instantWithdrawClaimsByEpoch[epochNumber];
    }
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L679-692)
```text
    uint256 pendingBasis = defaultPendingClaimBasis();
    uint256 totalBasis = activeBasis + pendingBasis;
    if (totalBasis == 0) revert NotAllowed();

    // Some recovery funds may already be in this strategy: partially prefunded instant requests
    // and borrower-send funds that failed at epoch start. Count both without pulling them again.
    uint256 prefundedReserve = _defaultPrefundedInstantReserve();
    uint256 reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve;
    // Recovery can be above par if the recovered funds exceed the computed basis.
    uint256 recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;

    defaultRecoveryFinalized = true;
    defaultRecoveryReserve = reserveAmount;
    defaultRecoveryPrice = recoveryPrice;
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L909-917)
```text
  /// @notice Transfer default recovery reserve to a user.
  /// @param _user claim receiver
  /// @param _amount amount to transfer
  function _transferDefaultRecovery(address _user, uint256 _amount) internal {
    if (_amount == 0) return;
    // Every defaulted or post-default claim consumes the isolated recovery reserve.
    defaultRecoveryReserve -= _amount;
    underlyingToken.safeTransfer(_user, _amount);
  }
```
