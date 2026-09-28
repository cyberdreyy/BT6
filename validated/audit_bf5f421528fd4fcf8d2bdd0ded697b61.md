### Title
Instant-withdraw requests bypass management and performance fees charged on normal withdraw requests - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
Analogous to the Footium issue (EIP2981 royalty implemented on `FootiumPlayer` but missing on `FootiumClub`, letting sellers bypass protocol fees), `IdleCDOEpochVariant.requestWithdraw` charges upfront management and net performance fees on ordinary withdraw receipts, but the instant-withdraw branch exits early and skips `_totalWithdrawFees` entirely. Users exiting through the instant-withdraw path pay no protocol fees even though their receipt still waits outside live NAV before being claimable.

### Finding Description
In `requestWithdraw` (`contracts/IdleCDOEpochVariant.sol:739`), a normal request computes `totalFees = _totalWithdrawFees(principal, interest)` and deducts them from the user's receipt (`_underlyings = principal + interest - totalFees`), crediting `pendingWithdrawFees` for the protocol [1](#0-0) .

However, when the APR dropped enough to trigger instant withdrawals, the same function takes an early branch: `creditVault.requestInstantWithdraw(_underlyings, msg.sender)` followed by `_withdrawOps` and `return`, never calling `_totalWithdrawFees` [2](#0-1) .

The fee rationale applies equally to both paths. `_withdrawRequestManagementFeeDuration` documents that management fees are charged upfront because "receipts leave live NAV at request time but can be claimed only after" a waiting period [3](#0-2) . Instant receipts also leave live NAV (NAV is decreased via `_withdrawOps`) yet sit unclaimable in `pendingInstantWithdraws` until funds are pulled via `getInstantWithdrawFunds` after `instantWithdrawDelay` [4](#0-3) . The funded claim pays the full gross `amount` with no fee deduction [5](#0-4) .

### Impact Explanation
Every instant withdrawal avoids the management fee that an identically-sized ordinary request would pay on principal for the receipt's out-of-NAV waiting period (`_calculateManagementFee(_principal, duration)`), and any performance fee on projected interest. With a 1% management fee and, e.g., a 35-day epoch + buffer duration, a user instant-withdrawing ~10,000 units saves ~10 units of fees that the protocol collects from ordinary requesters. Repeated instant exits across epochs compound the fee loss; the protocol's `pendingWithdrawFees`/`unclaimedFees` revenue is reduced proportionally to instant-withdraw volume.

### Likelihood Explanation
The path is reachable by any KYC-passed tranche holder whenever `lastEpochApr > currentApr + instantWithdrawAprDelta` after a stopEpoch — a routine state (APR cuts are expected to trigger mass instant exits, precisely when volume, and thus bypassed fees, is highest). No privileged collusion is needed; the attacker just calls `requestWithdraw` in the buffer phase. Whether this is "intended" is doubtful given the symmetric accounting: `_withdrawOps` removes principal from NAV in both branches identically, so the fee-waiting rationale holds for both.

### Recommendation
Apply a management fee on the instant-withdraw principal for the period the receipt waits outside live NAV (at minimum `instantWithdrawDelay`, or the remaining-buffer duration used for normal requests) inside the instant branch of `requestWithdraw`, crediting it to `pendingWithdrawFees` or `unclaimedFees` before `_withdrawOps`, mirroring `_totalWithdrawFees(principal, 0)`.

### Proof of Concept
Foundry fork scenario (mirroring `testRequestWithdrawInstant` in `test/foundry/IdleCreditVault.t.sol:3627`):

```solidity
// Setup: fee params set with nonzero managementFee, deposit AA + BB,
// start epoch 0, stopEpoch with apr lowered by > instantWithdrawAprDelta.
uint256 trancheReq = IERC20(AAtranche).balanceOf(user);
uint256 principal = trancheReq * cdoEpoch.tranchePrice(address(AAtranche)) / 1e18;

uint256 requested = cdoEpoch.requestWithdraw(0, address(AAtranche)); // takes instant branch

// Instant path: no fee charged
assertEq(requested, principal);                       // gross principal returned
assertEq(cdoEpoch.pendingWithdrawFees(), 0);          // nothing accrued for protocol

// Compare: a normal request of the same size would return
// principal - _calculateManagementFee(principal, _withdrawRequestManagementFeeDuration())
// with pendingWithdrawFees > 0
```

A valid PoC asserts `requested == principal` (no haircut) while `_totalWithdrawFees(principal, 0)` for the same principal is nonzero, demonstrating the fee bypass.

### Citations

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

**File:** contracts/IdleCDOEpochVariant.sol (L772-778)
```text
    uint256 principal = _underlyings;
    (uint256 interest, int256 diff) = _calcInterestWithdrawRequest(_underlyings, _tranche);
    uint256 totalFees = _totalWithdrawFees(principal, interest);
    // user is requesting principal + interest minus upfront management fee and net performance fee
    _underlyings = principal + interest - totalFees;
    // add expected fees to pending withdraw fees counter
    pendingWithdrawFees += totalFees;
```

**File:** contracts/IdleCDOEpochVariant.sol (L920-931)
```text
  /// @notice Get the duration used for upfront management fees on withdrawal receipts.
  /// @dev Receipts leave live NAV at request time but can be claimed only after the
  /// next epoch settles. Requests made during the buffer also pay for the remaining
  /// buffer time before that next epoch can start.
  /// @return _duration One epoch plus any remaining buffer before the next epoch.
  function _withdrawRequestManagementFeeDuration() private view returns (uint256 _duration) {
    uint256 bufferEnd = epochEndDate + bufferPeriod;
    _duration = epochDuration;
    if (block.timestamp < bufferEnd) {
      _duration += bufferEnd - block.timestamp;
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L387-392)
```text
    uint256 amount = instantWithdrawsRequests[_user];
    // burn strategy tokens from user
    _burn(_user, amount);

    instantWithdrawsRequests[_user] = 0;
    _transferFundedClaim(_user, amount);
```
