### Title
Loss-haircut withdraw receipt escapes `lossRecoveryPriceByEpoch` when a second request overwrites `lastWithdrawRequest`, letting it be claimed at par - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`IdleCreditVault` tracks a user's pending withdraw receipts per epoch (`withdrawsRequestsByEpoch`), but `claimWithdrawRequest` decides which receipt gets the `stopEpochWithDuration` loss haircut using only the single scalar `lastWithdrawRequest[_user]` — the equivalent of deleting the list head while the real entry stays in the list. If a user holds a loss-haircut receipt from epoch N and then requests a new withdraw in a later epoch M, `lastWithdrawRequest` is overwritten to M, the epoch-N receipt never matches `lossRecoveryPriceByEpoch`, and it is paid at full par through `_claimFundedWithdrawRequest` even though only the haircut amount was ever funded.

### Finding Description
When an epoch stops with a realized loss, `collectWithdrawFunds` collects less than `pendingWithdraws`, stores the haircut in `lossRecoveryPriceByEpoch[epochNumber]`, and sets `pendingWithdraws = 0`. The individual receipts remain in `withdrawsRequests[_user]` / `withdrawsRequestsByEpoch[_user][N]` at full basis; the haircut is only applied at claim time.

`claimWithdrawRequest` resolves claims in this order:

```solidity
// IdleCreditVault.sol:301-314
amount += _claimLossAdjustedWithdrawRequest(_user);
return amount + _claimFundedWithdrawRequest(_user);
```

`_claimLossAdjustedWithdrawRequest` looks up exactly one epoch — `lastWithdrawRequest[_user]`:

```solidity
// IdleCreditVault.sol:789-794
uint256 lossEpoch = lastWithdrawRequest[_user];
uint256 lossRecoveryPrice = lossRecoveryPriceByEpoch[lossEpoch];
if (lossRecoveryPrice == 0) return amount;
(uint256 claimBasis, uint256 burnAmount) = _clearWithdrawClaimForEpoch(_user, lossEpoch, false);
```

`requestWithdraw` then unconditionally overwrites the marker:

```solidity
// IdleCreditVault.sol:281-293
lastWithdrawRequest[_user] = currentEpoch;
...
withdrawsRequests[_user] += _amount;
withdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
```

Comments in `_claimFundedWithdrawRequest` (lines 323-324) confirm that stacking unclaimed requests across epochs is supported behavior ("if a user does not claim... he will have to wait for another epoch to claim both requests"). Once the second request lands:

1. `lossRecoveryPriceByEpoch[M] == 0` → `_claimLossAdjustedWithdrawRequest` returns 0; the epoch-N receipt is never cleared.
2. `_claimFundedWithdrawRequest` computes `normalAmount = withdrawsRequests[_user]` — the aggregate including the epoch-N receipt — and pays it in full via `_transferFundedClaim`.
3. `_transferFundedClaim` only ring-fences `defaultRecoveryReserve`; it does not distinguish other users' funded claim liquidity, so the excess is paid out of funds earmarked for other claimants.

The epoch-N receipt is the "real list entry" that was never unlinked: its funding was haircut at stop time, but because the head pointer moved, it is paid at par — precisely the CVE-2021-47536 pattern of operating on the head while the actual entry survives.

### Impact Explanation
Direct theft / insolvency. The strategy only ever received `claimBasis_N * lossRecoveryPrice` for the epoch-N receipt, but pays out `claimBasis_N`. The attacker (a tranche-token holder who timed a second `requestWithdraw` before claiming) extracts `(claimBasis_N - funded_N)` of underlying belonging to other pending claimants or active LPs; when the strategy balance falls short, subsequent legitimate `claimWithdrawRequest` calls revert in `_transferFundedClaim`/`safeTransfer`, permanently freezing remaining unclaimed yield. Loss scales with the victim's epoch-N receipt size and the haircut magnitude.

### Likelihood Explanation
Requires a `stopEpochWithDuration` with `_lossAmount > 0` — an honest manager/borrower flow (partial repayment) that is a supported protocol mode, as exercised in `test/foundry/IdleCDOEpochQueue.t.sol:811-858`. The attacker only needs to hold an unclaimed pending receipt through that epoch and submit one additional `requestWithdraw` in any later epoch, then wait one epoch — all unprivileged, KYC-lender actions. No privileged-role cooperation, oracle, or timing race is needed.

### Recommendation
Make the loss-adjusted claim path iterate over all epochs with `lossRecoveryPriceByEpoch[epoch] != 0` where the user has `withdrawsRequestsByEpoch` basis, not just `lastWithdrawRequest`. Options: (a) track per-epoch receipt lists so `_claimLossAdjustedWithdrawRequest` clears every loss epoch, (b) settle the loss haircut at `collectWithdrawFunds` time by proportionally reducing each epoch's `withdrawsRequestsByEpoch`/`withdrawsRequests` basis, or (c) reject `requestWithdraw` while `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]] != 0` forces the user to claim the haircut receipt first.

### Proof of Concept
Foundry fork sketch (extend `test/foundry/IdleCreditVault.t.sol` harness):

```solidity
// Epoch N-1 buffer: attacker deposits, epoch N starts.
uint256 tranches = _depositWithUser(attacker, 100e6);

// During epoch N: requestWithdraw(tranches) -> receipt in epoch N+1 bucket.
_requestWithdrawWithUser(attacker, tranches);

// stopEpochWithDuration with _lossAmount > 0: borrower repays only the
// haircut amount for pending receipts (see previewLossAdjustedWithdrawFunds).
// collectWithdrawFunds sets lossRecoveryPriceByEpoch[N+1], pendingWithdraws = 0.

// Epoch N+1 starts; attacker does NOT claim. Request a second withdraw.
_requestWithdrawWithUser(attacker, 1e6);   // lastWithdrawRequest[attacker] = N+2

// Roll past epoch N+2 so claims unlock.
_stopCurrentEpoch(); cdoEpoch.startEpoch(); _stopCurrentEpoch();

// claim: loss path checks lossRecoveryPriceByEpoch[N+2] == 0 -> skipped.
// _claimFundedWithdrawRequest pays full withdrawsRequests at par.
uint256 pre = underlying.balanceOf(attacker);
cdoEpoch.claimWithdrawRequest();  // via IdleCDO wrapper
// assertGt(received, expectedHaircutPayout + secondReceipt);
// assert other users' claims now revert / strategy underfunded.
```

The assertion that the attacker received more than `claimBasis * lossRecoveryPrice` for the epoch-N+1 receipt, funded only partially, demonstrates the broken "one receipt, haircut payout" solvency invariant.