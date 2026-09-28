### Title
Post-default withdraw requests pay out 1:1 from the fixed `defaultRecoveryReserve`, starving and permanently freezing defaulted-epoch claimants - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
The external bug class is an underflow where one user's withdrawal/deallocation reduces a shared balance so a counterparty's later settlement reverts, permanently blocking their payout. The analog lives in `IdleCreditVault`'s post-default redemption path: `finalizeDefaultRecovery` sizes `defaultRecoveryReserve` exactly for defaulted-epoch claims (`totalBasis * defaultRecoveryPrice / RECOVERY_FULL`), but `requestWithdraw` after finalization creates `postDefaultRequests` receipts that are paid out **1:1** from that same reserve without adding any new funding. A post-default claimer therefore drains reserve earmarked for defaulted claimants, and the last defaulted claimant reverts on `defaultRecoveryReserve -= _amount`.

### Finding Description
At finalization, the reserve is fully accounted for:

```solidity
// IdleCreditVault.sol:686-693
uint256 reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve;
uint256 recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;
defaultRecoveryReserve = reserveAmount;
```

Aggregate claims owed are `totalBasis * recoveryPrice / RECOVERY_FULL ≈ reserveAmount`. After finalization, an unprivileged lender can still open a withdraw request:

```solidity
// IdleCreditVault.sol:247-257
if (defaultRecoveryFinalized) {
  if (_hasWithdrawRequest(_user) || ...) revert NotAllowed();
  _burn(msg.sender, _amount);
  _mint(_user, _amount);
  postDefaultRequests[_user] = _amount;
  return;
}
```

No underlying is added to the reserve for this new claim. Yet `_claimPostDefaultWithdrawRequest` pays it at par from the recovery pool:

```solidity
// IdleCreditVault.sol:760-767
amount = postDefaultRequests[_user];
postDefaultRequests[_user] = 0;
_burn(_user, amount);
_transferDefaultRecovery(_user, amount);   // defaultRecoveryReserve -= amount
```

Every claim path funnels through `_transferDefaultRecovery`, which decrements the same finite `defaultRecoveryReserve` (line 915). When `recoveryPrice < RECOVERY_FULL` — the normal case for a default with losses — the reserve exactly funds the defaulted claims; any post-default claim of `X` leaves the reserve `X` short, so the last defaulted claimant's `claimWithdrawRequest` reverts with an arithmetic underflow in `defaultRecoveryReserve -= _amount`, permanently freezing their recovery. This mirrors the report: one party's payout deallocates the shared balance below what a counterparty's settlement requires, and settlement underflows. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

### Impact Explanation
Permanent freezing of defaulted-epoch recovery claims and theft of reserve funds by earlier claimants. Quantified: if an attacker opens a post-default request of `X` underlyings and claims before honest defaulted claimants, up to `X` of reserve is stolen; every defaulted claimant whose turn comes after the reserve is depleted has their claim revert forever (no alternate claim path exists, and `defaultRecoveryReserve` can never increase after finalization — `reserveDefaultRecovery` reverts once `defaultRecoveryFinalized` is set). The attacker is an ordinary KYC-passed lender, unprivileged.

### Likelihood Explanation
Requires a borrower default to be finalized with `recoveryPrice < RECOVERY_FULL` (any loss). After that, any lender holding tranches can call `requestWithdraw` (IdleCDOEpochVariant) → `IdleCreditVault.requestWithdraw` post-default path and immediately `claimWithdrawRequest`. No privileged cooperation is needed; ordering is determined by who claims first, so a motivated attacker can front-run. One caveat I could not fully rule out: if an off-chain/top-up flow ever increases `defaultRecoveryReserve` after finalization to cover post-default requests, the underflow window shrinks — I found no such path (`reserveDefaultRecovery` reverts post-finalization, and `collectWithdrawFunds`/`collectInstantWithdrawFunds` do not touch the reserve).

### Recommendation
Fund `postDefaultRequests` separately rather than from `defaultRecoveryReserve`: e.g., route post-default requests through `pendingWithdraws`/`_transferFundedClaim` semantics (they are "already-haircut" claims that should be funded by subsequent borrower repayments), or require the request to deposit/pull matching underlying into the reserve before minting the receipt. At minimum, add a solvency check in `_claimPostDefaultWithdrawRequest` ensuring `defaultRecoveryReserve` retains enough for all outstanding defaulted-epoch claims.

### Proof of Concept
Foundry fork PoC sketch (modeled on `test/foundry/IdleCreditVault.t.sol` default-recovery tests):

```solidity
// 1. Users A (honest) and B (attacker) deposit, epoch runs.
// 2. Borrower defaults; manager calls finalizeDefault via CDO with
//    _recoveredAmount < totalBasis  => defaultRecoveryPrice < RECOVERY_FULL.
//    Attacker B has NO pending receipt at default (requirement at line 249).
// 3. B: cdoEpoch.requestWithdraw(0, AAtranche)
//       -> IdleCreditVault.requestWithdraw post-default path mints receipt,
//          sets postDefaultRequests[B] = haircutAmount.
// 4. B: cdoEpoch.claimWithdrawRequest()
//       -> _claimPostDefaultWithdrawRequest pays haircutAmount 1:1,
//          defaultRecoveryReserve -= haircutAmount.
// 5. Honest defaulted claimant A: cdoEpoch.claimWithdrawRequest()
//       -> _claimDefaultedWithdrawRequest computes amount = basis*price
//       -> _transferDefaultRecovery: defaultRecoveryReserve -= amount
//          UNDERFLOWS once B's drain exceeds the rounding slack.
//    A's recovery is permanently frozen; B walked away with reserve
//    that was priced for A.
```

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L686-693)
```text
    uint256 reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve;
    // Recovery can be above par if the recovered funds exceed the computed basis.
    uint256 recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;

    defaultRecoveryFinalized = true;
    defaultRecoveryReserve = reserveAmount;
    defaultRecoveryPrice = recoveryPrice;
    defaultRecoveryEpoch = epochNumber;
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
