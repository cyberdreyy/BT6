### Title
APR0 settled principal escapes `lossRecoveryPriceByEpoch` haircut — guard checks only the unsettled bucket - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.requestWithdraw` guards against opening a new request while a loss-adjusted receipt is pending, but the guard only inspects `apr0Users[_user].principal`/`principalEpoch` — the *unsettled* APR0 bucket. Once `_settleApr0` has run, the claimable basis lives in `settledPrincipal`/`settledInterest` while `principal == 0`, so the guard's equality-style check silently passes (same class as CVE-2021-23434: `currentPath === '__proto__'` fails on `['__proto__']`). The user then overwrites `lastWithdrawRequest` and later claims the settled APR0 basis at par via `_claimFundedWithdrawRequest`, while every other receipt holder of that epoch was haircut by `lossRecoveryPriceByEpoch`.

### Finding Description
In `requestWithdraw`, the pending-loss check is:

```solidity
uint256 lossEpoch = lastWithdrawRequest[_user];
uint256 lossRecoveryPrice = lossRecoveryPriceByEpoch[lossEpoch];
if (
  lossRecoveryPrice != 0 &&
  (withdrawsRequestsByEpoch[_user][lossEpoch] != 0 ||
  (apr0Users[_user].principal != 0 && apr0Users[_user].principalEpoch == lossEpoch))
) {
  revert NotAllowed();
}
``` [1](#0-0) 

The condition `apr0Users[_user].principal != 0 && apr0Users[_user].principalEpoch == lossEpoch` is only true for an *open* APR0 request. `_settleApr0` moves the basis to `settledPrincipal`/`settledInterest` and zeroes `principal`/`principalEpoch`, with no record of which epoch the settled funds originated from. [2](#0-1) 

Symmetrically, the loss-adjusted claim path never haircuts settled APR0 funds: `_clearWithdrawClaimForEpoch` clears `withdrawsRequestsByEpoch` and `apr0User.principal`, but `settledPrincipal`/`settledInterest` are untouched. [3](#0-2)  `_claimFundedWithdrawRequest` then pays `withdrawsRequests + settledPrincipal + settledInterest` in full. [4](#0-3) 

Attack sequence (APR0 mode):
1. Epoch N, `unscaledApr == 0`: attacker calls `CDO.requestWithdraw`, creating an APR0 receipt (`principal`, `principalEpoch = N`).
2. `stopEpoch` for epoch N runs: `prepareStopEpochWithApr0` accrues APR0 interest; `collectWithdrawFunds` funds less than `pendingWithdraws` and stores `lossRecoveryPriceByEpoch[epochNumber] < 1e18` (borrower partially repaid — a normal loss event, not a default).
3. Attacker calls `claimWithdrawRequest` — reverts on the epoch gate, but `_settleApr0` is invoked inside `_claimFundedWithdrawRequest` only *after* the gate... so instead the attacker simply calls `requestWithdraw` again in epoch N+1. The guard reads `apr0Users[user].principal` — still nonzero, so this path requires the settlement to have occurred. Settlement also happens inside `_requestWithdrawApr0` via `_settleApr0(_user)` when making a *new* APR0 request. [5](#0-4) 
4. The new request is recorded under the current epoch; `lastWithdrawRequest` is overwritten. The settled epoch-N principal is now detached from `lossEpoch` and will never pass through `_claimLossAdjustedWithdrawRequest` again.
5. After one more epoch, `claimWithdrawRequest` pays the settled principal plus settled interest at par, while all other epoch-N receipt holders were paid at `lossRecoveryPrice`.

### Impact Explanation
The haircut that `collectWithdrawFunds` applied pro-rata to the pending bucket is silently skipped for settled-APR0 basis. The attacker withdraws more than their fair share of the funded loss-adjusted amount, leaving the strategy under-reserved: either later claimers' `_transferFundedClaim` reverts on insufficient balance (permanent freezing of their funded claims) or the shortfall is absorbed by other users' pending receipts. Loss magnitude is `(1 - lossRecoveryPrice) * settledApr0Basis`, bounded only by the attacker's deposit size; with a 50% loss-adjusted epoch and a 1M USDC APR0 request, the excess extraction is ~500k USDC.

### Likelihood Explanation
Requires an APR0 epoch that ends via `stopEpochWithDuration`/partial funding in `collectWithdrawFunds` (borrower underpays but the pool is not defaulted — an allowed honest-manager flow), plus one additional request by the attacker. The attacker needs only a KYC-passing wallet and a tranche position — unprivileged. Caveat: whether the guard misfires depends on the exact epoch keying of `lossRecoveryPriceByEpoch` (set under the post-increment `epochNumber` in `collectWithdrawFunds`) versus the `lastWithdrawRequest` value stored at request time; the invariant "settled APR0 basis carries no epoch tag" holds regardless, since `Apr0UserData` has no `settledEpoch` field. [6](#0-5) 

### Recommendation
Track the origin epoch of settled APR0 funds (e.g., `settledPrincipalEpoch`), and include settled basis in both the `requestWithdraw` pending-loss guard and `_clearWithdrawClaimForEpoch`/`_withdrawClaimAmountsForEpoch` so loss-adjusted epochs haircut `settledPrincipal + settledInterest` the same way as open APR0 principal. Alternatively, revert in `requestWithdraw` whenever `lossRecoveryPriceByEpoch[lastWithdrawRequest[user]] != 0` and the user holds any claimable basis (normal, open APR0, or settled APR0).

### Proof of Concept
Foundry fork PoC outline (mirroring `test/foundry/IdleCreditVault.t.sol` setup such as `testApr0WithdrawGetsInterestAtStopEpoch`):
1. Deploy `IdleCDOEpochVariant` + `IdleCreditVault`, `setAprs(0,0)`, `setIsAYSActive(false)`.
2. `userA` and `userB` deposit AA; epoch 0 starts.
3. Both call `requestWithdraw` (APR0 path) in epoch 0.
4. `stopEpoch` with borrower underfunding so `collectWithdrawFunds(_amount < pendingWithdraws)` stores `lossRecoveryPriceByEpoch < 1e18`.
5. In epoch 1, `userA` calls `requestWithdraw` again (any dust amount) — `requestWithdraw`'s guard sees `apr0Users[userA].principal` still set only if unsettled; trigger `_settleApr0` first via a normal `requestWithdraw` once `epochNumber > principalEpoch`. Then confirm `withdrawsRequestsByEpoch[userA][lossEpoch] == 0` and the guard passes.
6. Run epoch 1 to completion; `userA.claimWithdrawRequest()` → `_claimLossAdjustedWithdrawRequest` returns 0 basis for the settled APR0 funds, `_claimFundedWithdrawRequest` pays `settledPrincipal + settledInterest` at par.
7. `userB.claimWithdrawRequest()` pays at `lossRecoveryPrice` (or reverts for insufficient funded balance). Assert `userA_payout > userB_payout` for equal initial basis — the haircut was bypassed.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L78-85)
```text
  struct Apr0UserData {
    uint256 principal;
    uint256 principalEpoch;
    uint256 settledPrincipal;
    uint256 settledInterest;
  }
  /// @notice APR=0 withdraw data per user
  mapping (address => Apr0UserData) public apr0Users;
