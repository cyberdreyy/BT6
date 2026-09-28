### Title
Pending `requestWithdraw` executes under admin-changed epoch parameters, forcing users into epoch-locked withdrawals or reduced payouts - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
Direct analog of the NounsBuilder auction-settings issue: a user sends `requestWithdraw` based on the currently visible parameters (`instantWithdrawAprDelta`, `unscaledApr`, `trancheAPRSplitRatio`, fee params). While the transaction is in the mempool, the honest owner/manager updates those parameters — all setters apply immediately to the *current* buffer period rather than the next epoch — so the user's transaction is mined under different rules. The user's tranche tokens are burned and the withdrawal receipt is crystallized at the new, worse terms, with no way to revert the request once `epochWithdrawPrice`/pending claims are set.

### Finding Description
`requestWithdraw` decides between two materially different paths at execution time:

- Instant mode: burns tranche tokens immediately and routes the request to `creditVault.requestInstantWithdraw` when `lastEpochApr > (currentApr + instantWithdrawAprDelta)` [1](#0-0) 
- Normal mode: computes `_underlyings` from `_calcInterestWithdrawRequest`, which prices the receipt off the *current* `epochDuration`, `bufferPeriod`, strategy APR (`_calcInterest` → `_getStrategyApr`), and `trancheAPRSplitRatio` [2](#0-1) [3](#0-2) 

All of those inputs are mutable by owner/manager *during the buffer period*, when `requestWithdraw` is open (`allowAAWithdrawRequest`/`allowBBWithdrawRequest` are true and the contract is unpaused between `stopEpoch` and `startEpoch`):

- `setInstantWithdrawParams` only reverts when `paused()`, and the contract is unpaused in the buffer [4](#0-3) 
- `setEpochParams` changes `epochDuration`/`bufferPeriod`, which directly scale `_interest` in `_calcInterestWithdrawRequest` and `depositDuringEpoch` [5](#0-4) 
- `requestWithdraw` snapshots `_trancheToUnderlyings` and `_calcInterestWithdrawRequest` at mine-time, not at intent-time [6](#0-5) 

Concretely, in the buffer phase:

1. `lastEpochApr = 10%`, `unscaledApr = 8%`, `instantWithdrawAprDelta = 1%` → instant withdraw is eligible (`10 > 8 + 1`). A user sends `requestWithdraw` expecting an instant withdrawal claimable after `instantWithdrawDelay`.
2. Before the tx mines, the manager calls `setInstantWithdrawParams(delay, 5%, true)` or raises `unscaledApr` via `IdleCreditVault.setAprs` so the delta check fails.
3. The user's tx mines in *normal* mode: tranche tokens are burned, the receipt is queued behind the full next epoch (`epochDuration`, e.g. 30–90 days) instead of `instantWithdrawDelay` (~100s), and management fees for remaining buffer + next epoch are charged upfront against the receipt [7](#0-6) 

Symmetrically, lowering `unscaledApr` or extending `epochDuration` mid-mempool shrinks `_interest` and `diff`, so the user locks a smaller `_underlyings` receipt than the quote they saw — identical to the bidder who would have bid less had they known the new `minBidIncrement`.

### Impact Explanation
- **Temporary freezing**: user expecting an instant exit is locked for a full `epochDuration` + buffer with no self-serve escape for that tranche-token receipt once the epoch price is set (queue `deleteWithdrawRequest` only exists for queue-based requests, and is blocked once `epochWithdrawPrice`/`isEpochWithdrawZero` is set).
- **Direct loss**: reduced `unscaledApr`/changed `trancheAPRSplitRatio`/raised fees shrink `principal + interest - totalFees` at crystallization; the burned tranche tokens are gone and `pendingWithdrawFees` is fixed at request time.

### Likelihood Explanation
Requires only the honest owner/manager executing a routine parameter update (APR renegotiation, instant-withdraw policy change, fee change) ordering before a user's pending `requestWithdraw` in the same buffer window — no malicious privileged action, no attacker capability beyond sending a normal user tx. Buffer periods are explicitly designed as the open window for withdrawals, so user txs and admin maintenance naturally interleave there.

### Recommendation
Apply parameter changes to the *next* epoch/buffer rather than the live request path: snapshot `instantWithdrawAprDelta`, `unscaledApr`, `trancheAPRSplitRatio`, and fee params at `stopEpoch` (buffer start), or add `minExpectedUnderlyings`/`allowInstantOnly` slippage parameters to `requestWithdraw` so users can bound the terms their burned tranche tokens are converted at.

### Proof of Concept
Foundry fork PoC sketch (buffer phase, post-`stopEpoch`):

```solidity
// setup: deposits done, epoch stopped, buffer active, instant withdraws enabled
cdoEpoch.setInstantWithdrawParams(100, 1e18, false); // delta = 1%
// lastEpochApr=10e18, strategy.unscaledApr()=8e18 -> instant path eligible

uint256 trancheBal = AAtranche.balanceOf(user);
vm.prank(user);
// user simulates: requestWithdraw returns instantly-claimable receipt
// --- mempool gap: manager updates params before user tx mines ---
vm.prank(manager);
cdoEpoch.setInstantWithdrawParams(100, 1e18, true); // disableInstantWithdraw = true

vm.prank(user);
uint256 got = cdoEpoch.requestWithdraw(trancheBal, address(AAtranche));
// got < quoted: normal path, upfront mgmt fee charged, receipt locked for full epochDuration
assertGt(strategy.withdrawsRequests(address(cdoEpoch)), 0);
// user's tranche tokens already burned; no instant claim exists
assertEq(AAtranche.balanceOf(user), 0);
```

**Caveat**: I verified the mutable inputs and the mine-time pricing in `requestWithdraw`, `setInstantWithdrawParams`, and `setEpochParams`, but could not fully confirm the absence of an `isEpochRunning`/epoch-gate on `IdleCreditVault.setAprs` and `IdleCDO.setTrancheAPRSplitRatio` (test evidence at `test/foundry/IdleCreditVault.t.sol:1836` shows `setAprs` callable by manager outside stopEpoch). If those setters are buffer-gated, the `setInstantWithdrawParams` mode-flip variant still stands on its own, since that setter is only blocked while the contract is paused (i.e., during a running epoch) and is freely callable in the buffer where `requestWithdraw` is open.

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L117-125)
```text
  function setEpochParams(uint256 _epochDuration, uint256 _bufferPeriod) public {
    _checkOnlyOwnerOrManager();
    // cannot set epoch params if epoch is running
    // cannot set epochDuration to 0 as it's reserved for closing the pool
    // and cannot set epochDuration if previously was set to 0 as borrower repaid all funds
    _checkNotAllowed(defaulted || isEpochRunning || _epochDuration == 0 || epochDuration == 0);
    epochDuration = _epochDuration;
    bufferPeriod = _bufferPeriod;
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L131-137)
```text
  function setInstantWithdrawParams(uint256 _delay, uint256 _aprDelta, bool _disable) public virtual {
    _checkOnlyOwnerOrManager();
    _checkNotAllowed(paused());
    instantWithdrawDelay = _delay;
    instantWithdrawAprDelta = _aprDelta;
    disableInstantWithdraw = _disable;
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L756-790)
```text
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

    uint256 principal = _underlyings;
    (uint256 interest, int256 diff) = _calcInterestWithdrawRequest(_underlyings, _tranche);
    uint256 totalFees = _totalWithdrawFees(principal, interest);
    // user is requesting principal + interest minus upfront management fee and net performance fee
    _underlyings = principal + interest - totalFees;
    // add expected fees to pending withdraw fees counter
    pendingWithdrawFees += totalFees;

    /// if there is an AA withdrawal the overperformance that the amount withdrawed would have generated for BB tranches
    /// is saved in interestForOverUnderPerformance. This is used to calculate the interest that should be added to the
    /// expectedEpochInterest at the startEpoch.
    /// If there is a BB withdrawal this amount is subtracted from the expectedEpochInterest
    interestForOverUnderPerformance += diff;

    // The receipt is fixed now and leaves live NAV. Charge management fees upfront
    // for the time it waits outside live NAV: remaining buffer plus the next epoch.
    creditVault.requestWithdraw(_underlyings, msg.sender, principal);
    // burn tranche tokens and decrease NAV without interest for the next epoch as it was not yet counted in NAV
    _withdrawOps(_amount, principal, _tranche);
```

**File:** contracts/IdleCDOEpochVariant.sol (L856-877)
```text
  function _calcInterestWithdrawRequest(uint256 _amount, address _tranche) internal view returns (uint256 _interest, int256 _diff) {
    uint256 _duration = epochDuration;
    if (_duration == 0) {
      return (_interest, _diff);
    }

    uint256 _buffer = bufferPeriod;
    // calculate total vault interest (they don't get the interest for the buffer period for withdraw requests so 
    // we scale it back since _calcInterest is scaling the interest with tht buffer period),
    uint256 totInterest = _calcInterest(_managedContractValue()) * _duration / (_duration + _buffer);
    // calculate total tranche interest for the whole tranche supply
    uint256 totTrancheInterest = _calcTrancheInterestShare(totInterest, _tranche);
    // calculate interest for the given tranche and given amount
    uint256 _trancheBal = _lastSavedNAV(_tranche);
    _interest = _trancheBal == 0 ? 0 : _amount * totTrancheInterest / _trancheBal;
    // calculate the interest that the _amount would have received if there was no split ratio (ie interest split based only on tvl).
    // This is used to calculate the interest that should be added to the expectedEpochInterest when 
    // withdrawing an AA tranche or the interest that should be removed from expectedEpochInterest when
    // withdrawing a BB tranche
    uint256 interestWithoutSplitRatio = _calcInterest(_amount) * _duration / (_duration + _buffer);
    // difference between total interest and tranche interest (positive for AA, negative for BB)
    _diff = int256(interestWithoutSplitRatio) - int256(_interest);
```
