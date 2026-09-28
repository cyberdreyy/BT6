### Title
Loss-adjusted withdraw receipts can be claimed at par by stacking a later normal request — `_claimLossAdjustedWithdrawRequest` only checks `lastWithdrawRequest[_user]` - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
The MarbleRun bug class is "unauthenticated state substitution during recovery": a party presents recovery data (sealed state / manifest) that is not bound to the authoritative state, so payout happens against a mismatched basis. The analog here is the loss-recovery claim path in `IdleCreditVault`. `_claimLossAdjustedWithdrawRequest` derives the claim epoch from the mutable `lastWithdrawRequest[_user]` instead of iterating the user's per-epoch receipts. If a user's latest request epoch has no `lossRecoveryPriceByEpoch` entry, the function returns early and the older loss-adjusted receipt falls through to `_claimFundedWithdrawRequest`, which pays the *aggregate* `withdrawsRequests[_user]` at par — bypassing the haircut entirely.

### Finding Description
In `claimWithdrawRequest`, after the default-recovery branches, the vault runs `_claimLossAdjustedWithdrawRequest(_user)` then `_claimFundedWithdrawRequest(_user)` [1](#0-0) .

`_claimLossAdjustedWithdrawRequest` reads `lossEpoch = lastWithdrawRequest[_user]` and returns `0` if `lossRecoveryPriceByEpoch[lossEpoch] == 0` [2](#0-1) . Loss-adjusted receipts are kept inside the aggregate `withdrawsRequests[_user]` until that path clears them — `_clearWithdrawClaimForEpoch` explicitly subtracts only the matched epoch's piece, with the comment "The aggregate may also include older funded receipts" [3](#0-2) .

So for a user with a loss-adjusted receipt in epoch E1 (`lossRecoveryPriceByEpoch[E1] = p < RECOVERY_FULL`) who then makes a second `requestWithdraw` recorded in epoch E2, `lastWithdrawRequest[_user]` becomes E2. On claim:

1. Loss-adjusted branch looks up epoch E2 → price 0 → early return, the E1 receipt is untouched.
2. `_claimFundedWithdrawRequest` computes `amount = withdrawsRequests[_user] + apr0...`, i.e., E1 basis + E2 basis, burns receipts, clears the aggregate, and pays everything at par via `_transferFundedClaim` [4](#0-3) .

But the strategy only received the haircut amount `basis_E1 * p / RECOVERY_FULL` for epoch E1 — `pendingWithdraws` was already decremented at funding time [5](#0-4) . The excess `basis_E1 * (RECOVERY_FULL - p) / RECOVERY_FULL` is paid out of underlying belonging to other receipt holders and active LPs.

The broken invariant is the loss waterfall / fair burn: a receipt whose funding was haircut must be paid at the haircut price, yet the epoch selection is driven by `lastWithdrawRequest[_user]`, which the attacker controls by making a cheap second request. The existing gating (`epochNumber <= lastWithdrawRequest[_user]` in `_claimFundedWithdrawRequest`) only enforces waiting one epoch — it does not bind the payout price to each receipt's own epoch.

### Impact Explanation
Direct theft of underlying. An attacker holding tranche tokens requests a withdrawal in an epoch that later ends via `stopEpochWithDuration(_lossAmount)` (a borrower partial-loss / loss-socialization event), then makes a dust-sized second request in the following epoch, and claims both at par. Quantified loss to the vault: `receiptBasis_E1 * (1 - lossRecoveryPrice)` — e.g., a 30% loss on a 1M USDC receipt yields ~300k USDC stolen from the funded-claim pool, causing insolvency for honest claimants.

### Likelihood Explanation
Requires a `stopEpochWithDuration` loss event followed by at least one more epoch — both performed by the honest manager/owner per the threat model. The attacker only needs tranche tokens and two `requestWithdraw` calls plus one `claimWithdrawRequest`. The one caveat I could not fully verify in this pass: whether `requestWithdraw` reverts when the user still has an open receipt in a *non-defaulted* state (a revert exists only on the post-default path, per `testPostDefaultWithdrawRequiresClaimingOpenPriorReceipt`). If no such guard exists on the normal path — and nothing in `requestWithdraw`'s shown code enforces it — the exploit is straightforward.

### Recommendation
Decouple claim pricing from `lastWithdrawRequest[_user]`: iterate (or require the caller to pass) every epoch in `withdrawsRequestsByEpoch[_user]`/`apr0Users[_user].principalEpoch`, and pay each receipt at its own epoch's `lossRecoveryPriceByEpoch`. Alternatively, revert in `requestWithdraw` when the user has any uncleared receipt in an epoch with a pending loss-adjusted price, mirroring the post-default guard.

### Proof of Concept
Foundry sketch (fork setup as in `test/foundry/IdleCreditVault.t.sol`):

```solidity
function testLossReceiptClaimedAtParAfterLaterRequest() external {
    address attacker = makeAddr('attacker');
    uint256 amount = 100_000 * ONE_SCALE;
    _depositWithUser(attacker, amount, true);          // AA tranches
    uint256 trancheBal = IERC20(AAtranche).balanceOf(attacker);

    // Epoch 0: request half, epoch ends with loss via stopEpochWithDuration
    vm.prank(attacker);
    uint256 basisE1 = cdoEpoch.requestWithdraw(trancheBal / 2, address(AAtranche));
    _startEpochAndCheckPrices(0);
    // manager stops epoch with loss -> lossRecoveryPriceByEpoch[1] = p < 1e18, haircut funded
    _stopEpochWithLoss(initialProvidedApr, lossAmount);

    // Epoch 1: attacker requests the remaining half (lastWithdrawRequest -> new epoch)
    vm.prank(attacker);
    uint256 basisE2 = cdoEpoch.requestWithdraw(trancheBal / 2, address(AAtranche));
    _startEpochAndCheckPrices(1);
    _stopEpochAndCheckPrices(1, initialProvidedApr, _expectedFundsEndEpoch());

    // Claim: E1 receipt pays at PAR, not at lossRecoveryPrice
    uint256 balPre = underlying.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimWithdrawRequest();

    uint256 expectedHonest = basisE1 * lossPrice / ONE_TRANCHE + basisE2;
    assertGt(underlying.balanceOf(attacker) - balPre, expectedHonest,
        'loss-adjusted receipt was paid at par');
}
```

The asserted excess equals `basisE1 * (RECOVERY_FULL - lossPrice) / RECOVERY_FULL` drained from other claimants' funded withdrawals.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L301-314)
```text
  function claimWithdrawRequest(address _user) external returns (uint256 amount) {
    _onlyIdleCDO();
    if (defaultRecoveryFinalized) {
      // Post-default requests are already priced after the haircut and backed by the reserve,
      // so they must not fall through to the defaulted-epoch receipt logic.
      amount = _claimPostDefaultWithdrawRequest(_user);
      if (amount != 0) return amount;
      // Only receipts created in the defaulted epoch are haircutted here; old fulfilled
      // receipts are handled below at par if they were already funded before default.
      amount = _claimDefaultedWithdrawRequest(_user);
    }
    amount += _claimLossAdjustedWithdrawRequest(_user);
    return amount + _claimFundedWithdrawRequest(_user);
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L789-795)
```text
  function _claimLossAdjustedWithdrawRequest(address _user) internal returns (uint256 amount) {
    uint256 lossEpoch = lastWithdrawRequest[_user];
    uint256 lossRecoveryPrice = lossRecoveryPriceByEpoch[lossEpoch];
    if (lossRecoveryPrice == 0) return amount;

    (uint256 claimBasis, uint256 burnAmount) = _clearWithdrawClaimForEpoch(_user, lossEpoch, false);
    if (claimBasis == 0) return amount;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L797-800)
```text
    // pendingWithdraws was already cleared when the borrower funded the loss-adjusted amount.
    _burn(_user, burnAmount);
    amount = (claimBasis * lossRecoveryPrice) / RECOVERY_FULL;
    _transferFundedClaim(_user, amount);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L815-819)
```text
    uint256 normalAmount = withdrawsRequestsByEpoch[_user][_claimEpoch];
    if (normalAmount != 0) {
      withdrawsRequestsByEpoch[_user][_claimEpoch] = 0;
      // The aggregate may also include older funded receipts; clear only this epoch's piece.
      withdrawsRequests[_user] -= normalAmount;
```
