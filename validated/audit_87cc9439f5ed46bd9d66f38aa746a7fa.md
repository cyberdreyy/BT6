### Title
Claimed instant-withdraw receipts remain in `instantWithdrawClaimsByEpoch`, corrupting default-recovery basis and prefunded reserve - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`claimInstantWithdrawRequest` zeroes `instantWithdrawsRequests[_user]` but never clears the per-epoch records `instantWithdrawsRequestsByEpoch[_user][epoch]` or the aggregate `instantWithdrawClaimsByEpoch[epoch]`. If the borrower then defaults in that same epoch while other instant receipts remain unfunded, `finalizeDefaultRecovery` treats already-paid claims as still-outstanding basis and counts the already-disbursed underlyings as "prefunded reserve". The recovery price is computed on phantom money, so the last claimants' `_transferDefaultRecovery` transfers underflow the real balance — recovery funds are permanently frozen — and honest claimants are diluted.

### Finding Description
The bug class from the Opencast advisory — removing an item leaves a stale aggregate that is still applied to the remaining items — maps directly onto instant-withdraw receipt accounting.

In `claimInstantWithdrawRequest` (lines 380–393), only `instantWithdrawsRequests[_user]` is cleared; `instantWithdrawsRequestsByEpoch[_user][currentEpoch]` and `instantWithdrawClaimsByEpoch[currentEpoch]` are never decremented [1](#0-0) . `collectInstantWithdrawFunds` only reduces `pendingInstantWithdraws` [2](#0-1) . `epochNumber` is incremented inside `deposit()` when an epoch is running, i.e. only on a *successful* `stopEpoch` funding path [3](#0-2) ; on borrower default (`getFundsFromBorrower` fails → `_handleBorrowerDefault`) `epochNumber` stays equal to the request epoch, so `defaultRecoveryEpoch == epochNumber` still keys the stale entries [4](#0-3) .

At finalization, `defaultPendingClaimBasis` adds the stale `instantWithdrawClaimsByEpoch[epochNumber]` whenever `pendingInstantWithdraws != 0` [5](#0-4) , and `_defaultPrefundedInstantReserve` counts `instantBasis - pendingInstant` — including the already-claimed receipts — as underlying already held [6](#0-5) . `defaultRecoveryReserve` is then set to an amount larger than the real balance [7](#0-6) .

### Impact Explanation
Recovery price = reserveAmount / totalBasis is inflated on the numerator (phantom prefunded funds) and the denominator (phantom claim basis). Honest defaulted claimants receive `(claimBasis * defaultRecoveryPrice) / RECOVERY_FULL` drawn from a reserve that overstates holdings, so the last claimant(s) hit an underflow/insufficient-balance revert in `_transferDefaultRecovery` [8](#0-7) . The net effect is permanent freezing of a portion of the default-recovery reserve and mispriced payouts — a direct solvency break, not a rounding quibble: every previously-claimed instant receipt's full amount is double-counted.

### Likelihood Explanation
Requires only unprivileged actions: a tranche holder calls `requestInstantWithdraw`, gets funded from the CDO buffer, claims (no epoch gating on `claimInstantWithdrawRequest`), and the epoch must then default at `stopEpoch` while another user's instant request is still unfunded. Borrower default is a normal protocol state, not attacker-controlled misconduct, and the attacker can be the same user whose stale entry poisons the basis — no privileged role needed.

### Recommendation
In `claimInstantWithdrawRequest`, clear the per-epoch records the same way `_claimDefaultedInstantWithdrawRequest` does: decrement `instantWithdrawsRequestsByEpoch[_user][epoch]` (tracked via a per-user last-instant-request-epoch index or by clearing all epochs) and `instantWithdrawClaimsByEpoch[epoch]` when a funded instant receipt is paid, mirroring the cleanup in `instantWithdrawsRequestsByEpoch`/`instantWithdrawClaimsByEpoch` at lines 847–853.

### Proof of Concept
```solidity
// Foundry fork PoC sketch against the deployed IdleCreditVault/IdleCDOEpochVariant
// Phase: epoch N running, instant mode, borrower defaults at stopEpoch.

// 1. userA deposits AA, epoch N starts (manager.startEpoch()).
// 2. userA calls cdoEpoch.requestInstantWithdraw(amount, AATranche)
//    -> instantWithdrawsRequestsByEpoch[userA][N] = amount
//    -> instantWithdrawClaimsByEpoch[N] = amount
// 3. CDO funds it via getInstantWithdrawFunds/collectInstantWithdrawFunds
//    -> pendingInstantWithdraws back to 0 for userA's piece.
// 4. userA calls cdoEpoch.claimInstantWithdrawRequest()
//    -> paid in full; BUG: instantWithdrawClaimsByEpoch[N] still == amount.
// 5. userB files a new instant request in the same epoch N that stays
//    unfunded: pendingInstantWithdraws = amountB,
//    instantWithdrawClaimsByEpoch[N] = amount + amountB.
// 6. Borrower fails to repay; manager.stopEpoch -> _handleBorrowerDefault
//    -> epochNumber stays N; owner calls finalizeDefaultRecovery.
// 7. Observations:
//    - defaultPendingClaimBasis() includes userA's already-paid `amount`.
//    - _defaultPrefundedInstantReserve() counts `amount` as held cash that
//      was actually disbursed to userA.
//    - defaultRecoveryReserve > strategy.balanceOf(this) earmarked share.
// 8. userB claims: receives claimBasis * price (diluted). Any subsequent
//    claimant reverts: _transferDefaultRecovery sends more than remains.
//    Recovery dust equal to userA's `amount` is permanently locked.
```

Key assertion to encode: after step 4, `strategy.instantWithdrawClaimsByEpoch(N)` should be 0 but remains `amount`, and post-finalization `strategy.underlyingToken().balanceOf(strategy)` < `strategy.defaultRecoveryReserve()`.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L380-392)
```text
  function claimInstantWithdrawRequest(address _user) external {
    _onlyIdleCDO();
    if (defaultRecoveryFinalized && defaultInstantWithdrawsFinalized) {
      // Clear the defaulted-epoch instant receipt first, then continue so the same call can
      // also pay any older instant receipt that was already funded before default finalization.
      _claimDefaultedInstantWithdrawRequest(_user);
    }
    uint256 amount = instantWithdrawsRequests[_user];
    // burn strategy tokens from user
    _burn(_user, amount);

    instantWithdrawsRequests[_user] = 0;
    _transferFundedClaim(_user, amount);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L398-402)
```text
  function collectInstantWithdrawFunds(uint256 _amount) external {
    _onlyIdleCDO();
    if (_amount == 0) return;
    pendingInstantWithdraws -= _amount;
    underlyingToken.safeTransferFrom(idleCDO, address(this), _amount);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L607-611)
```text
    if (IIdleCDOEpochVariant(idleCDO).isEpochRunning()) {
      // deposit done on stopEpoch (before setting the var to false) so we reset the counter
      totEpochDeposits = 0;
      epochNumber += 1;
    } else {
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L644-648)
```text
  function defaultPendingClaimBasis() public view returns (uint256 basis) {
    basis = pendingWithdraws;
    if (pendingInstantWithdraws != 0) {
      basis += instantWithdrawClaimsByEpoch[epochNumber];
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L686-691)
```text
    uint256 reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve;
    // Recovery can be above par if the recovered funds exceed the computed basis.
    uint256 recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;

    defaultRecoveryFinalized = true;
    defaultRecoveryReserve = reserveAmount;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L693-696)
```text
    defaultRecoveryEpoch = epochNumber;
    // A non-zero pending instant bucket means current-epoch instant receipts were not fully funded
    // and must be paid through the same recovery ratio as normal pending receipts.
    defaultInstantWithdrawsFinalized = pendingInstantWithdraws != 0;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L716-723)
```text
  function _defaultPrefundedInstantReserve() internal view returns (uint256 prefundedReserve) {
    uint256 pendingInstant = pendingInstantWithdraws;
    if (pendingInstant == 0) return prefundedReserve;
    uint256 instantBasis = instantWithdrawClaimsByEpoch[epochNumber];
    if (instantBasis > pendingInstant) {
      prefundedReserve = instantBasis - pendingInstant;
    }
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
