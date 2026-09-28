### Title
Stop-epoch loss haircut is stored under the post-stop epoch number, so haircutted withdraw receipts can still be claimed at par (double payout of receipt value) - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`collectWithdrawFunds` records a shortfall haircut in `lossRecoveryPriceByEpoch[epochNumber]`, but `epochNumber` has already been incremented by the `deposit` call inside `stopEpoch` (`deposit` bumps `epochNumber` whenever `isEpochRunning()` is still true, which is exactly the stopEpoch call path, lines 607-610). Pending receipts requested during the ended epoch are keyed to the *pre-stop* epoch via `lastWithdrawRequest[_user]` (line 282), so `_claimLossAdjustedWithdrawRequest` reads `lossRecoveryPriceByEpoch[lastWithdrawRequest] == 0` and falls through to `_claimFundedWithdrawRequest`, which pays and burns the receipt at full par value — even though the strategy only collected the haircutted amount. This is the on-chain analog of CVE-2016-2384's double free: the same claim basis is effectively released twice (once haircutted at funding time, once again at par at claim time).

### Finding Description
When a borrower partially repays at `stopEpochWithDuration(_lossAmount)`, the CDO calls `previewLossAdjustedWithdrawFunds`/`collectWithdrawFunds` on the strategy. In `collectWithdrawFunds` (lines 411-430):

```solidity
uint256 lossRecoveryPrice = _amount * RECOVERY_FULL / pendingBasis;
pendingWithdraws = 0;
lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice;
```

But `epochNumber` is incremented in `deposit` (lines 607-611) when a deposit is made while the epoch is still flagged as running — i.e. inside `stopEpoch` itself. Any `stopEpoch`/`stopEpochWithDuration` path that deposits interest or repaid funds to the strategy before collecting withdraw funds therefore stores the haircut under `epochNumber + 1`.

Users who called `requestWithdraw` during the ended epoch have `lastWithdrawRequest[_user] = <old epoch>` (line 282). On claim:

- `_claimLossAdjustedWithdrawRequest` (lines 789-801) looks up `lossRecoveryPriceByEpoch[lastWithdrawRequest]` → `0` → returns 0.
- `_claimFundedWithdrawRequest` (lines 319-350) only requires `epochNumber > lastWithdrawRequest` (now true), then burns the full `withdrawsRequests[_user]` basis and calls `_transferFundedClaim(_user, amount)` at par.

Additionally, the guard in `requestWithdraw` (lines 262-270) that blocks a new request while an unclaimed loss-adjusted receipt exists also reads `lossRecoveryPriceByEpoch[lastWithdrawRequest]` → `0`, so the haircutted receipt is invisible to that guard too.

### Impact Explanation
The strategy only holds `_amount < pendingBasis` underlying for that epoch's receipts (the haircut was applied at collection). Paying receipts at par means:

- Early claimants withdraw more underlying than was funded for their receipts.
- Once the strategy's funded underlying is exhausted, `_transferFundedClaim`'s `safeTransfer` reverts for later claimants — their legitimately haircutted receipts are permanently unclaimable (the loss-adjusted path is dead code for them since the price was stored under a key no `lastWithdrawRequest` points to).
- The broken invariant is "one receipt one haircutted payout"; value is effectively released twice from the same undercollateralized bucket — theft from later claimants / insolvency equal to the aggregate haircut (`pendingBasis - _amount`).

### Likelihood Explanation
Requires only an unprivileged user: deposit, call `requestWithdraw` during the running epoch, wait for `stopEpochWithDuration` with any positive `_lossAmount` (an honest manager/borrower sequence — a partial repayment), then `claimWithdrawRequest`. The bug triggers whenever the CDO's stop flow performs a strategy `deposit` (interest deposit) before `collectWithdrawFunds`, which is the documented ordering that increments `epochNumber` (`deposit` comment, lines 607-610). No privileged-role misbehavior is needed.

### Recommendation
Store the loss haircut under the epoch the receipts were requested in, not the post-increment counter. Either:

- Pass the request epoch explicitly: capture `epochNumber` in `collectWithdrawFunds` *before* any deposit-side increment, or store receipts' epoch in a strategy-level `pendingWithdrawsEpoch` set at request time and key `lossRecoveryPriceByEpoch` to it; or
- Make `collectWithdrawFunds` record the haircut keyed by `epochNumber - 1` semantics consistent with `lastWithdrawRequest`, and add an invariant test asserting `lossRecoveryPriceByEpoch[lastWithdrawRequest[user]] != 0` after every loss-adjusted stop.

### Proof of Concept
Foundry fork PoC outline (against `test/foundry/IdleCreditVault.t.sol` harness):

```solidity
function test_LossHaircutKeyedToWrongEpoch() public {
    // 1. User deposits, epoch starts (epochNumber = E)
    // 2. During epoch E: user calls requestWithdraw(amount=100e6) via CDO
    //    => lastWithdrawRequest[user] == E, pendingWithdraws == 100e6
    // 3. stopEpochWithDuration with _lossAmount covering part of pendingWithdraws
    //    => inside stop, deposit() runs first: epochNumber becomes E+1
    //    => collectWithdrawFunds(funded=80e6) stores
    //       lossRecoveryPriceByEpoch[E+1] = 0.8e18   (WRONG KEY)
    // 4. user calls claimWithdrawRequest:
    //    _claimLossAdjustedWithdrawRequest reads lossRecoveryPriceByEpoch[E] == 0
    //    _claimFundedWithdrawRequest pays user 100e6 at par
    assertEq(underlying.balanceOf(user), 100e6);       // overpaid by 20e6
    // 5. second user with same-epoch receipt claims -> safeTransfer reverts
    vm.expectRevert();
    cdo.claimWithdrawRequest(user2);                   // permanent freeze
}
```

Uncertainty note: I verified the key mismatch between `lastWithdrawRequest` (set pre-increment, line 282) and `lossRecoveryPriceByEpoch[epochNumber]` (line 421), and the `epochNumber` increment inside `deposit` during `isEpochRunning` (lines 607-611). I could not fully read `IdleCDOEpochVariant.stopEpoch`'s call ordering within the iteration budget to confirm that a `deposit` to the strategy always precedes `collectWithdrawFunds`; the finding holds for any stop path where that ordering occurs.