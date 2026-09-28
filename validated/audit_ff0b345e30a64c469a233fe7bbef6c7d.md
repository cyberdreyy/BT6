### Title
Loss-adjusted withdraw receipts are claimed at par because `lossRecoveryPriceByEpoch` is keyed by the post-stop epoch while claims are keyed by the request epoch - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
When `stopEpochWithDuration` realizes a loss, `collectWithdrawFunds` is supposed to store a haircut in `lossRecoveryPriceByEpoch` so pending withdraw receipts are paid at a reduced recovery price. The haircut is stored under `epochNumber` at collection time, but `_claimLossAdjustedWithdrawRequest` looks it up under `lastWithdrawRequest[_user]`, the epoch in which the user *requested* the withdrawal. `epochNumber` is bumped inside `deposit()` during the same `stopEpoch` call (the `epochNumber += 1` runs whenever `isEpochRunning()` is still true, independent of `_amount`), so the two keys diverge by one. The loss-adjusted path therefore returns 0 and the claim falls through to `_claimFundedWithdrawRequest`, which pays the full pre-loss `withdrawsRequests[_user]` at par even though only the haircut amount was collected from the borrower. This is the same class as the reported bug: a "debt" (here, the loss haircut) is misapplied so the user receives more than accounted.

### Finding Description
- `requestWithdraw` records `lastWithdrawRequest[_user] = currentEpoch` (the request epoch) and `withdrawsRequests[_user] += _amount` (full claim basis, `contracts/strategies/idle/IdleCreditVault.sol:281-293`).
- On a lossy `stopEpoch`, `collectWithdrawFunds(_amount)` with `_amount < pendingWithdraws` stores `lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice` and sets `pendingWithdraws = 0`, pulling only the haircut `_amount` into the strategy (`IdleCreditVault.sol:411-430`).
- `epochNumber` is incremented inside `deposit()` whenever `isEpochRunning()` still holds — which is the case during `stopEpoch` — so by the time `collectWithdrawFunds` runs the stored key is `requestEpoch + 1` (`IdleCreditVault.sol:607-611`).
- `claimWithdrawRequest` calls `_claimLossAdjustedWithdrawRequest`, which reads `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` — the *request* epoch, which is 0 — returns 0, then `_claimFundedWithdrawRequest` pays `withdrawsRequests[_user]` in full via `_transferFundedClaim` (`IdleCreditVault.sol:312-313, 789-801, 338-349`).
- The epoch-wait guard does not save it: `epochNumber > lastWithdrawRequest[_user]` already holds after the stop, so the funded path does not revert (`IdleCreditVault.sol:326-328`).

### Impact Explanation
Each loss-adjusted receipt is paid at 100% while only `claimBasis * lossRecoveryPrice / 1e18` was collected. The excess is taken from underlyings held for other claimants and the recovery reserve path (`_transferFundedClaim` only protects `defaultRecoveryReserve`, not other users' funded balances): first claimants drain later claimants' funds, and once the strategy balance is exhausted later `claimWithdrawRequest`/`claimInstantWithdrawRequest` calls revert — permanent freezing of unclaimed yield for honest users and effective theft by earlier claimants. A user with a pending request in the loss epoch is an ordinary unprivileged lender, so the attacker's position is reachable by any KYC-passing tranche holder.

### Likelihood Explanation
Triggering requires the honest manager to call `stopEpochWithDuration(_lossAmount)`/`stopEpoch` with a loss that underfunds `pendingWithdraws`, plus at least one pending receipt — a normal partial-repayment scenario, not an attacker action. One caveat: I could not fully trace the `stopEpoch`/`stopEpochWithDuration` body in `IdleCDOEpochVariant.sol` to confirm `collectWithdrawFunds` executes strictly after the `deposit()` call that bumps `epochNumber`. If the CDO collects funds *before* bumping `epochNumber`, the keys match and the haircut is applied correctly; the PoC below disambiguates this. The design comment "the epoch that stores its haircut" tied to `lastWithdrawRequest` strongly suggests the intended key is the request epoch, while `collectWithdrawFunds` writes under the current `epochNumber`.

### Recommendation
Key `lossRecoveryPriceByEpoch` by the request epoch (i.e., store under `epochNumber - 1` if collection happens after the bump, or record the price under the epoch captured before incrementing), or store the loss epoch explicitly in a `pendingWithdrawLossEpoch` variable at collection time and have `_claimLossAdjustedWithdrawRequest` read that instead of `lastWithdrawRequest`. Also delete `withdrawsRequestsByEpoch`/`withdrawsRequests` for a receipt that can never match a stored haircut so it cannot silently fall through to the par-funded path.

### Proof of Concept
```solidity
function testLossAdjustedReceiptPaidAtPar() public {
    // deposit, start epoch 0, stop epoch 0, then request withdraw in buffer (request epoch = 0)
    uint256 tranches = _depositWithUser(user1, 100e6);
    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, apr, expected);
    uint256 requested = cdoEpoch.requestWithdraw(tranches, AA);
    assertEq(strategy.lastWithdrawRequest(user1), strategy.epochNumber());

    _startEpochAndCheckPrices(1); // borrower underfunds: manager stops with a loss
    vm.warp(cdoEpoch.epochEndDate() + 1);
    // borrower repays less than pendingWithdraws -> collectWithdrawFunds stores
    // lossRecoveryPriceByEpoch[epochNumber_after_bump]
    uint256 loss = requested / 4;
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(loss, ...); // or stopEpoch path that calls collectWithdrawFunds(shortfall)

    uint256 recoveryPrice = strategy.lossRecoveryPriceByEpoch(strategy.epochNumber()); // key = N+1
    assertGt(recoveryPrice, 0);
    // user claims: loss path misses (keyed by request epoch N), funded path pays par
    uint256 bal = underlying.balanceOf(user1);
    vm.prank(user1);
    cdoEpoch.claimWithdrawRequest();
    // Bug: paid full `requested` although only requested*recoveryPrice/1e18 was collected
    assertEq(underlying.balanceOf(user1) - bal, requested); // should be requested*recoveryPrice/1e18
}
```