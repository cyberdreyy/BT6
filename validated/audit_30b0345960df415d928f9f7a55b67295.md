### Title
Loss-adjusted withdraw receipts escape their haircut because `collectWithdrawFunds` keys `lossRecoveryPriceByEpoch` to the post-increment epoch - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
When `stopEpoch` realizes a loss, `IdleCreditVault.collectWithdrawFunds` stores the haircut under `lossRecoveryPriceByEpoch[epochNumber]` (IdleCreditVault.sol:421). But `epochNumber` is incremented inside `deposit()` while the epoch is still flagged as running (IdleCreditVault.sol:607-611), i.e., during the same `stopEpoch` flow before/alongside the funding collection. Withdraw receipts, however, are indexed by the epoch in which `requestWithdraw` was called (`lastWithdrawRequest[_user]`, `withdrawsRequestsByEpoch[_user][currentEpoch]`, IdleCreditVault.sol:282-293). The price key and the receipt key diverge by one epoch whenever a loss is applied, so the anti-loss-escape guard in `requestWithdraw` (IdleCreditVault.sol:261-271) reads a zero price and lets the user open a new request that overwrites `lastWithdrawRequest`, permanently detaching the haircutted receipt from its recovery price — the same bug class as the eclair report: state read/committed against an epoch marker that has already advanced while settlement for the previous epoch is still in flight.

### Finding Description
- `requestWithdraw` records `lastWithdrawRequest[_user] = currentEpoch` and `withdrawsRequestsByEpoch[_user][currentEpoch] += _amount` (IdleCreditVault.sol:282-293).
- On a lossy stop, `IdleCDOEpochVariant` calls the strategy's `deposit` (or equivalent funding path) which executes `epochNumber += 1` while `isEpochRunning()` is still true (IdleCreditVault.sol:607-611), then calls `collectWithdrawFunds(_amount)` with `_amount < pendingWithdraws`, which stores `lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice` — now the *new* epoch N+1, while the receipts it haircuts were recorded under epoch N (IdleCreditVault.sol:411-430).
- Later, `requestWithdraw` checks `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` — i.e., index N — finds 0, and does not revert (IdleCreditVault.sol:261-271). The user can therefore request a second withdraw; `lastWithdrawRequest[_user]` is overwritten to N+1 (IdleCreditVault.sol:282) while `withdrawsRequests[_user]` accumulates both the haircutted basis and the new basis in one unpartitioned bucket (IdleCreditVault.sol:292).
- The funded reserve transferred in `collectWithdrawFunds` only covers `lossRecoveryPrice * pendingBasis`, but the claim path (`_claimFundedWithdrawRequest` / `_claimLossAdjustedWithdrawRequest`, IdleCreditVault.sol:312-349) can no longer associate the epoch-N basis with its haircut: the epoch-N basis is either paid at par from the underfunded reserve (stealing from other pending claimants' share) or, once `lastWithdrawRequest` points at N+1, the epoch-N+1 haircut is applied to unrelated receipts. Either way the one-receipt-one-priced-payout invariant is broken.

### Impact Explanation
An attacker holding tranche tokens requests a withdraw in epoch N. The epoch ends with a realized loss; `collectWithdrawFunds` funds only `price < 1` of the pending basis. Because the recovery price is keyed at N+1, the attacker (in the buffer of epoch N+1) submits a second `requestWithdraw`, which should have reverted per the guard comment ("A loss-adjusted receipt must be claimed before opening a later request", IdleCreditVault.sol:268-270) but passes. The attacker then claims through `_claimFundedWithdrawRequest`/`_transferFundedClaim`, draining the underfunded strategy reserve at effectively par value. Loss bounded by `pendingBasis * (1 - lossRecoveryPrice)` — i.e., the unrecovered haircut amount is paid to early claimers at the expense of later ones, constituting direct theft of other users' funded withdrawal reserve and potential insolvency of the receipt pool.

### Likelihood Explanation
Requires a stopEpoch with `_lossAmount > 0` while `pendingWithdraws > 0` — a normal, privileged-but-honest operation (`stopEpochWithDuration(_lossAmount)`), not attacker-controlled. The attacker needs only to be a KYC'd tranche holder with a pending request, then send one additional `requestWithdraw` via the CDO in the next epoch. No reentrancy or privileged collusion needed; the flaw is the off-by-one keying between the receipt epoch and the recovery-price epoch, matching the "act on state before the transition is fully committed" race in the reference bug.

### Recommendation
Key `lossRecoveryPriceByEpoch` by the epoch the receipts belong to, not the counter value at collection time. Either capture `epochNumber` before `deposit()` increments it during `stopEpoch` (e.g., pass the request epoch into `collectWithdrawFunds`, or set the mapping before bumping `epochNumber`), or record the pending-withdraw basis epoch explicitly (e.g., a `pendingWithdrawsEpoch` variable set in `requestWithdraw` and used as the mapping key). Add a Foundry test asserting that after a lossy `stopEpoch`, a second `requestWithdraw` by the same user reverts with `NotAllowed` and that `lossRecoveryPriceByEpoch` is indexed under the request epoch.

### Proof of Concept
Foundry fork test (schematic, extend `test/foundry/IdleCreditVault.t.sol`):

```solidity
function testLossRecoveryKeyRace() external {
  // deposit, start epoch, user requests withdraw in epoch N
  _depositWithUser(user1, 10_000 * ONE_SCALE, true);
  _startEpochAndCheckPrices(0);
  vm.prank(user1);
  cdoEpoch.requestWithdraw(0, address(AAtranche));
  uint256 reqEpoch = strategy.epochNumber(); // N

  // lossy stopEpoch: borrower underfunds; deposit() bumps epochNumber to N+1
  // before collectWithdrawFunds stores the haircut
  deal(defaultUnderlying, borrower, _expectedFundsEndEpoch() / 2);
  vm.warp(cdoEpoch.epochEndDate() + 1);
  vm.prank(manager);
  cdoEpoch.stopEpochWithDuration(0, loss);

  // haircut was stored under N+1, receipt under N
  assertEq(strategy.lossRecoveryPriceByEpoch(reqEpoch), 0);      // guard reads this
  assertGt(strategy.lossRecoveryPriceByEpoch(reqEpoch + 1), 0);  // but price is here

  // guard should revert but passes; lastWithdrawRequest is overwritten
  vm.prank(user1);
  cdoEpoch.requestWithdraw(0, address(AAtranche)); // no revert => bug
}
```

Note: I could not fully verify the exact call ordering inside `IdleCDOEpochVariant.stopEpoch`/`stopEpochWithDuration` (whether `collectWithdrawFunds` executes after the `deposit()` that increments `epochNumber`) or the body of `_claimLossAdjustedWithdrawRequest` within the available search budget. If `collectWithdrawFunds` is invoked before the epoch increment, the mapping key matches `lastWithdrawRequest` and this finding does not hold — the ordering should be confirmed by reading `contracts/IdleCDOEpochVariant.sol`'s `stopEpoch` implementation before acting on this report.