### Title
Pending withdraw receipts escape `stopEpochWithDuration` loss haircut because `lossRecoveryPriceByEpoch` is keyed by the post-increment epoch while user receipts are keyed by the pre-increment request epoch - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
The Cetus exploit is a math-bookkeeping mismatch: an inconsistency between two records of the same quantity lets the attacker withdraw value that was never properly accounted. The strongest analog in the credit vault is the epoch-keyed loss-haircut bookkeeping in `IdleCreditVault`: withdraw receipts are tagged with `lastWithdrawRequest[user] = epochNumber` at request time, but `collectWithdrawFunds` stores the loss recovery price under `epochNumber` **after** `deposit()` has already incremented it during `stopEpoch`/`stopEpochWithDuration`. The two records disagree by exactly one epoch, so the haircut is never found at claim time and pending withdrawers are paid at par out of a strategy that only holds the haircutted amount.

### Finding Description
At request time, `requestWithdraw` records `lastWithdrawRequest[_user] = currentEpoch` where `currentEpoch = epochNumber` (call it `N`), and stores the claim basis in `withdrawsRequestsByEpoch[_user][N]` and `withdrawsRequests[_user]` [1](#0-0) .

On `stopEpoch`/`stopEpochWithDuration`, `deposit()` runs while `isEpochRunning` is still true and executes `epochNumber += 1` (its own comment: "deposit done on stopEpoch (before setting the var to false)"), so the epoch counter becomes `N+1` [2](#0-1) . When the borrower under-funds pending receipts, `collectWithdrawFunds` writes the haircut under the *current* (already incremented) counter: `lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice`, i.e. key `N+1`, and zeroes `pendingWithdraws` [3](#0-2) .

At claim time `_claimLossAdjustedWithdrawRequest` looks up `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]`, i.e. key `N`, which is `0`, so the function returns 0 without clearing anything [4](#0-3) . Execution falls into `_claimFundedWithdrawRequest`, whose gate `epochNumber <= lastWithdrawRequest[_user]` now passes (`N+1 > N`), and pays `withdrawsRequests[_user]` **at par** — the full pre-loss basis [5](#0-4) . The strategy, however, only received `_amount < pendingBasis` underlying, so paying early claimants at par drains the haircutted pool and leaves later receipt holders permanently unpayable.

The same off-by-one poisons `requestWithdraw`'s "claim your loss-adjusted receipt first" guard: it reads `lossRecoveryPriceByEpoch[N]` (0), so users can also open new requests without ever clearing the phantom haircut [6](#0-5) .

### Impact Explanation
Broken invariant: loss waterfall / fair burn. A realized `stopEpochWithDuration` loss intended to be socialized pro-rata across pending receipts and active LPs is instead borne entirely by whoever claims last (insolvency) while early claimants withdraw their full pre-loss principal plus interest. Concretely, with `pendingBasis = P` and borrower funding `A < P`, the first claimants collectively extract `P` from a strategy holding `A`, i.e. they take `A` at par and the remaining `P - A` of receipts are permanently frozen — the exact loss amount that was meant to be haircut is extracted in full by the racing claimants. Any unprivileged KYC-passed lender holding a pending withdraw request qualifies as the attacker; the loss itself is applied by the honest manager via `stopEpochWithDuration(_lossAmount)` (e.g. a partial borrower repayment), not by the attacker.

### Likelihood Explanation
This requires only: (a) an open withdraw request in epoch `N`, (b) the manager calling `stopEpochWithDuration` with a positive `_lossAmount` (a normal, expected flow whenever the borrower underpays or the pool is stopped at a loss — `previewLossAdjustedWithdrawFunds` and `collectWithdrawFunds` exist precisely for this), and (c) the internal ordering in which `deposit()` (epoch increment) precedes `collectWithdrawFunds` inside `stopEpoch`, which the in-code comment on `deposit()` describes as the canonical stop-epoch path. If the real `stopEpoch` implementation instead calls `collectWithdrawFunds` before `deposit()`, the epoch keys would match and this finding collapses — that ordering could not be fully verified from the strategy file alone, and it is the single dependency of this report; the PoC below asserts it directly. If the ordering holds, exploitation is fully deterministic and requires no privileged misbehavior.

### Recommendation
Key the haircut by the epoch in which the receipts were created, not the post-increment counter. Either:

- In `collectWithdrawFunds`, store `lossRecoveryPriceByEpoch[epochNumber - 1]` (or capture the request epoch before `deposit()` increments it), or
- Pass the request epoch explicitly from the CDO: `collectWithdrawFunds(_amount, _receiptEpoch)` and use it consistently in `_claimLossAdjustedWithdrawRequest` and the `requestWithdraw` guard.

Also add a regression test asserting `lossRecoveryPriceByEpoch[lastWithdrawRequest[user]]` is non-zero for every user whose request epoch was haircut, and that aggregate claims after a lossy stop never exceed the funded `_amount`.

### Proof of Concept
```solidity
// Fork test against test/foundry/IdleCreditVault.t.sol harness conventions.
function testLossHaircutEpochMismatch_PoC() external {
    uint256 amount = 10_000 * ONE_SCALE;

    // Alice and Bob deposit into AA; epoch starts
    idleCDO.depositAA(amount); // alice
    // ... bob deposits too ...
    _startEpochAndCheckPrices(0);

    uint256 requestEpoch = IdleCreditVault(address(strategy)).epochNumber();

    // Alice and Bob request withdraws during epoch N
    vm.prank(alice);
    cdoEpoch.requestWithdraw(0, cdoEpoch.AATranche()); // full balance
    vm.prank(bob);
    cdoEpoch.requestWithdraw(0, cdoEpoch.AATranche());

    uint256 pendingBasis = IdleCreditVault(address(strategy)).pendingWithdraws();

    // Borrower only repays principal minus a loss; manager stops epoch with loss
    uint256 loss = pendingBasis / 4; // 25% loss on pending receipts share
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.startPrank(manager);
    // stopEpochWithDuration internally: deposit() -> epochNumber++ THEN collectWithdrawFunds(funded < pendingBasis)
    cdoEpoch.stopEpochWithDuration(0, 0, duration, loss);
    vm.stopPrank();

    uint256 newEpoch = IdleCreditVault(address(strategy)).epochNumber();
    // Assert the off-by-one: haircut stored under N+1, receipt tagged N
    assertEq(IdleCreditVault(address(strategy)).lossRecoveryPriceByEpoch(newEpoch) != 0, true);
    assertEq(IdleCreditVault(address(strategy)).lossRecoveryPriceByEpoch(requestEpoch), 0);
    assertEq(IdleCreditVault(address(strategy)).lastWithdrawRequest(alice), requestEpoch);

    // Alice claims: skips the loss-adjusted path (price 0 at key N),
    // funded path gate epochNumber(N+1) > lastWithdrawRequest(N) passes -> paid at PAR
    uint256 balBefore = underlying.balanceOf(alice);
    vm.prank(alice);
    cdoEpoch.claimWithdrawRequest();
    uint256 aliceOut = underlying.balanceOf(alice) - balBefore;

    // Alice received her full un-haircutted basis instead of basis * lossRecoveryPrice
    uint256 intended = aliceBasis * IdleCreditVault(address(strategy))
        .lossRecoveryPriceByEpoch(newEpoch) / 1e18;
    assertGt(aliceOut, intended); // theft of the haircut

    // Bob's identical claim now reverts on insufficient strategy balance -> permanently frozen
    vm.prank(bob);
    vm.expectRevert();
    cdoEpoch.claimWithdrawRequest();
}
```

Note: this PoC asserts the deposit-before-collect ordering inside `stopEpoch`/`stopEpochWithDuration` (line `assertEq(lastWithdrawRequest(alice), requestEpoch)` plus haircut stored at `newEpoch`); per the scope rules the manager/borrower actions are honest sequencing, and the exploiter is only a lender with a pending receipt.

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L282-294)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L326-349)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L414-421)
```text
    if (_amount < pendingBasis) {
      // Legacy receipts do not have per-epoch ownership data, so they can only be fully funded.
      if (!defaultRecoveryInitialized) revert NotAllowed();
      uint256 lossRecoveryPrice = _amount * RECOVERY_FULL / pendingBasis;
      // Avoid storing a zero price, which is indistinguishable from "no loss-adjusted epoch".
      if (lossRecoveryPrice == 0) revert NotAllowed();
      pendingWithdraws = 0;
      lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L607-611)
```text
    if (IIdleCDOEpochVariant(idleCDO).isEpochRunning()) {
      // deposit done on stopEpoch (before setting the var to false) so we reset the counter
      totEpochDeposits = 0;
      epochNumber += 1;
    } else {
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L789-800)
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
```
