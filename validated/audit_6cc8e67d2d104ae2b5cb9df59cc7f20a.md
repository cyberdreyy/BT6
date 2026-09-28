### Title
Loss-adjusted withdraw receipts are only haircut for the *latest* request epoch; earlier-epoch pending receipts are still paid at par, letting an attacker drain funded reserves - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
`collectWithdrawFunds` applies a single `lossRecoveryPriceByEpoch[epochNumber]` haircut to the **entire** `pendingWithdraws` aggregate, which may contain receipts opened in several earlier epochs. However, `_claimLossAdjustedWithdrawRequest` only clears the receipt recorded under `lastWithdrawRequest[_user]` (the most recent epoch). Any remainder in `withdrawsRequests[_user]` — which includes still-unfunded, haircut receipts from earlier epochs — falls through to `_claimFundedWithdrawRequest` and is paid at 100%. An unprivileged lender can therefore claim more than their pro-rata recovery, directly stealing from other pending withdrawers and leaving the vault insolvent.

### Finding Description
Bug-class mapping: the CVE is an out-of-bounds/stale read — the code reads memory at an index derived from attacker-influenced input rather than the correct bound. The analog is the stale-epoch-indexed claim read in `IdleCreditVault`:

- `requestWithdraw` records per-epoch basis in `withdrawsRequestsByEpoch[_user][currentEpoch]` but keeps only a single `lastWithdrawRequest[_user]` marker and a single aggregate `withdrawsRequests[_user]` [1](#0-0) .
- On a `stopEpochWithDuration` loss, `collectWithdrawFunds` writes `lossRecoveryPriceByEpoch[epochNumber]` and zeroes `pendingWithdraws`, implicitly haircutting **every** outstanding receipt regardless of the epoch in which it was requested [2](#0-1) .
- At claim time, `_claimLossAdjustedWithdrawRequest` reads `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` and calls `_clearWithdrawClaimForEpoch`, which zeroes only `withdrawsRequestsByEpoch[_user][_claimEpoch]` and subtracts only that epoch's piece from `withdrawsRequests[_user]` [3](#0-2) [4](#0-3) .
- `claimWithdrawRequest` then adds `_claimFundedWithdrawRequest(_user)`, which pays the whole remaining `withdrawsRequests[_user]` at par via `_transferFundedClaim` [5](#0-4) .

So a receipt opened in epoch N that is still pending when the epoch-M (M > N) stop applies the loss is both (a) haircut in aggregate funding and (b) paid at par on claim. The new-request guard at `requestWithdraw` (lines 261–271) only blocks a *subsequent* request after a loss price already exists; it does not prevent a user from holding receipts across multiple epochs before any loss occurs [6](#0-5) .

### Impact Explanation
Broken invariant: "one receipt, one haircut-adjusted payout" / loss socialization. With loss price `p < 1e18` and an attacker holding basis `B` in an earlier epoch plus `C` in the loss epoch, the attacker receives `C·p/1e18 + B` instead of `(B+C)·p/1e18` — an overpayment of `B·(1 − p)`. Since the strategy only holds the funded (haircut) amount, this excess is paid out of underlyings reserved for other pending claimants; earlier claimants win, later claimants' claims revert on insufficient balance. Quantified loss: up to the attacker's earlier-epoch pending basis times the haircut, i.e. direct theft/insolvency of `B·(1−p)` underlying, bounded by total pending receipts. `_transferFundedClaim`'s `defaultRecoveryReserve` guard does not help because this is not a default scenario — `defaultRecoveryReserve == 0` [7](#0-6) .

### Likelihood Explanation
Requirements: a fixed-APR (non-APR0) vault, epochs enabled, and an honest manager calling `stopEpochWithDuration(_lossAmount)` after the borrower partially repays — an ordinary supported flow, not an edge case. The attacker is a normal KYC'd lender/tranche holder who simply splits their withdraw requests across two consecutive epochs (e.g., request 50% in epoch N buffer, 50% in epoch N+1 buffer) and waits for any loss realization. No privileged cooperation is needed.

### Recommendation
- Track haircut per epoch per user: in `_claimLossAdjustedWithdrawRequest`, iterate or record all epochs with non-zero `withdrawsRequestsByEpoch[_user]` that are `<=` the loss epoch, or store a per-user list/set of pending receipt epochs, and apply `lossRecoveryPriceByEpoch` to each.
- Alternatively, store a per-user weighted recovery basis: on `collectWithdrawFunds` loss, record for each pending epoch its haircut and require `_claimFundedWithdrawRequest` to pay only receipts from epochs whose `lossRecoveryPriceByEpoch[epoch] == 0` (never haircut).
- Simplest robust fix: keep a `mapping(address => uint256) lossAdjustedBasis` that accumulates the haircut-adjusted entitlement for every receipt captured by a loss event, and make `_claimFundedWithdrawRequest` exclude that basis from the par payout.

### Proof of Concept
Foundry fork PoC sketch (against `test/foundry/IdleCreditVault.t.sol` harness):

```solidity
// Setup: epoch credit vault, USDC underlying, borrower funded, epoch running.
// attacker = KYC'd AA lender with 2_000_000 USDC equivalent position.
// victim   = another lender with pending receipt.

// Epoch N buffer: both request withdraw
cdo.requestWithdraw(1_000_000e6, attacker); // attacker epoch-N receipt
cdo.requestWithdraw(1_000_000e6, victim);

// honest manager runs epoch N -> stopEpoch/startEpoch (full funding path is fine,
// or leave receipts pending by only partially funding — pendingWithdraws persists)

// Epoch N+1 buffer: attacker requests again (allowed: no lossRecoveryPrice yet)
cdo.requestWithdraw(1_000_000e6, attacker); // lastWithdrawRequest[attacker] = N+1

// Borrower repays only part; manager calls:
// stopEpochWithDuration expects loss -> collectWithdrawFunds(funded < pendingWithdraws)
// lossRecoveryPriceByEpoch[N+1] = p = 0.5e18 (50% haircut on ALL pending basis)

// Attack
cdo.claimWithdrawRequest(attacker);
// _claimLossAdjustedWithdrawRequest pays epoch-(N+1) piece at p
// _claimFundedWithdrawRequest then pays epoch-N piece (1_000_000e6) AT PAR
uint256 stolen = 1_000_000e6 - 1_000_000e6 * p / 1e18; // = 500_000e6
assertGt(underlying.balanceOf(attacker), expectedFair);
// victim's later claim reverts / is underpaid -> insolvency
```

The assertion to encode: `attackerReceived > (attackerTotalBasis * p) / 1e18` and a subsequent `claimWithdrawRequest(victim)` reverting on insufficient strategy balance, demonstrating theft of `500_000` units of underlying.

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L312-349)
```text
    amount += _claimLossAdjustedWithdrawRequest(_user);
    return amount + _claimFundedWithdrawRequest(_user);
  }

  /// @notice Claim a funded non-default withdraw request at par.
  /// @param _user address of the user
  /// @return amount amount claimed
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L789-800)
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
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L815-820)
```text
    uint256 normalAmount = withdrawsRequestsByEpoch[_user][_claimEpoch];
    if (normalAmount != 0) {
      withdrawsRequestsByEpoch[_user][_claimEpoch] = 0;
      // The aggregate may also include older funded receipts; clear only this epoch's piece.
      withdrawsRequests[_user] -= normalAmount;
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L897-906)
```text
  function _transferFundedClaim(address _user, uint256 _amount) internal {
    if (_amount == 0) return;
    uint256 reserve = defaultRecoveryReserve;
    if (reserve != 0) {
      uint256 balance = underlyingToken.balanceOf(address(this));
      // This should be unreachable when accounting is consistent. Keep the guard so old funded
      // receipts can never spend underlyings reserved for default recovery claimants.
      if (balance < reserve || balance - reserve < _amount) revert NotAllowed();
    }
    underlyingToken.safeTransfer(_user, _amount);
```
