### Title
Dust withdraw request causes `processWithdrawRequests` to revert with `Is0`, permanently freezing all queued withdrawals - (File: contracts/IdleCDOEpochQueue.sol)

### Summary
Analogous to the SurrealDB bug — an uncaught error triggered by crafted input crashing the whole service — a single dust-sized `requestWithdraw` in `IdleCDOEpochQueue` makes the manager/owner-only `processWithdrawRequests()` revert unconditionally. This blocks the entire epoch's queued withdrawals and, since the dust request can be re-created cheaply every epoch by any whitelisted lender, all other users' queued tranche redemptions can be frozen indefinitely.

### Finding Description
In `IdleCDOEpochQueue.processWithdrawRequests()`, the aggregate pending withdrawal `_pending` is forwarded to the CDO and the implied epoch price is computed:

```solidity
uint256 _underlyingsRequested = _cdo.requestWithdraw(_pending, tranche);
uint256 _epochPrice = _underlyingsRequested * ONE_TRANCHE / _pending;
if (_epochPrice == 0) {
  revert Is0();
}
``` [1](#0-0) 

`requestWithdraw` returns strategy tokens 1:1 with underlyings, i.e. `trancheAmount * virtualPrice / ONE_TRANCHE`, rounded down. If the aggregate `_pending` is small enough that the CDO rounds `_underlyingsRequested` to 0 (e.g. a request of ~100 wei of tranche tokens, as demonstrated in `testProcessWithdrawRequestsWith0Price`), the whole call reverts and `epochPendingWithdrawals[_epoch]` is never cleared.

An attacker (any KYC-passing lender / tranche holder) simply calls `queue.requestWithdraw(dustAmount)` while the epoch is running. The dust amount is added to `epochPendingWithdrawals`, and because the revert happens before state cleanup, every subsequent `processWithdrawRequests()` for that epoch reverts too — including for legitimate large requests batched in the same epoch. Nobody else can remove the attacker's entry: `deleteWithdrawRequest` only operates on `msg.sender`'s own request, and neither owner nor manager has a path to evict a single user's dust entry. [2](#0-1) 

### Impact Explanation
Temporary-to-permanent freezing of funds. All other users' queued withdrawals for that epoch cannot be processed (`epochPendingWithdrawals` stays non-zero, `pendingClaims` never gets set, and `claimWithdrawRequest` reverts with `NotAllowed` because `epochWithdrawPrice[_epoch]` stays 0). The attacker loses only ~100 wei of tranche tokens per epoch and can repeat the grief every epoch, freezing the entire queue's redemption pipeline for as long as they keep it up — the exact analog of a crafted request repeatedly crashing the shared service.

### Likelihood Explanation
High feasibility, low cost: requires only a KYC-whitelisted wallet and a dust tranche balance. No privileged cooperation, no oracle manipulation, no timing precision needed — the dust request just needs to land in the same epoch as any pending withdrawals (which is the normal state during a running epoch). The only mitigation is that the attacker must spend a small amount each epoch to sustain the grief.

### Recommendation
Handle the zero-price case without reverting, mirroring the fix already applied in `processWithdrawalClaims` for zero-rounded payouts (`isEpochWithdrawZero`): when `_underlyingsRequested == 0` or `_epochPrice == 0`, record the epoch as a zero-payout epoch (e.g. `isEpochWithdrawZero[_epoch] = true`, `epochWithdrawPrice[_epoch] = 0`), clear `epochPendingWithdrawals[_epoch]`, and let users clear their receipts at zero payout (or refund their tranche tokens). Alternatively, enforce a minimum request size well above the rounding threshold. [3](#0-2) 

### Proof of Concept
Foundry fork PoC (already mirrored by `testProcessWithdrawRequestsWith0Price` in `test/foundry/IdleCDOEpochQueue.t.sol:1431`):

```solidity
// epoch running; victim has queued a real withdraw request
// attacker (whitelisted) deposits dust and requests withdraw of 100 wei tranches
uint256 tranchePrice = cdoEpoch.virtualPrice(address(tranche));
// pick dust so dust * price / ONE_TRANCHE == 0
uint256 dust = (ONE_TRANCHE + tranchePrice - 1) / tranchePrice; // ~1 underlying worth -> but use 100 wei
vm.prank(attacker);
queue.requestWithdraw(100); // 100 wei of tranche tokens

// advance to buffer/stop so requests can be processed
_stopCurrentEpoch();

// manager tries to process ALL pending requests for the epoch -> always reverts
vm.prank(manager);
vm.expectRevert(Is0.selector);
queue.processWithdrawRequests();

// epochPendingWithdrawals[epoch] is never cleared; victims' claims stay frozen
vm.prank(victim);
vm.expectRevert(NotAllowed.selector);
queue.claimWithdrawRequest(epoch);
```

### Citations

**File:** contracts/IdleCDOEpochQueue.sol (L315-322)
```text
    uint256 _underlyingsRequested = _cdo.requestWithdraw(_pending, tranche);
    isEpochInstant[_epoch] = _strategy.instantWithdrawsRequests(address(this)) > _instantWithdraws;
    // save current implied tranche price for this epoch based on underlyings that will be received on claim
    uint256 _epochPrice = _underlyingsRequested * ONE_TRANCHE / _pending;
    if (_epochPrice == 0) {
      revert Is0();
    }
    epochWithdrawPrice[_epoch] = _epochPrice;
```

**File:** contracts/IdleCDOEpochQueue.sol (L355-363)
```text
    if (_received != _pending) {
      uint256 _updatedPrice = epochWithdrawPrice[_epoch] * _received / _pending;
      epochWithdrawPrice[_epoch] = _updatedPrice;
      // A valid aggregate recovery can still round this queue's small share to zero. Record that
      // terminal outcome explicitly so users can clear claims and later epochs are not blocked.
      if (_updatedPrice == 0) {
        isEpochWithdrawZero[_epoch] = true;
      }
    }
```

**File:** test/foundry/IdleCDOEpochQueue.t.sol (L1431-1456)
```text
  function testProcessWithdrawRequestsWith0Price() external {
    // stop epoch #0
    _stopCurrentEpoch();
    // we are now in epoch #1 (epoch starts at the beginning of the buffer period)

    // deposit with user1
    uint256 amount1 = 1e6; // 1 USDC
    address user1 = makeAddr('user1');
    _depositWithUser(user1, amount1);

    // start epoch #1
    vm.prank(manager);
    cdoEpoch.startEpoch();

    // request withdrawals with both users
    _requestWithdrawWithUser(user1, 100);

    // stopEpoch, deposits got some interest
    _stopCurrentEpoch();
    // we are now in epoch #2

    // can't call process deposits with 0 price
    vm.expectRevert(abi.encodeWithSelector(Is0.selector));
    vm.prank(manager);
    queue.processWithdrawRequests();
  }
```
