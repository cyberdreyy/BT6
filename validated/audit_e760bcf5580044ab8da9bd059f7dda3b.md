### Title
Loss haircut skipped by one epoch in `collectWithdrawFunds` — pending withdraw receipts are paid at par after `stopEpochWithDuration` losses - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
The external bug is an off-by-one that *skips one element* (`%%` makes the loop jump one byte past the real delimiter, so bounds are never re-checked). The direct analog lives in `IdleCreditVault`: the stop-epoch loss haircut is stored under `lossRecoveryPriceByEpoch[epochNumber]`, but `epochNumber` is incremented inside `deposit()` during the same `stopEpoch` before `collectWithdrawFunds` runs. The haircut is therefore written to epoch `N+1` while every receipt created in that epoch was recorded — and later looked up — under epoch `N`. The claim path checks the wrong (empty) slot, silently skips the loss adjustment, and pays the unfunded remainder at par.

### Finding Description
`requestWithdraw` records receipts under the *current* epoch and stores the marker:

- `withdrawsRequestsByEpoch[_user][currentEpoch] += _amount` and `lastWithdrawRequest[_user] = currentEpoch` where `currentEpoch = epochNumber` (pre-stop value N) — `IdleCreditVault.sol:260,282,293`. [1](#0-0) 

During `stopEpochWithDuration(_lossAmount)`, the CDO first pushes repaid funds into the strategy via `deposit`. `deposit` detects `isEpochRunning()` is still true and increments `epochNumber` (N → N+1) — `IdleCreditVault.sol:607-610`. [2](#0-1) 

Only afterwards does the CDO call `collectWithdrawFunds(pendingToFund)` with the haircut amount computed by `previewLossAdjustedWithdrawFunds`. On partial funding it writes:

- `lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice` — now epoch **N+1**, not N — `IdleCreditVault.sol:414-421`. [3](#0-2) 

On claim, `_claimLossAdjustedWithdrawRequest` looks up `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` — i.e. epoch **N** — gets `0`, and returns early without applying any haircut — `IdleCreditVault.sol:789-792`. [4](#0-3) 

Execution falls through to `_claimFundedWithdrawRequest`, which passes the gating check (`epochNumber N+1 > lastWithdrawRequest N`) and pays `withdrawsRequests[_user]` — the *full, un-haircutted* basis — from `IdleCreditVault.sol:326-349`. [5](#0-4) 

This is the exact `%%` pattern: one epoch step is skipped, the haircut entry lands one slot past where the reader looks, and the out-of-bounds read (`lossRecoveryPriceByEpoch[N] == 0`) is treated as "no loss", bypassing the check entirely. The same off-by-one also affects the `requestWithdraw` guard at `IdleCreditVault.sol:261-271`, which reads `lossRecoveryPriceByEpoch[lastWithdrawRequest]` — it too reads the empty N slot, so users are never blocked from stacking new requests on top of an unclaimed haircut. [6](#0-5) 

### Impact Explanation
`collectWithdrawFunds` sets `pendingWithdraws = 0` after pulling only `_amount < pendingBasis` from the CDO. The strategy therefore holds `pendingToFund` underlying but owes `pendingBasis` in receipts. Every claimant drains the full basis: the shortfall `(pendingBasis - pendingToFund)` is paid out of underlying belonging to other claims — funded instant-withdraw receipts (`collectInstantWithdrawFunds` transfers), `defaultRecoveryReserve`, or later withdrawers — until the strategy is insolvent. Broken invariant: **one receipt one payout at the funded price** and **loss socialization** — the realized stop-epoch loss is silently re-homed onto unrelated claimants instead of the pending receipts that `previewLossAdjustedWithdrawFunds` explicitly assigned it to.

Quantified example: pendingBasis = 100, loss split gives pendingToFund = 90 (recovery price 0.9e18 stored under N+1). First claimer receives 100 instead of 90; the extra 10 comes from other users' funded claims, which then revert on insufficient balance — direct theft plus freezing of unclaimed funds.

### Likelihood Explanation
Triggering requires only the honest privileged sequence `stopEpochWithDuration` with a realized loss while `pendingWithdraws > 0` — a normal, expected flow (the partial-funding branch and `previewLossAdjustedWithdrawFunds` exist precisely for it). No malicious privileged actor is needed; any lender with a pending request in that epoch benefits at others' expense simply by calling `claimWithdrawRequest` first. Nothing in `collectWithdrawFunds`, `deposit`, or the claim path re-anchors the haircut to the request epoch, and `defaultRecoveryInitialized` (line 416) doesn't gate this — it's required only to *allow* the loss path, so the buggy path is the intended one.

### Recommendation
Write the haircut keyed to the epoch the receipts belong to, not the post-increment counter. Two options:

- In `collectWithdrawFunds`, use `lossRecoveryPriceByEpoch[epochNumber - 1]` (the epoch whose requests are being funded), or snapshot `pendingEpoch = epochNumber` in `requestWithdraw`'s epoch bucket and key the map on it.
- Alternatively, reorder the CDO's `stopEpochWithDuration` so `collectWithdrawFunds` runs before the `deposit()` that bumps `epochNumber`, and add an invariant test asserting `lossRecoveryPriceByEpoch[lastWithdrawRequest[user]] != 0` for every user whose receipts took a haircut.

### Proof of Concept
Foundry fork PoC outline (against `IdleCDOEpochVariant` + `IdleCreditVault`):

```solidity
// Epoch N running; user1 and user2 hold AA tranches.
vm.prank(manager); cdoEpoch.startEpoch();
// Both request full withdrawals during epoch N.
cdoEpoch.requestWithdraw(tranches1, AA);   // lastWithdrawRequest[u1] = N, withdrawsRequestsByEpoch[u1][N]
cdoEpoch.requestWithdraw(tranches2, AA);

// Honest manager stops epoch with a realized loss covering part of pendingWithdraws.
// Inside stopEpochWithDuration: borrower repays -> strategy.deposit() bumps epochNumber to N+1
// -> previewLossAdjustedWithdrawFunds(loss) -> collectWithdrawFunds(pendingToFund < pendingBasis).
cdoEpoch.stopEpochWithDuration(loss);      // lossRecoveryPriceByEpoch[N+1] = price < RECOVERY_FULL

assertEq(strategy.lossRecoveryPriceByEpoch(N), 0);      // slot the claim path reads: empty
assertGt(strategy.lossRecoveryPriceByEpoch(N + 1), 0);  // haircut stored one epoch too far

// user1 claims the FULL basis at par instead of basis * price.
vm.prank(user1); cdoEpoch.claimWithdrawRequest();
// underlying received == full request, strategy now underfunded by (pendingBasis - pendingToFund).
// user2's claim (or instant-withdraw / recovery-reserve-backed claims) later reverts on
// insufficient strategy balance -> stolen funds + frozen claims.
```

*Caveat:* the exploit hinges on `deposit()` (which bumps `epochNumber`) executing before `collectWithdrawFunds` inside `stopEpochWithDuration`. That ordering is implied by `deposit`'s own comment ("deposit done on stopEpoch (before setting the var to false)") at `IdleCreditVault.sol:608`, but the CDO-side call order could not be fully verified within this session; the PoC above confirms or refutes it directly by asserting which epoch slot receives the haircut.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L260-293)
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
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L326-349)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L411-426)
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
    } else {
      // A plain implementation upgrade may leave legacy normal receipts pending. Their next
      // successful stop can fully fund the aggregate before lazy initialization occurs.
      pendingWithdraws = pendingBasis - _amount;
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L607-614)
```text
    if (IIdleCDOEpochVariant(idleCDO).isEpochRunning()) {
      // deposit done on stopEpoch (before setting the var to false) so we reset the counter
      totEpochDeposits = 0;
      epochNumber += 1;
    } else {
      // deposit done between epochs so we increase the counter
      totEpochDeposits += _amount;
    }
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
