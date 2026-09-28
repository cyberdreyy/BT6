### Title
Loss-adjusted withdraw receipts are keyed under the post-bump `epochNumber`, so `lossRecoveryPriceByEpoch` never matches `lastWithdrawRequest` and pending receipts are paid at par (draining funded reserves) or permanently frozen - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.collectWithdrawFunds` stores a stop-epoch haircut under `lossRecoveryPriceByEpoch[epochNumber]`, but `epochNumber` is already incremented by `deposit()` during `stopEpoch` (see the comment "deposit done on stopEpoch ... epochNumber += 1"). Withdraw receipts, however, are indexed by the epoch in which `requestWithdraw` was called (`lastWithdrawRequest`, `withdrawsRequestsByEpoch[user][requestEpoch]`). The haircut is therefore written under a different key than the one the claim paths read, so loss-adjusted receipts are treated as fully funded receipts.

### Finding Description
- `requestWithdraw` records `lastWithdrawRequest[_user] = currentEpoch` and `withdrawsRequestsByEpoch[_user][currentEpoch] += _amount` using the *request-time* epoch number (lines 260-294).
- `deposit()` bumps `epochNumber` while the epoch is still running, i.e. inside `stopEpoch` before the running flag is cleared (lines 607-611).
- `collectWithdrawFunds` writes `lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice` when the borrower underfunds `pendingWithdraws`, and zeroes `pendingWithdraws` (lines 411-426). When this is invoked in the same `stopEpochWithDuration` after the deposit bump, the haircut is stored under `epochNumber = requestEpoch + 1`.
- On claim, `_claimLossAdjustedWithdrawRequest` looks up `lossRecoveryPriceByEpoch[lastWithdrawRequest[user]]` (request epoch), finds `0`, and returns early (lines 789-795). The re-request guard in `requestWithdraw` (lines 263-271) reads the same wrong key and also never triggers.
- Execution then falls into `_claimFundedWithdrawRequest`, whose gate `epochNumber <= lastWithdrawRequest[user]` passes because `epochNumber` was just incremented, and it pays the full `withdrawsRequests[user]` amount via `_transferFundedClaim` (lines 319-349).

### Impact Explanation
The strategy only holds the haircut amount (`_amount` collected) for those receipts, but the first claimer(s) withdraw the full un-haircut principal. This either (a) drains underlying that belongs to other funded receipts or the `defaultRecoveryReserve`, i.e. direct theft of other users' funds, or (b) once the balance is exhausted, `safeTransfer` reverts for every remaining claimer of that epoch — the haircutted funds are permanently frozen because `pendingWithdraws` was zeroed and no other path can release them. Loss equals either the full realized loss amount being socialized onto honest claimers, or 100% of the stranded haircut funds. A second-order effect: a later, unrelated `requestWithdraw` in epoch `requestEpoch + 1` would set `lastWithdrawRequest` to the key that *does* hold the haircut, mixing two unrelated request epochs.

### Likelihood Explanation
The trigger is a normal `stopEpochWithDuration(_lossAmount)` with `pendingWithdraws > 0` — a documented, honest-operator flow, not attacker-dependent. The only requirement is that `collectWithdrawFunds` runs after the `deposit()`/epoch bump inside the same stop sequence, which matches the prefunded/epoch flow design. Any unprivileged tranche holder with a pending withdraw request in that epoch can then claim at par or be frozen.

### Recommendation
Key `lossRecoveryPriceByEpoch` by the epoch the pending receipts belong to (the pre-bump `epochNumber - 1`, or an explicit `_epoch` parameter passed by the CDO), not the current `epochNumber` at collection time. Alternatively bump `epochNumber` in `stopEpoch` only after withdraw funding is collected. Add a regression test: request withdraw in epoch E, `stopEpochWithDuration` with a partial loss, then `claimWithdrawRequest` must pay `amount * lossRecoveryPrice / 1e18`.

### Proof of Concept
```solidity
// Foundry fork PoC (schematic — wire into existing test harness for the CDO + vault)
// 1. Epoch E running: user (KYC'd tranche holder) calls CDO.withdraw* -> 
//    IdleCreditVault.requestWithdraw(amount, user, principal)
//    -> lastWithdrawRequest[user] = E; withdrawsRequestsByEpoch[user][E] = amount;
//       pendingWithdraws += amount
// 2. Borrower repays only (contractValue + interest - loss) at epoch end.
//    Manager calls stopEpochWithDuration(loss):
//      - strategy.deposit(...) runs while isEpochRunning() -> epochNumber = E + 1
//      - collectWithdrawFunds(pending - pendingLoss) ->
//          lossRecoveryPriceByEpoch[E + 1] = price  // wrong key
//          pendingWithdraws = 0
// 3. User calls CDO.claimWithdrawRequest:
//      - _claimLossAdjustedWithdrawRequest reads lossRecoveryPriceByEpoch[E] == 0 -> skips
//      - _claimFundedWithdrawRequest: epochNumber (E+1) > lastWithdrawRequest (E) -> pays `amount` at par
// 4. Assert: user received `amount` although strategy only holds `amount * price / 1e18`;
//    second claimer's tx reverts on insufficient balance -> funds frozen.
```

Caveat: I could not read `IdleCDOEpochVariant.stopEpochWithDuration` to confirm `collectWithdrawFunds` executes after the `deposit()`-driven `epochNumber` bump; if it is called strictly before the bump within the same transaction, the keys align and this specific finding does not trigger. The PoC ordering check is the single step needed to confirm. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L260-294)
```text
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
    // burn strategy tokens from cdo (we don't burn future interest here, only the principal)
    _burn(msg.sender, _principal);
    // mint equal amount of strategy tokens to the user as receipt (interest included), useful in case of default
    _mint(_user, _amount);
    // A successfully closed pool already recalled all funds and has no later stopEpoch.
    if (!isClosed) {
      // Global amount that stopEpoch must source from borrower/strategy for all pending receipts.
      pendingWithdraws += _amount;
    }
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L319-349)
```text
  function _claimFundedWithdrawRequest(address _user) internal returns (uint256 amount) {
    // User should wait at least an epoch before claiming the withdraw. Once the epoch is over user can withdraw 
    // at any time even if a new epoch started. 
    // So if epochNumber is the same as the last withdraw request then we revert. Epoch number is increased at stopEpoch
    // NOTE: If a user does not claim a withdraw request and instead requests another withdraw, he will have to wait
    // for another epoch to claim both requests.
    // NOTE 2: if borrower defaults, old withdraw requests can still be claimed
    if (IIdleCDOEpochVariant(idleCDO).epochEndDate() != 0 && (epochNumber <= lastWithdrawRequest[_user])) {
      revert NotAllowed();
    }
    // settle APR=0 requests once the related epoch has ended
    _settleApr0(_user);
    Apr0UserData storage _apr0User = apr0Users[_user];
    // Claim includes:
    // - settled APR0 principal from finalized epochs
    // - still-open APR0 principal: if pool-close mode was used (_interest == 1), IdleCDO sets
    //   epochEndDate = 0 and claims can be immediate, while _settleApr0 can still skip settlement
    //   for the current request epoch (reqEpoch >= epochNumber).
    // - settled APR0 interest
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L411-421)
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
      lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L607-611)
```text
    if (IIdleCDOEpochVariant(idleCDO).isEpochRunning()) {
      // deposit done on stopEpoch (before setting the var to false) so we reset the counter
      totEpochDeposits = 0;
      epochNumber += 1;
    } else {
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
