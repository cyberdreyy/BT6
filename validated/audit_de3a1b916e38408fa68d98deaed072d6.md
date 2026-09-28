### Title
Loss-adjusted withdrawal haircut is only applied to the latest request epoch, letting multi-epoch receipts escape the loss and drain other claimants - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
Analogous to the kernel bug's "wrong unmap size on the cleanup path" (unmap of `iaddr` used `datalen` instead of `inflen`), `IdleCreditVault` applies a realized epoch loss to the *aggregate* `pendingWithdraws` basis when computing `lossRecoveryPriceByEpoch`, but `claimWithdrawRequest` only clears and haircuts the receipt stored under the user's *latest* request epoch (`lastWithdrawRequest`). Any older, still-unclaimed receipt in the same aggregate basis is then paid **at par** by `_claimFundedWithdrawRequest`, breaking the loss-socialization invariant and leaving later claimants unfunded.

### Finding Description
When the borrower under-funds a stop, `collectWithdrawFunds` computes a single recovery price over the whole pending basis and records it under the current `epochNumber`:

```solidity
uint256 lossRecoveryPrice = _amount * RECOVERY_FULL / pendingBasis;   // pendingBasis = ALL unclaimed receipts
lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice;
```
`contracts/strategies/idle/IdleCreditVault.sol:411-430` [1](#0-0) 

The funded amount actually pulled is only `_amount = pendingBasis * price` — i.e., the haircut is assumed on **every** receipt in the basis, including ones requested in earlier epochs that were never claimed.

On the claim side, `claimWithdrawRequest` runs three stages (`_claimPostDefaultWithdrawRequest`, `_claimDefaultedWithdrawRequest`, `_claimLossAdjustedWithdrawRequest`, `_claimFundedWithdrawRequest`): [2](#0-1) 

`_claimLossAdjustedWithdrawRequest` looks up only `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` and clears only `withdrawsRequestsByEpoch[_user][lossEpoch]`: [3](#0-2) [4](#0-3) 

Whatever remains in `withdrawsRequests[_user]` — including the earlier-epoch receipt that was part of the haircut basis — is then paid 1:1 by `_claimFundedWithdrawRequest`: [5](#0-4) 

The request-side guard (`requestWithdraw` lines 261-270) only blocks a *new* request when an unclaimed loss-adjusted receipt exists; it does not block stacking a fresh request on top of an old *funded* receipt — the comments at lines 320-325 explicitly document that multiple pending requests are allowed and simply extend the wait. So a user can legitimately hold receipts in two epochs, and the per-epoch clearing only ever touches `lastWithdrawRequest`. Nothing else decrements or reprices the older entry.

### Impact Explanation
Direct theft / insolvency. Let the user hold receipt `A` (epoch N) and `B` (epoch N+1), with realized loss giving `p < 1`. The borrower funds `(A+B)*p`; the user claims `B*p` via the loss-adjusted path plus `A` at par via the funded path — total `A + B*p > (A+B)*p`. The excess `A*(1-p)` is taken from strategy underlying earmarked for other pending claimants (or causes their claims to revert for insufficient balance → permanent freezing of their funds). The loss waterfall invariant (all pending receipts share the loss pro rata, per `previewLossAdjustedWithdrawFunds`) is broken: [6](#0-5) 

### Likelihood Explanation
Requires only unprivileged actions: deposit, `requestWithdraw`, wait one epoch, `requestWithdraw` again, then wait for an epoch that ends with a partial borrower repayment (`stopEpochWithDuration` with `_lossAmount > 0`). The honest manager/borrower supply the loss; the attacker merely chooses not to claim between epochs — a normal, documented usage pattern. `_transferFundedClaim`'s reserve guard only protects `defaultRecoveryReserve`, not other claimants' funded underlyings. [7](#0-6) 

### Recommendation
Apply the recorded epoch recovery price to every receipt that was inside `pendingWithdraws` when the loss was recorded, not only to `lastWithdrawRequest`. Concretely: either (a) store the loss price per covered epoch range and have `_claimLossAdjustedWithdrawRequest` iterate/clear all of the user's `withdrawsRequestsByEpoch` entries with nonzero `lossRecoveryPriceByEpoch`, or (b) snapshot a per-user funded-vs-pending split so that receipts already fully funded before the loss epoch are provably excluded from the haircut basis in `pendingWithdraws`/`collectWithdrawFunds`.

### Proof of Concept
Foundry fork scenario (modeled on `test/foundry/IdleCreditVault.t.sol`):

```solidity
// setup: deposit as user (AA tranche), start epoch 1
idleCDO.depositAA(amount);
cdoEpoch.requestWithdraw(x, AAtranche);          // receipt A, epoch N
_stopEpoch_fullyFunded();                         // borrower repays, pendingWithdraws funded
// do NOT claim
cdoEpoch.requestWithdraw(y, AAtranche);          // receipt B, epoch N+1; lastWithdrawRequest=N+1
// borrower under-repays -> manager stops with loss
uint256 loss = pendingBasis / 2;
(uint256 pendingToFund,) = strategy.previewLossAdjustedWithdrawFunds(loss);
// borrower approves interest + pendingToFund
cdoEpoch.stopEpochWithDuration(apr, 0, duration, loss); // sets lossRecoveryPriceByEpoch[N+1]=p<1

// attacker claim
uint256 balPre = underlying.balanceOf(user);
cdoEpoch.claimWithdrawRequest();
// received = A (par) + B*p  >  fair (A+B)*p
assertGt(underlying.balanceOf(user) - balPre, (A + B) * p / 1e18);
// a second claimant's receipt now underfunded: their claim reverts or is short
```

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L411-430)
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
    if (_amount != 0) {
      underlyingToken.safeTransferFrom(idleCDO, address(this), _amount);
    }
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L440-460)
```text
  function previewLossAdjustedWithdrawFunds(uint256 _lossAmount) external view returns (uint256 pendingToFund, uint256 activeLoss) {
    uint256 pendingBasis = pendingWithdraws;
    // Full zero-loss funding is safe for legacy aggregate receipts and needs no migration call.
    if (_lossAmount == 0) return (pendingBasis, _lossAmount);

    IIdleCDOEpochVariant cdo = IIdleCDOEpochVariant(idleCDO);
    uint256 activeBasis = _lossActiveBasis(cdo);
    if (pendingBasis == 0) {
      if (_lossAmount > activeBasis) revert NotAllowed();
      return (0, _lossAmount);
    }

    // Legacy pending receipts do not have the per-epoch ownership data needed to store a haircut.
    if (!defaultRecoveryInitialized) revert NotAllowed();
    uint256 totalBasis = activeBasis + pendingBasis;
    if (_lossAmount >= totalBasis) revert NotAllowed();

    uint256 pendingLoss = _lossAmount * pendingBasis / totalBasis;
    pendingToFund = pendingBasis - pendingLoss;
    activeLoss = _lossAmount - pendingLoss;
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L897-907)
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
  }
```
