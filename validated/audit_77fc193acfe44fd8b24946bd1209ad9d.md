### Title
Loss-adjusted withdraw receipts escape the haircut because `collectWithdrawFunds` stores the recovery price under the post-increment epoch, letting users claim at par — (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault` records each withdraw request under the *pre-stop* `epochNumber` in `withdrawsRequestsByEpoch` and `lastWithdrawRequest`. During a successful `stopEpochWithDuration(_lossAmount)`, `epochNumber` is incremented inside `deposit()` before `collectWithdrawFunds` runs, so the loss recovery price is stored under `epoch N+1` while all receipts it should haircut live under epoch `N`. `_claimLossAdjustedWithdrawRequest` reads `lossRecoveryPriceByEpoch[lastWithdrawRequest[user]]` (epoch `N`), finds `0`, and falls through to `_claimFundedWithdrawRequest`, which pays the full un-haircutted basis at par — even though only `pendingToFund` underlyings were collected.

### Finding Description
In `requestWithdraw`, receipts are booked at `currentEpoch = epochNumber` (`IdleCreditVault.sol:260`, `282`, `293`). During `_stopEpoch` in `IdleCDOEpochVariant.sol`, `getFundsFromBorrower` calls `strategy.deposit(...)`, and `deposit()` increments `epochNumber` while `isEpochRunning()` is still true (`IdleCreditVault.sol:607-610`). Immediately after, `_strategy.collectWithdrawFunds(_pendingWithdraws)` executes (`IdleCDOEpochVariant.sol:408-410`), and on partial funding stores `lossRecoveryPriceByEpoch[epochNumber]`, i.e. under `N+1` (`IdleCreditVault.sol:421`).

At claim time:
- `_claimLossAdjustedWithdrawRequest` looks up `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` = slot `N` → `0` → returns 0 (`IdleCreditVault.sol:790-792`).
- `_claimFundedWithdrawRequest` only requires `epochNumber > lastWithdrawRequest[_user]` (`N+1 > N`, true) and then pays `withdrawsRequests[_user]` in full through `_transferFundedClaim` (`IdleCreditVault.sol:326-349`).

The haircut mechanism is therefore unreachable for every receipt it was designed for: the price is written under an epoch index that no user receipt can ever point to, because requests made *after* the stop are recorded under `N+1` but with a fresh `lastWithdrawRequest`, and the `requestWithdraw` guard at lines 263-271 also reads the stale `N` slot.

### Impact Explanation
Broken invariant: loss waterfall / "one receipt, haircut-adjusted payout". The borrower funds only `pendingToFund < pendingBasis` underlying, yet every pending receipt redeems `claimBasis` at par. The first claimers drain the strategy's underlyings (which are the collateral backing other receipt holders and, in close-pool mode, remaining claimants), causing direct theft of the unfunded difference and permanent insolvency/freezing for later claimants whose `safeTransfer` in `_transferFundedClaim` reverts on insufficient balance. Quantified loss: `pendingBasis - pendingToFund` = the pending share of `_lossAmount`, taken from other users' funded claims.

### Likelihood Explanation
Requires only a `stopEpochWithDuration` with `0 < _lossAmount < totalBasis` while `pendingWithdraws > 0` and `defaultRecoveryInitialized` — a normal, honest-manager epoch-stop path with a realized loss, not an edge case. Any unprivileged tranche holder with a pending withdraw request becomes the attacker simply by calling `claimWithdrawRequest` first. No existing guard stops it: the `lossRecoveryPrice != 0` check can never trigger since the price is stored under the wrong epoch key, and `defaultRecoveryFinalized` is false.

### Recommendation
Store the recovery price under the epoch the receipts were requested in, or make claims look it up consistently. Concretely: in `collectWithdrawFunds`, store under the epoch that was just stopped — e.g., `lossRecoveryPriceByEpoch[epochNumber - 1]` after the increment, or capture `pendingEpoch = epochNumber` before `deposit()` bumps it and pass it in. Alternatively, record `lastWithdrawRequest`/per-epoch receipts under the *claim* epoch (`epochNumber + 1`-style convention) at request time so both sides agree. Add a regression test: request withdraw in epoch N, `stopEpochWithDuration` with partial loss, then assert the claim pays `basis * lossRecoveryPrice / 1e18`, not par.

### Proof of Concept
```solidity
// Foundry fork PoC (sketch, mirrors test/foundry/IdleCDOEpochVariantPrefunded.t.sol helpers)
function testLossAdjustedReceiptClaimsAtPar() external {
    // 1. LP deposits and requests a normal withdraw during epoch N
    _depositWithUser(USER, 100e6);
    _requestWithdrawWithUser(USER, ONE_TRANCHE); // lastWithdrawRequest[USER] = N

    // 2. borrower funds only pendingToFund (partial loss split)
    uint256 pendingBasis = strategy.pendingWithdraws();
    uint256 activeBasis  = cdoEpoch.getContractValue() + cdoEpoch.expectedEpochInterest();
    uint256 lossAmount   = (activeBasis + pendingBasis) / 10;
    (uint256 pendingToFund, ) = strategy.previewLossAdjustedWithdrawFunds(lossAmount);
    assertLt(pendingToFund, pendingBasis); // haircut must apply

    address borrower = strategy.borrower();
    uint256 repay = cdoEpoch.expectedEpochInterest() + pendingToFund;
    deal(address(underlying), borrower, repay, true);
    vm.prank(borrower);
    underlying.approve(address(cdoEpoch), repay);

    // 3. honest manager stops with the loss -> epochNumber becomes N+1,
    //    price stored at lossRecoveryPriceByEpoch[N+1], receipts live at N
    uint256 duration = cdoEpoch.epochDuration();
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(10e18, 0, duration, lossAmount);

    uint256 priceAtReceiptEpoch = strategy.lossRecoveryPriceByEpoch(N); // == 0: bug
    assertEq(priceAtReceiptEpoch, 0);

    // 4. USER claims and receives FULL basis instead of pendingToFund share
    vm.prank(USER);
    uint256 claimed = cdoEpoch.claimWithdrawRequest();
    assertGt(claimed, pendingToFund);        // haircut bypassed
    // subsequent claimants' _transferFundedClaim reverts: vault insolvent
}
```

Note on verification limits: I could not read `getFundsFromBorrower`'s body to confirm the exact line where `strategy.deposit()` fires relative to `collectWithdrawFunds` within the same `_stopEpoch` try-block; the finding rests on `deposit()` being the only place `epochNumber` increments during a successful stop (`IdleCreditVault.sol:607-610`) and on the documented comment that the incrementing deposit is "done on stopEpoch". If the increment happens strictly after `collectWithdrawFunds`, the epoch keys align and this finding collapses; that ordering should be confirmed against `IdleCDOEpochVariant.getFundsFromBorrower` before final report inclusion.