### Title
Post-default withdraw receipts are paid 1:1 from the default-recovery reserve that was sized only for pre-default claims — (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
After `finalizeDefaultRecovery` runs, `IdleCreditVault.requestWithdraw` keeps accepting new withdraw requests and records them in `postDefaultRequests`, but those receipts are paid out of `defaultRecoveryReserve` at full 1:1 value even though the reserve was computed exclusively to cover pre-default claim basis. The reserve is a bounded "container" of recovered funds; post-default claims render outside that boundary and consume it. The result is theft of unclaimed default recovery from defaulted-epoch receipt holders, and eventual underflow freezing of the remaining claims.

### Finding Description
`finalizeDefaultRecovery` sizes the reserve exactly once, before finalization: [1](#0-0) 

`reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve` is divided by `totalBasis` (active basis + `defaultPendingClaimBasis()` = `pendingWithdraws` + current-epoch instant claims). No post-default requests exist in this basis — they cannot, because `postDefaultRequests` entries only come into existence *after* `defaultRecoveryFinalized` is set.

The post-default request path in `requestWithdraw` explicitly avoids increasing `pendingWithdraws` and adds nothing to the reserve: [2](#0-1) 

The comment says "without increasing borrower-facing pendingWithdraws" and claims the amount is "already-haircut" — but a haircut is not funding. `_claimPostDefaultWithdrawRequest` then pays the full minted amount from the recovery reserve: [3](#0-2) 

and `_transferDefaultRecovery` decrements the finite reserve: [4](#0-3) 

There is no path that grows `defaultRecoveryReserve` after finalization:
- `reserveDefaultRecovery` reverts once `defaultRecoveryFinalized` is true (line 631).
- `collectWithdrawFunds` reduces `pendingWithdraws`, which post-default requests never touch; the tokens it pulls in raise the strategy's raw balance but not the reserve counter, so they cannot be released through `_transferDefaultRecovery` once the counter is exhausted (it underflows).
- `pendingWithdraws` is only *decreased* by `_claimDefaultedWithdrawRequest` post-finalization (line 778).

So every post-default receipt is an unbacked claim drawn against a reserve sized for someone else — the exact analog of iframe content escaping its boundary: claims paid outside the container that was dimensioned for them.

### Impact Explanation
A post-default requester (any KYC-passing tranche holder, who only needs to have no outstanding requests) converts freshly minted receipt tokens into reserve underlyings at 1:1, while defaulted-epoch claimants are only entitled to `claimBasis * defaultRecoveryPrice / 1e18`. Each post-default claim directly reduces `defaultRecoveryReserve`, so legitimate defaulted-epoch claimants who have not yet claimed receive less than their finalized pro-rata share (theft of unclaimed recovery). Once the reserve counter is depleted, `defaultRecoveryReserve -= _amount` underflows, permanently freezing all remaining defaulted and post-default claims. Quantified loss: up to the full `defaultRecoveryReserve`, depending on the size of post-default requests; with a large post-default redemption the entire reserve can be diverted.

### Likelihood Explanation
Requires a finalized borrower default with recovery (`defaulted() == true`, `finalizeDefaultRecovery` executed) and a reserve smaller than total basis — the normal case. After that, any user holding tranche tokens (transferred or remaining) calls `requestWithdraw`; the only gate is the `_hasWithdrawRequest`/instant/postDefault-zero check at line 249. No privileged action is needed beyond the honest manager/borrower sequencing that produced the default. Every post-default claim is guaranteed to overdraw the reserve.

### Recommendation
Do not pay post-default requests from `defaultRecoveryReserve`. Either:
- fund post-default claims through a separately tracked bucket (e.g., a `postDefaultFunded` counter topped up by the CDO via `collectWithdrawFunds`-style pull, paid with `_transferFundedClaim`), or
- extend `reserveDefaultRecovery` (or a new function) to let the CDO add borrower repayments to the reserve post-finalization before minting the receipt, and include `postDefaultRequests` in a reserve-vs-liability invariant check.

### Proof of Concept
Foundry fork sketch (mainnet USDC, existing `IdleCreditVault.t.sol` harness):

```solidity
// contracts/strategies/idle/IdleCreditVault.sol scenario
// 1. Users A (defaulted claimant) and B (post-default requester) deposit, epoch runs.
// 2. stopEpoch, borrower defaults: manager calls _handleBorrowerDefault -> cdo.defaulted() == true.
// 3. finalizeDefault(_recoveredAmount, source) -> finalizeDefaultRecovery():
//    defaultRecoveryReserve = R, sized for totalBasis = activeBasis + pendingBasis only.
// 4. B (no outstanding requests) calls cdoEpoch.requestWithdraw(x, tranche):
//    strategy mints B x receipt tokens, postDefaultRequests[B] = x, reserve unchanged.
// 5. B calls claimWithdrawRequest() -> _claimPostDefaultWithdrawRequest:
//    defaultRecoveryReserve -= x; B receives x underlyings at 1:1.
// 6. A calls claimWithdrawRequest() -> _claimDefaultedWithdrawRequest:
//    expects claimBasisA * defaultRecoveryPrice / 1e18, but reserve is now R - x.
//    If remaining reserve < A's entitlement (or sum of all remaining entitlements > R - x),
//    the last claimants' _transferDefaultRecovery underflows -> DoS / loss.
// Assert: recovered A < claimBasisA * defaultRecoveryPrice / 1e18 by exactly x * recoveryPrice.
```

Note: I verified the strategy-side accounting end to end, but could not re-check the IdleCDOEpochVariant side within the iteration limit (e.g., whether the CDO credits an additional reserve contribution for post-default requests through a path I did not see, or whether post-default requests are blocked elsewhere). If such a funding path exists, this finding is invalid; from the strategy code alone, no post-finalization reserve top-up exists and the invariant `sum(claims) <= defaultRecoveryReserve` is broken by construction.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L247-257)
```text
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
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L685-692)
```text
    uint256 prefundedReserve = _defaultPrefundedInstantReserve();
    uint256 reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve;
    // Recovery can be above par if the recovered funds exceed the computed basis.
    uint256 recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;

    defaultRecoveryFinalized = true;
    defaultRecoveryReserve = reserveAmount;
    defaultRecoveryPrice = recoveryPrice;
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L912-917)
```text
  function _transferDefaultRecovery(address _user, uint256 _amount) internal {
    if (_amount == 0) return;
    // Every defaulted or post-default claim consumes the isolated recovery reserve.
    defaultRecoveryReserve -= _amount;
    underlyingToken.safeTransfer(_user, _amount);
  }
```
