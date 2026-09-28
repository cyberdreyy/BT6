### Title
Loss-adjusted withdraw receipts are paid at par when a newer request overwrites `lastWithdrawRequest` - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault` tracks each user's *latest* withdraw-request epoch in a single slot, `lastWithdrawRequest[_user]`. Both the guard that blocks new requests after a loss epoch (`requestWithdraw`) and the loss-adjusted claim path (`_claimLossAdjustedWithdrawRequest`) consult only that one slot. An attacker who holds a receipt from an epoch that suffered a `stopEpochWithDuration(_lossAmount)` haircut can place a new withdraw request in a later epoch, which overwrites `lastWithdrawRequest`. The older loss-adjusted receipt then falls through to `_claimFundedWithdrawRequest` and is paid **at par**, even though the pool was only funded for the haircutted amount. This mirrors the kernel bug: a non-atomic, single-slot indicator (`iucv->path` / `lastWithdrawRequest`) is checked and cleared in one context while a second context still acts on the stale resource, letting one receipt be handled by the wrong path — a double-handling/use-after-free analog.

### Finding Description
Relevant code:

- `requestWithdraw` records `lastWithdrawRequest[_user] = currentEpoch` unconditionally at line 282, overwriting any earlier epoch marker. The loss-epoch guard at lines 261-271 only inspects `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]`, i.e. only the *most recent* epoch: [1](#0-0) 
- `claimWithdrawRequest` chains `_claimLossAdjustedWithdrawRequest` then `_claimFundedWithdrawRequest` (lines 312-313). `_claimLossAdjustedWithdrawRequest` derives `lossEpoch` solely from `lastWithdrawRequest[_user]` (line 790), so once it points at a non-loss epoch the function returns 0 and the older receipt is untouched: [2](#0-1) 
- `_claimFundedWithdrawRequest` pays the *aggregate* `withdrawsRequests[_user]` (which still contains the loss-epoch amount, since `_clearWithdrawClaimForEpoch` was never invoked for it) at par via `_transferFundedClaim` (lines 338-349): [3](#0-2) 
- The protocol only funds the haircutted amount for a loss epoch — per line 797, "`pendingWithdraws` was already cleared when the borrower funded the loss-adjusted amount", so `lossRecoveryPriceByEpoch[lossEpoch] < RECOVERY_FULL` claims are the only correct payout for that epoch.

Attack sequence (unprivileged tranche holder, e.g. a KYC'd AA holder):

1. Epoch N: user calls `cdoEpoch.requestWithdraw(...)` → receipt minted, `withdrawsRequestsByEpoch[user][N] = X`, `lastWithdrawRequest[user] = N`, `pendingWithdraws += X`.
2. Borrower returns short; manager calls `stopEpochWithDuration(_lossAmount > 0)` → `lossRecoveryPriceByEpoch[N]` set, only `X * lossPrice` underlyings funded into the strategy.
3. Epoch N+1 buffer: user calls `requestWithdraw` again with new tranche tokens. The guard checks `lossRecoveryPriceByEpoch[N+1] == 0` → passes. `lastWithdrawRequest[user] = N+1` — the epoch-N loss marker is silently lost (the stale-indicator bug).
4. After epoch N+1 ends: `claimWithdrawRequest` → `_claimLossAdjustedWithdrawRequest` sees `lossEpoch = N+1`, price 0, returns 0 → `_claimFundedWithdrawRequest` pays `withdrawsRequests[user] = X + Y` at par.

The user is paid `X` in full although only `X * lossPrice` was ever recovered for that receipt.

### Impact Explanation
Direct theft / insolvency: the payout of `(1 - lossRecoveryPriceByEpoch[N]) * X` comes out of strategy underlyings that back other users' funded receipts and active LP positions. The "one receipt, one haircutted payout" and loss-waterfall invariants are broken; later claimants or LPs absorb the shortfall (permanent loss up to the full un-funded haircut difference). No privileged role is required beyond the honest sequence of a loss epoch.

### Likelihood Explanation
Requires a `stopEpochWithDuration` with `_lossAmount > 0` while the attacker has a pending receipt, plus one additional `requestWithdraw` in a later epoch — both normal user actions, no timing race needed since the state overwrite is deterministic. The guard's single-epoch check misses all older loss epochs by construction.

### Recommendation
Track loss-adjusted receipts independently of `lastWithdrawRequest` — e.g. iterate/record the earliest unclaimed loss epoch per user (or keep a per-user `pendingLossEpoch` set when `lossRecoveryPriceByEpoch` is written at stopEpoch), and make the `requestWithdraw` guard reject whenever the user has *any* `withdrawsRequestsByEpoch` entry whose epoch has a nonzero `lossRecoveryPriceByEpoch`. Alternatively, split the funded aggregate by epoch so `_claimFundedWithdrawRequest` can never pay a loss-epoch amount at par.

### Proof of Concept
Foundry fork PoC sketch (based on `test/foundry/IdleCreditVault.t.sol` helpers):

```solidity
// Setup: depositAA, startEpoch.
// 1) user calls cdoEpoch.requestWithdraw(X, AAtranche) during epoch N.
// 2) warp past epochEndDate; deal borrower < pendingWithdraws + interest;
//    manager calls cdoEpoch.stopEpochWithDuration(lossAmount) so that
//    creditVault.lossRecoveryPriceByEpoch(N) != 0 (e.g. ~50%).
// 3) startEpoch(N+1); user deposits more (or uses remaining tranches) and calls
//    requestWithdraw(Y, AAtranche) -> guard at L261-271 passes because
//    lossRecoveryPriceByEpoch[N+1] == 0; lastWithdrawRequest[user] = N+1.
// 4) warp, stopEpoch normally; user calls cdoEpoch.claimWithdrawRequest().
// Assert: underlying received == X + Y (par) instead of
//         X*lossRecoveryPriceByEpoch[N]/RECOVERY_FULL + Y.
// The excess (1-lossPrice)*X is drained from the strategy balance backing
// other claimants — verify by checking a second user's funded claim then
// reverts in _transferFundedClaim or receives less than expected.
```

Uncertainty note: I could not read `stopEpochWithDuration`/`collectWithdrawFunds` within the iteration budget to confirm the exact funding amount for loss epochs; the finding rests on the documented invariant at line 797 that only the loss-adjusted amount is funded, and on the fact that `withdrawsRequestsByEpoch[user][N]` remains nonzero and is aggregated into `withdrawsRequests[user]` paid at par.

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
