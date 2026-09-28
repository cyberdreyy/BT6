### Title
Loss-adjusted withdraw receipts escape their haircut because the recovery price is stored under the post-stop epoch while claims look it up under the request epoch - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.collectWithdrawFunds` stores a stop-epoch loss haircut under `lossRecoveryPriceByEpoch[epochNumber]`, but `epochNumber` is incremented inside `deposit()` during `stopEpoch` before the borrower-funded amount is collected, so the price is written under epoch N+1. `_claimLossAdjustedWithdrawRequest` looks the price up under `lastWithdrawRequest[_user]` (the request epoch N), reads 0, and falls through to `_claimFundedWithdrawRequest`, which pays the full un-haircutted basis. This is the vault analog of CVE-2021-30584's spoofed security context: a receipt is classified under the wrong epoch "domain" and paid at par.

### Finding Description
- `requestWithdraw` records the receipt under the user's request epoch: `lastWithdrawRequest[_user] = currentEpoch` and `withdrawsRequestsByEpoch[_user][currentEpoch] += _amount` [1](#0-0) .
- During `stopEpoch`/`stopEpochWithDuration`, the CDO pushes repaid funds via `deposit()`, which bumps `epochNumber += 1` because the epoch is still marked running [2](#0-1) .
- `collectWithdrawFunds` then writes `lossRecoveryPriceByEpoch[epochNumber]` — i.e. under epoch N+1, not the request epoch N [3](#0-2) .
- On claim, `_claimLossAdjustedWithdrawRequest` resolves `lossEpoch = lastWithdrawRequest[_user]` = N, finds `lossRecoveryPriceByEpoch[N] == 0`, returns 0 [4](#0-3) .
- `claimWithdrawRequest` then calls `_claimFundedWithdrawRequest`, whose gate `epochNumber <= lastWithdrawRequest[_user]` now passes (N+1 > N) and which pays the entire un-haircutted `withdrawsRequests[_user]` through `_transferFundedClaim` [5](#0-4) .

The "one receipt, haircut-priced payout" invariant is broken: the strategy only received `pendingToFund` (the loss-adjusted amount) but pays out the full principal. The `requestWithdraw` guard that forces users to claim loss-adjusted receipts before re-requesting does not help — it checks `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]`, which is also 0 for these users, so it never triggers [6](#0-5) .

### Impact Explanation
Every pending withdraw receipt in an epoch closed with `stopEpochWithDuration(_lossAmount)` is paid at par instead of at `lossRecoveryPrice`. The strategy only holds the haircutted funding, so paying full basis drains underlyings belonging to other claimants and to active LPs (direct theft / insolvency, first-come-first-served). The excess paid equals `claimBasis * (1 - lossRecoveryPrice)` per user. Unprivileged tranche holders trigger this with a normal `requestWithdraw` before the loss epoch ends and `claimWithdrawRequest` after it.

### Likelihood Explanation
Any honest-manager `stopEpochWithDuration` with a realized loss on a vault with pending receipts exposes it — no attacker setup beyond holding a pending withdraw request. It applies on the first loss-adjusted stop, in any mode (fixed-APR or APR0), since the mismatch is purely in the epoch keying.

### Recommendation
Key `lossRecoveryPriceByEpoch` by the request epoch the receipts were recorded under, not by the post-increment `epochNumber`. Either capture the epoch before `deposit()` bumps it (e.g. pass it in, or write `lossRecoveryPriceByEpoch[epochNumber - 1]` when called during the stopEpoch funding flow), or make `_claimLossAdjustedWithdrawRequest`/`_clearWithdrawClaimForEpoch` iterate the user's receipt epochs rather than trusting `lastWithdrawRequest` as the loss-epoch identity. A regression test should stop an epoch with a partial loss and assert claimants receive exactly `claimBasis * lossRecoveryPrice / 1e18`.

### Proof of Concept
Foundry fork PoC sketch (mainnet fork, deploy `IdleCDOEpochVariant` + `IdleCreditVault`):

```solidity
// 1. KYC'd user deposits 100e6 underlying, mints AA tranches.
// 2. manager.startEpoch(); user requests full withdraw via CDO
//    -> strategy.lastWithdrawRequest[user] == epochNumber (N),
//       withdrawsRequestsByEpoch[user][N] == 100e6, pendingWithdraws == 100e6.
// 3. warp past epochEndDate; borrower repays only partially.
//    manager calls stopEpochWithDuration(lossAmount) where
//    previewLossAdjustedWithdrawFunds gives pendingToFund < pendingBasis.
//    Inside: deposit() -> epochNumber becomes N+1;
//    collectWithdrawFunds(pendingToFund) -> lossRecoveryPriceByEpoch[N+1] = price < 1e18,
//    pendingWithdraws = 0, strategy receives only pendingToFund underlying.
// 4. user calls IdleCDO.withdraw()/claimWithdrawRequest:
//    _claimLossAdjustedWithdrawRequest reads lossRecoveryPriceByEpoch[N] == 0 -> skipped.
//    _claimFundedWithdrawRequest: epochNumber (N+1) > lastWithdrawRequest (N) -> passes.
//    User receives 100e6 (full basis) although only pendingToFund was funded.
// 5. assert user payout == 100e6 > pendingToFund; strategy balance now < sum of
//    remaining obligations -> insolvency for subsequent claimants/LPs.
```

Note: the ordering `epochNumber` increment inside `deposit()` → `collectWithdrawFunds` is inferred from the strategy code shown; it should be confirmed against `IdleCDOEpochVariant.stopEpoch`/`stopEpochWithDuration` call order when writing the PoC (if the CDO collects withdraw funds before the deposit that bumps `epochNumber`, the keying is consistent and this specific path is not exploitable).

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L301-349)
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L411-422)
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
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L607-611)
```text
    if (IIdleCDOEpochVariant(idleCDO).isEpochRunning()) {
      // deposit done on stopEpoch (before setting the var to false) so we reset the counter
      totEpochDeposits = 0;
      epochNumber += 1;
    } else {
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L789-792)
```text
  function _claimLossAdjustedWithdrawRequest(address _user) internal returns (uint256 amount) {
    uint256 lossEpoch = lastWithdrawRequest[_user];
    uint256 lossRecoveryPrice = lossRecoveryPriceByEpoch[lossEpoch];
    if (lossRecoveryPrice == 0) return amount;
```
