### Title
Loss-adjusted withdraw receipts are paid at par when a user re-requests in a later epoch — the claim path binds the haircut only to the latest request epoch — (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault` lets a borrower under-fund pending withdraw receipts at `collectWithdrawFunds`, recording a haircut in `lossRecoveryPriceByEpoch[epoch]`. However, the claim path `_claimLossAdjustedWithdrawRequest` looks up the haircut solely via `lastWithdrawRequest[_user]`, which tracks only the user's *most recent* request epoch. If a user with an underfunded receipt makes a new withdraw request in a later (fully funded) epoch, the loss-adjusted path clears nothing and `_claimFundedWithdrawRequest` pays the aggregate `withdrawsRequests[_user]` — including the underfunded old receipt — at 100% of face value. The extra payout is drained from underlyings that belong to other users, breaking solvency.

### Finding Description
When `stopEpochWithDuration` collects less than `pendingWithdraws`, `collectWithdrawFunds` stores `lossRecoveryPriceByEpoch[lossEpoch] = _amount * 1e18 / pendingBasis` and zeroes `pendingWithdraws`, but leaves each user's basis in `withdrawsRequestsByEpoch[user][lossEpoch]` and in the aggregate `withdrawsRequests[user]` [1](#0-0) .

On claim, `claimWithdrawRequest` first calls `_claimLossAdjustedWithdrawRequest`, which reads `lossEpoch = lastWithdrawRequest[_user]` and looks up `lossRecoveryPriceByEpoch[lossEpoch]` [2](#0-1) . The check and the clearing are keyed to the *latest* request epoch only — `_clearWithdrawClaimForEpoch` clears `withdrawsRequestsByEpoch[user][_claimEpoch]` for that single epoch [3](#0-2) .

A new `requestWithdraw` overwrites `lastWithdrawRequest[user]` with the current epoch and adds to the same aggregate `withdrawsRequests[user]` [4](#0-3) . After that:

- `lossRecoveryPriceByEpoch[lastWithdrawRequest]` is 0 (the new epoch was funded), so `_claimLossAdjustedWithdrawRequest` returns 0 without touching the old epoch's basis.
- `_claimFundedWithdrawRequest` then pays `withdrawsRequests[user]` — which still contains the loss-epoch basis — at full par via `_transferFundedClaim` [5](#0-4) .

The receipt's face value is decoupled from the epoch's actual funding ratio — the analog of accepting finalization data whose `chainId` (here, the request epoch's loss price) is not bound to the claim.

### Impact Explanation
The attacker receives the unhaircutted portion `(1 - lossRecoveryPrice) * oldBasis` of underlyings that the borrower never funded. This amount is taken from the strategy's funded balance reserved for other users' receipts, directly breaking solvency and causing a quantifiable theft equal to the haircut bypassed (up to nearly 100% of the old receipt if the loss epoch was almost entirely unfunded).

### Likelihood Explanation
Requires a `stopEpochWithDuration` partial-funding event (manager/borrower honest action, happens on any shortfall), after which the attacker only needs to make a new `requestWithdraw` in a subsequent epoch — a normal, unprivileged, KYC-gated user flow explicitly supported by the code ("NOTE: If a user does not claim a withdraw request and instead requests another withdraw..."). No privileged collusion is needed.

### Recommendation
Bind each receipt's claim to its own epoch's funding: iterate over all epochs with `withdrawsRequestsByEpoch[user][epoch] > 0` (or track a per-user list of pending epochs), and in `_claimFundedWithdrawRequest` exclude any basis whose `lossRecoveryPriceByEpoch[epoch] != 0` so it can only be paid through `_claimLossAdjustedWithdrawRequest` at its haircut. Alternatively, subtract the underfunded basis from `withdrawsRequests` at `collectWithdrawFunds` time.

### Proof of Concept
Foundry fork test (mainnet, as in `test/foundry/IdleCreditVault.t.sol`):

```solidity
function testLossReceiptPaidAtParAfterReRequest() external {
    // 1. Attacker deposits and requests withdraw during buffer of epoch 0
    uint256 amt = 10_000 * ONE_SCALE;
    _depositWithUser(attacker, amt, true);
    _startEpochAndCheckPrices(0);
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(IERC20Detailed(address(AAtranche)).balanceOf(attacker), address(AAtranche));

    // 2. Honest borrower funds only 50% at stopEpoch -> lossRecoveryPrice = 0.5e18
    IdleCreditVault vault = IdleCreditVault(address(strategy));
    _startEpochAndCheckPrices(1);
    uint256 pending = vault.pendingWithdraws();
    deal(defaultUnderlying, borrower, /* principal + interest but only half of pending */ ...);
    // manager calls stopEpochWithDuration so collectWithdrawFunds(_amount = pending/2)
    ... // assert vault.lossRecoveryPriceByEpoch(requestEpoch) == 0.5e18

    // 3. Attacker makes a new small request in epoch 2 (fully funded epoch)
    _depositWithUser(attacker, 1 * ONE_SCALE, true);
    _stopEpochAndCheckPrices(1, ...); // fully funded
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(1 * ONE_TRANCHE, address(AAtranche));
    _startEpochAndCheckPrices(2); // epochNumber > lastWithdrawRequest now

    // 4. Claim: loss path keyed to epoch 2 (price 0) -> skipped;
    //    funded path pays old 50%-funded basis at 100%
    uint256 balPre = IERC20Detailed(defaultUnderlying).balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimWithdrawRequest();
    // attacker received full face value instead of 50% haircut:
    // (balance gained) ≈ oldBasis + newBasis, while strategy only held
    // oldBasis*0.5 + newBasis -> vault drained by oldBasis*0.5
}
```

The key assertions: `lossRecoveryPriceByEpoch[epoch1] > 0`, `vault.lastWithdrawRequest(attacker) == 2`, and the attacker payout exceeds `oldBasis * lossPrice / 1e18 + newBasis`, with the excess paid out of funds reserved for other pending receipts.

Caveat: I could not fully verify the exact `epochNumber` key used when `collectWithdrawFunds` stores `lossRecoveryPriceByEpoch` (the indexing lines were truncated in search results); the PoC assumes it is keyed by the request epoch of the receipts being funded, which matches the lookup in `_claimLossAdjustedWithdrawRequest` via `lastWithdrawRequest`. If it is keyed differently the exploit path needs adjusting, but the core issue — the haircut lookup being bound only to the latest request epoch while the aggregate payout ignores per-epoch funding — holds regardless.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L282-294)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L411-420)
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