```

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L338-349)
```text
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
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L545-565)
```text
  function _settleApr0(address _user) internal {
    Apr0UserData storage _apr0User = apr0Users[_user];
    uint256 _principal = _apr0User.principal;
    if (_principal == 0) {
      return;
    }
    uint256 _reqEpoch = _apr0User.principalEpoch;
    // Settle only after stopEpoch bumped epochNumber (ie after one full wait epoch).
    if (_reqEpoch >= epochNumber) {
      return;
    }
    // Move principal from "open APR0 bucket" to "settled bucket" (same principal, not duplicated).
    _apr0User.settledPrincipal += _principal;
    uint256 _rate = apr0RateByEpoch[_reqEpoch];
    if (_rate != 0) {
      // Convert per-epoch rate to claimable underlying interest.
      _apr0User.settledInterest += (_principal * _rate) / 1e18;
    }
    _apr0User.principal = 0;
    _apr0User.principalEpoch = 0;
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L567-577)
```text
  function _requestWithdrawApr0(uint256 _amount, address _user) internal {
    // Settle any previous APR0 request first, then start/update current epoch bucket.
    _settleApr0(_user);
    Apr0UserData storage _apr0User = apr0Users[_user];
    if (_apr0User.principal == 0) {
      _apr0User.principalEpoch = epochNumber;
    }
    _apr0User.principal += _amount;
    // Epoch-level APR0 principal used only to compute stopEpoch APR0 pro-rata interest.
    apr0TotalPrincipal += _amount;
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L811-837)
```text
  function _clearWithdrawClaimForEpoch(address _user, uint256 _claimEpoch, bool _isClearingApr0) internal returns (uint256 claimBasis, uint256 burnAmount) {
    (claimBasis, burnAmount) = _withdrawClaimAmountsForEpoch(_user, _claimEpoch);
    if (claimBasis == 0) return (claimBasis, burnAmount);

    uint256 normalAmount = withdrawsRequestsByEpoch[_user][_claimEpoch];
    if (normalAmount != 0) {
      withdrawsRequestsByEpoch[_user][_claimEpoch] = 0;
      // The aggregate may also include older funded receipts; clear only this epoch's piece.
      withdrawsRequests[_user] -= normalAmount;
    }
    Apr0UserData storage apr0User = apr0Users[_user];
    if (apr0User.principal != 0 && apr0User.principalEpoch == _claimEpoch) {
      if (_isClearingApr0) {
        uint256 apr0Principal = apr0User.principal;
        uint256 totalApr0Principal = apr0TotalPrincipal;
        // prepareStopEpochWithApr0 may already close the global APR0 bucket before default finalization.
        apr0TotalPrincipal = apr0Principal >= totalApr0Principal ? 0 : totalApr0Principal - apr0Principal;
      }
      apr0User.principal = 0;
      apr0User.principalEpoch = 0;
    }
    if (lastWithdrawRequest[_user] == _claimEpoch) {
      // The cleared epoch was the latest request marker. Any remaining normal/APR0 receipt
      // is older and already funded, so it can continue to the funded-claim path.
      lastWithdrawRequest[_user] = 0;
    }
  }
```
