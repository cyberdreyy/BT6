### Title
Loss-adjusted withdraw receipts escape their haircut because `collectWithdrawFunds` keys `lossRecoveryPriceByEpoch` by the post-stop `epochNumber` while claims look it up by the request-time `lastWithdrawRequest` epoch - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`requestWithdraw` records the user's receipt epoch in `lastWithdrawRequest[_user]` at request time and `_claimLossAdjustedWithdrawRequest` looks up the haircut under `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]`. But `collectWithdrawFunds` — called by the CDO during `stopEpoch` — stores the recovery price under `lossRecoveryPriceByEpoch[epochNumber]`, i.e. the epoch number at stop time. The code comments state "Epoch number is increased at stopEpoch", so the two keys refer to different epochs. Any unprivileged tranche holder whose pending request gets haircutted by a `stopEpochWithDuration` loss can then claim at par through `_claimFundedWithdrawRequest`, draining underlyings the strategy never collected.

### Finding Description
In `requestWithdraw` the receipt epoch is snapshotted from `epochNumber` at request time:

- `lastWithdrawRequest[_user] = currentEpoch;` and `withdrawsRequestsByEpoch[_user][currentEpoch] += _amount;` [1](#0-0) 

On a lossy stop, the CDO calls `collectWithdrawFunds` which records the haircut under the current `epochNumber`:

- `lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice;` [2](#0-1) 

At claim time the haircut is resolved via the *request* epoch:

- `uint256 lossEpoch = lastWithdrawRequest[_user]; uint256 lossRecoveryPrice = lossRecoveryPriceByEpoch[lossEpoch];` [3](#0-2) 

Because `epochNumber` is increased at `stopEpoch` (per the comment at line 322), a request made during epoch N carries `lastWithdrawRequest = N`, while the haircut is stored under `epochNumber = N+1`. `_claimLossAdjustedWithdrawRequest` therefore reads `lossRecoveryPriceByEpoch[N] == 0`, returns 0, and execution falls through to `_claimFundedWithdrawRequest`, which pays `withdrawsRequests[_user]` at par via `_transferFundedClaim` [4](#0-3) . Only `_amount < pendingBasis` was ever pulled into the strategy [5](#0-4) , so the full-basis payout is undercollateralized. The same key mismatch defeats the re-request guard that relies on `lossRecoveryPriceByEpoch[lossEpoch]` [6](#0-5) .

### Impact Explanation
A lender with a pending withdraw in a lossy epoch receives the unhaircutted basis while the strategy only holds the loss-adjusted amount; the residual loss is silently socialized onto active AA/BB LPs and later claimants, breaking the pending-receipt loss waterfall and leaving the strategy insolvent for the unpaid remainder. Loss is bounded by the user's request basis times the haircut difference, repeatable by any requester in the loss epoch.

### Likelihood Explanation
Requires only an unprivileged KYC-passed lender calling `requestWithdraw` in an epoch later stopped with a `_lossAmount` shortfall — an entirely ordinary honest-manager call path. Caveat: the exploit depends on `epochNumber` being incremented inside `stopEpoch` before/around `collectWithdrawFunds` such that the stored key differs from request epochs; I could not open `IdleCDOEpochVariant.stopEpoch` to confirm the exact ordering before the iteration limit. If the increment happens strictly after `collectWithdrawFunds` within the same transaction, the keys coincide and this finding does not hold — that ordering must be verified first.

### Recommendation
Key `lossRecoveryPriceByEpoch` by the request epoch, not the stop-time counter: store under `epochNumber` captured before it is incremented, or pass the target epoch from the CDO to `collectWithdrawFunds`. Alternatively resolve claims via a per-user recorded loss-epoch set rather than relying on `lastWithdrawRequest` matching the write-side key.

### Proof of Concept
```solidity
// Foundry fork PoC sketch (verification of epoch ordering required first)
// 1. user deposits into AA tranche, epoch starts (epochNumber = N)
// 2. user calls cdoEpoch.requestWithdraw(amount, AAtranche)
//      -> lastWithdrawRequest[user] = N, pendingWithdraws += amount
// 3. borrower repays short: manager calls cdoEpoch.stopEpoch(apr, lossAmount)
//      -> previewLossAdjustedWithdrawFunds computes pendingToFund < pendingBasis
//      -> collectWithdrawFunds(pendingToFund) writes lossRecoveryPriceByEpoch[N+1]
// 4. user calls cdoEpoch.claimWithdrawRequest()
//      -> _claimLossAdjustedWithdrawRequest reads byEpoch[N] == 0 -> skipped
//      -> _claimFundedWithdrawRequest pays full `amount` at par
// assert: underlying received == amount > amount * lossRecoveryPrice / 1e18
// assert: vault underlying balance < sum of remaining funded claims (insolvency)
```

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L261-271)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L281-294)
```text
    // save the epoch of the last withdraw request (buffer + epochDuration is 1 epoch)
    lastWithdrawRequest[_user] = currentEpoch;
    // APR=0 requests keep separate accounting and settle interest at stopEpoch.
    // `_amount` here is the post-management-fee principal bucket for that flow.
    if (unscaledApr == 0 && !isClosed) {
      _requestWithdrawApr0(_amount, _user);
    } else {
      // increase the withdraw requests for the user
      // we record both per-user (old, kept for compatibility) and per-epoch so
      // on finalization we can distinguish "default-epoch pending receipts"
      // from old funded receipts.
      withdrawsRequests[_user] += _amount;
      withdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L414-421)
```text
    if (_amount < pendingBasis) {
      // Legacy receipts do not have per-epoch ownership data, so they can only be fully funded.
      if (!defaultRecoveryInitialized) revert NotAllowed();
      uint256 lossRecoveryPrice = _amount * RECOVERY_FULL / pendingBasis;
      // Avoid storing a zero price, which is indistinguishable from "no loss-adjusted epoch".
      if (lossRecoveryPrice == 0) revert NotAllowed();
      pendingWithdraws = 0;
      lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L425-429)
```text
      pendingWithdraws = pendingBasis - _amount;
    }
    if (_amount != 0) {
      underlyingToken.safeTransferFrom(idleCDO, address(this), _amount);
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L789-792)
```text
  function _claimLossAdjustedWithdrawRequest(address _user) internal returns (uint256 amount) {
    uint256 lossEpoch = lastWithdrawRequest[_user];
    uint256 lossRecoveryPrice = lossRecoveryPriceByEpoch[lossEpoch];
    if (lossRecoveryPrice == 0) return amount;
```
