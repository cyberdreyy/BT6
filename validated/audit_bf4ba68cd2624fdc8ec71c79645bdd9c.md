### Title
Second withdraw request orphans a loss-adjusted receipt epoch, letting the user claim the haircutted amount at par - (contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault._claimLossAdjustedWithdrawRequest` looks up loss-adjusted claim data only via `lastWithdrawRequest[_user]`. A user whose withdraw request was haircut by a `stopEpochWithDuration` loss can simply submit a second `requestWithdraw` in the next buffer, which overwrites `lastWithdrawRequest`. The loss-epoch lookup then returns nothing (`lossRecoveryPriceByEpoch[newEpoch] == 0`), the haircutted basis stays inside `withdrawsRequests[_user]`, and `_claimFundedWithdrawRequest` pays the full aggregate at par — including the amount that was supposed to be loss-adjusted. This mirrors CVE-2025-38283: a recovery/claim path keyed on an expected data pointer silently skips processing when that pointer is absent/stale.

### Finding Description
When a borrower shortfall occurs, `IdleCDOEpochVariant.stopEpochWithDuration` calls `collectWithdrawFunds` on the strategy. If `_amount < pendingBasis`, the strategy records `lossRecoveryPriceByEpoch[epochNumber] = _amount * RECOVERY_FULL / pendingBasis` and zeroes `pendingWithdraws` (IdleCreditVault.sol:411-430).

On claim, `claimWithdrawRequest` runs three paths in order (IdleCreditVault.sol:301-314):

1. `_claimLossAdjustedWithdrawRequest` computes `lossEpoch = lastWithdrawRequest[_user]` and only proceeds if `lossRecoveryPriceByEpoch[lossEpoch] != 0` (IdleCreditVault.sol:789-801).
2. `_claimFundedWithdrawRequest` pays `withdrawsRequests[_user]` (the aggregate across all request epochs) at par once `epochNumber > lastWithdrawRequest[_user]` (IdleCreditVault.sol:319-350).

The per-epoch clearing helper `_clearWithdrawClaimForEpoch` would correctly deduct `withdrawsRequestsByEpoch[_user][lossEpoch]` — but it is only reached for the single epoch stored in `lastWithdrawRequest` (IdleCreditVault.sol:811-837).

Sequence:
- Epoch E: user calls `cdoEpoch.requestWithdraw(...)`. `withdrawsRequests[user] += X`, `withdrawsRequestsByEpoch[user][E] += X`, `lastWithdrawRequest[user] = E` (IdleCreditVault.sol:282-293).
- `stopEpochWithDuration` funds only part of `pendingWithdraws` → `lossRecoveryPriceByEpoch[E] = p < RECOVERY_FULL`, `pendingWithdraws = 0`.
- Epoch E+1 buffer: user calls `requestWithdraw` again (allowed — the code explicitly notes a second request is permitted and just resets the wait). `lastWithdrawRequest[user]` is overwritten to E+1 and `withdrawsRequestsByEpoch[user][E+1] += Y`.
- Epoch E+2: user calls `claimWithdrawRequest`.
  - `_claimLossAdjustedWithdrawRequest`: `lossRecoveryPriceByEpoch[E+1] == 0` → returns 0. The epoch-E basis `X` is never cleared from `withdrawsRequests`.
  - `_claimFundedWithdrawRequest`: pays `X + Y` at full price.

The user receives `X` at par instead of `X * p / RECOVERY_FULL`, i.e. steals `(X * (RECOVERY_FULL - p)) / RECOVERY_FULL` underlyings from the strategy's funded claim reserve, which is backed by funds destined for other pending claimants and the borrower repayment.

### Impact Explanation
Direct theft of underlying from `IdleCreditVault`'s funded-claim balance. The loss recovery invariant ("one receipt, haircut applied") is broken: the haircutted portion is paid at full value, so the strategy's underlying is drained by the haircut delta, leaving insufficient funds for later claimants (insolvency/loss socialization inverted — the loss-escaping user is paid first at par). Loss equals `X * (1 - p)` per exploiting user, unbounded by anything except the user's deposit size.

### Likelihood Explanation
Requires only an unprivileged tranche holder with a pending withdraw request in an epoch where `stopEpochWithDuration` realizes a partial loss on `pendingWithdraws` — a normal protocol path after borrower underpayment. The exploit needs two ordinary `requestWithdraw` calls and one `claimWithdrawRequest`; no privileged action, no race. No guard prevents a second request while a loss-adjusted epoch claim is outstanding: `requestWithdraw` does not check `lossRecoveryPriceByEpoch[lastWithdrawRequest[user]]`, and `claimWithdrawRequest` never iterates other epochs.

### Recommendation
In `_claimLossAdjustedWithdrawRequest` (or `claimWithdrawRequest`), iterate/verify all epochs in which the user has `withdrawsRequestsByEpoch` entries with a non-zero `lossRecoveryPriceByEpoch`, or block `requestWithdraw` when `lossRecoveryPriceByEpoch[lastWithdrawRequest[user]] != 0` and the claim has not been collected (e.g., force `_claimLossAdjustedWithdrawRequest` inside `requestWithdraw` before overwriting `lastWithdrawRequest`).

### Proof of Concept
```solidity
// Foundry fork test (test/foundry/IdleCreditVault.t.sol harness)
// Setup: deposit AA+BB, start epoch, request withdraw of X tranches.
uint256 requested = cdoEpoch.requestWithdraw(mintedAA, address(AAtranche));

// Epoch E stops with a partial loss on pending receipts
// (stopEpochWithDuration path funds only part of pendingWithdraws):
//   strategy.collectWithdrawFunds(funded) with funded < pendingBasis
//   => lossRecoveryPriceByEpoch[E] = funded * RECOVERY_FULL / pendingBasis
_stopEpochWithPartialWithdrawFunding(); // e.g. fund 50% -> p = 0.5e18

// New buffer: attacker requests a small second withdraw, overwriting
// lastWithdrawRequest[user] = E+1.
cdoEpoch.requestWithdraw(smallAmount, address(AAtranche));

// Run one full epoch so epochNumber > lastWithdrawRequest.
_startEpoch();
_stopEpochFullyFunded();

// Claim: loss-adjusted path sees lossRecoveryPriceByEpoch[E+1] == 0 and skips;
// funded path pays requested + smallAmount at par.
uint256 balPre = underlying.balanceOf(cdo);
cdoEpoch.claimWithdrawRequest();
// Attacker received `requested` at par instead of requested * p / RECOVERY_FULL.
// assertEq received ~= requested + smallAmount  (should have been requested*0.5 + smallAmount)
```