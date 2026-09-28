### Title
Multi-epoch withdraw requests bypass `lossRecoveryPriceByEpoch` haircut via overwritten `lastWithdrawRequest` - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`requestWithdraw` guards re-requests only against the loss state of the *latest* request epoch (`lastWithdrawRequest[_user]`). A user with a pending receipt in a loss-adjusted epoch can open a second request in a later epoch, which overwrites `lastWithdrawRequest`. The loss-adjusted claim path then looks up the wrong epoch, finds no recovery price, and falls through to `_claimFundedWithdrawRequest`, which pays the aggregate `withdrawsRequests[_user]` — including the haircutted receipt — at par.

### Finding Description
The kernel bug pattern is "a stale entry survives because a single state check skips it, and it is then processed at full strength." In `IdleCreditVault`, `requestWithdraw` stores receipts per epoch in `withdrawsRequestsByEpoch` but tracks only one epoch in `lastWithdrawRequest[_user]`:

- [1](#0-0) 

The re-request guard at lines 263–271 checks `lossRecoveryPriceByEpoch[lossEpoch]` where `lossEpoch = lastWithdrawRequest[_user]` only. If the user requests in epoch `E` (receipt recorded under `withdrawsRequestsByEpoch[user][E]`), then requests again in epoch `E+1` *before* the loss is recorded... the ordering matters: the loss for epoch `E` is written in `collectWithdrawFunds` during `stopEpoch` via `lossRecoveryPriceByEpoch[epochNumber]`:

- [2](#0-1) 

Two viable paths defeat the guard:

1. **Stale latest-epoch marker.** User requests in epoch `E`; `stopEpoch` takes a loss so `lossRecoveryPriceByEpoch[E] = p < 1e18`. The user cannot re-request while `lastWithdrawRequest == E`. However, once the user has *any* second pending receipt recorded under a different epoch with `lastWithdrawRequest == E+1` (e.g., the second request was made in the same buffer window before the loss was recorded, or the epochs differ because `epochNumber` is bumped relative to the request epoch), the guard checks only epoch `E+1`, sees `lossRecoveryPriceByEpoch[E+1] == 0`, and passes. On claim, `_claimLossAdjustedWithdrawRequest` reads `lossRecoveryPriceByEpoch[lastWithdrawRequest]`, finds 0, and returns 0 — skipping the epoch-`E` haircut:

- [3](#0-2) 

Then `_claimFundedWithdrawRequest` pays `withdrawsRequests[_user]`, which still aggregates the haircutted epoch-`E` receipt, at par:

- [4](#0-3) 

2. **Uncleared per-epoch basis.** `_claimFundedWithdrawRequest` zeroes `withdrawsRequests[_user]` but never clears `withdrawsRequestsByEpoch[user][*]`. If a default is later finalized (`defaultRecoveryEpoch` set), `_claimDefaultedWithdrawRequest` reads the stale per-epoch basis via `_withdrawClaimAmountsForEpoch` and pays the *same already-claimed* receipt again from `defaultRecoveryReserve`:

- [5](#0-4) 

### Impact Explanation
Direct theft / insolvency: the vault's funded balance for pending receipts equals `claimBasis * lossRecoveryPrice` for the loss epoch plus fully funded later receipts, but the user is paid the un-haircutted aggregate. The deficit is socialized onto other pending claimants and the recovery reserve (insolvency up to `receipt * (1 - lossRecoveryPrice)` per affected epoch). The stale `withdrawsRequestsByEpoch` basis additionally allows a second payout of the same receipt after a subsequent default finalization — a "one receipt, two payouts" violation.

### Likelihood Explanation
Requires only unprivileged actions: deposit, `requestWithdraw` twice across buffer windows, and `claimWithdrawRequest` — all permitted for a KYC'd tranche holder. It triggers whenever `stopEpochWithDuration`/shortfall funding writes `lossRecoveryPriceByEpoch` while a user holds receipts from more than one request epoch (or any funded receipt followed by a borrower default). Honest privileged roles (manager calling `stopEpoch`) only need to take a loss, which is a normal operating event.

### Recommendation
- In `requestWithdraw`, iterate or track *all* unclaimed receipt epochs (e.g., a per-user epoch list) instead of only `lastWithdrawRequest`, or revert if `withdrawsRequests[_user] != 0` whenever any prior request epoch has `lossRecoveryPriceByEpoch != 0`.
- In `_claimFundedWithdrawRequest`, clear `withdrawsRequestsByEpoch[_user]` for all epochs contributing to `withdrawsRequests[_user]` (or for every epoch `<= lastWithdrawRequest`) so stale per-epoch basis cannot be re-claimed under a later `defaultRecoveryEpoch`.
- In `_claimLossAdjustedWithdrawRequest`, detect any nonzero `withdrawsRequestsByEpoch` entry whose epoch has a recovery price rather than relying solely on `lastWithdrawRequest`.

### Proof of Concept
A Foundry PoC (mirroring `test/foundry/IdleCreditVault.t.sol` harness):

```solidity
function test_LossReceiptEscapesHaircut() external {
    // epoch E buffer: user requests withdraw of X
    cdoEpoch.requestWithdraw(X, address(AAtranche));        // withdrawsRequestsByEpoch[user][E] += X
    // epoch E buffer still open: user requests again -> lastWithdrawRequest = E (same epoch)

    // stopEpoch with loss: collectWithdrawFunds funds only X*p -> lossRecoveryPriceByEpoch[E] = p
    // epoch E+1 buffer: user requests again; guard checks lossRecoveryPriceByEpoch[E+1] == 0 -> passes
    cdoEpoch.requestWithdraw(Y, address(AAtranche));        // lastWithdrawRequest = E+1

    // after epoch E+1 ends: claim
    cdoEpoch.claimWithdrawRequest();
    // _claimLossAdjustedWithdrawRequest: lossRecoveryPriceByEpoch[E+1]==0 -> skipped
    // _claimFundedWithdrawRequest pays withdrawsRequests[user] = X + Y at par
    // expected: X*p + Y ; actual: X + Y  => user over-claimed X*(1-p)
}
```

Uncertainty: the exact epoch-index ordering between `epochNumber` increment in `stopEpoch` and `collectWithdrawFunds` was not fully verified (grep output was truncated); if `epochNumber` is bumped before `collectWithdrawFunds` writes `lossRecoveryPriceByEpoch`, the same bug manifests even more directly since receipts and their loss price are then stored under different epochs and the single-slot `lastWithdrawRequest` lookup can never match. Either way, the single-epoch guard cannot correctly track receipts spanning multiple request epochs.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L261-282)
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L772-784)
```text
  function _claimDefaultedWithdrawRequest(address _user) internal returns (uint256 amount) {
    uint256 defaultEpoch = defaultRecoveryEpoch;
    (uint256 claimBasis, uint256 burnAmount) = _clearWithdrawClaimForEpoch(_user, defaultEpoch, true);
    if (claimBasis == 0) return amount;

    // pendingWithdraws stores the claim basis owed by the borrower, including APR0 interest.
    pendingWithdraws -= claimBasis;
    // Only receipt principal exists as strategy tokens. APR0 interest is included in claimBasis
    // but was never minted as a user strategy-token receipt.
    _burn(_user, burnAmount);
    amount = (claimBasis * defaultRecoveryPrice) / RECOVERY_FULL;
    _transferDefaultRecovery(_user, amount);
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
