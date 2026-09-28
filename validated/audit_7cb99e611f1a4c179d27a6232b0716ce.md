### Title
APR0 withdraw interest permanently stranded when claiming during pool-close (`_interest == 1`) path - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault._claimFundedWithdrawRequest` claims a user's open APR0 principal via `_apr0User.settledPrincipal + _apr0User.principal`, but only pays out `settledInterest`. On the pool-close stop path (`stopEpoch` with `_interest == 1`), `epochEndDate` is set to `0`, which bypasses the "wait one epoch" guard, while `_settleApr0` still skips settlement because `principalEpoch >= epochNumber`. The user's accrued APR0 interest (`apr0RateByEpoch` × principal) is never credited, `apr0Users[_user]` is deleted, and the corresponding funds that `prepareStopEpochWithApr0` pushed into `pendingWithdraws` remain stranded in the vault forever.

### Finding Description
- `prepareStopEpochWithApr0` computes `_apr0NetInterest`, adds it to `pendingWithdraws`, and stores `apr0RateByEpoch[epochNumber]` (`IdleCreditVault.sol:533-538`). The rate is only converted into user-claimable `settledInterest` inside `_settleApr0`, which returns early when `_reqEpoch >= epochNumber` (`IdleCreditVault.sol:551-555`).
- In the close-pool flow, `IdleCDOEpochVariant._stopEpoch` sets `epochEndDate = 0`, `epochDuration = 0` and `disableInstantWithdraw = true` (`IdleCDOEpochVariant.sol:488-493`). With `epochEndDate == 0`, the guard `epochEndDate != 0 && epochNumber <= lastWithdrawRequest[_user]` in `_claimFundedWithdrawRequest` no longer reverts, so immediate claims are allowed (`IdleCreditVault.sol:326-328`).
- The claim then executes `_settleApr0(_user)`, which does nothing for the still-current epoch, computes `amount = normalAmount + settledPrincipal + principal + settledInterest` with `settledInterest == 0`, burns the receipt tokens, and `delete apr0Users[_user]` (`IdleCreditVault.sol:338-347`).
- Result: the user receives principal only. The APR0 interest that the borrower actually funded through `pendingWithdraws` can never be claimed — the per-user record is wiped and `apr0RateByEpoch` still holds the unpaid rate. There is no recovery path; the underlying sits in the vault contract permanently.

This is the accounting-analog of the Libksba integer-overflow bug class: a length/amount field is consumed under a code path that was only safe in the "normal" mode, and the exceptional mode (pool close, like the malformed CRL) produces an accounting shortfall rather than a revert. Solidity 0.8 checked arithmetic prevents the raw overflow, but the semantic invariant "one funded receipt → full payout" is broken.

### Impact Explanation
Every APR0 withdraw requester whose request epoch coincides with the closing epoch loses 100% of their accrued APR0 net interest (`_apr0NetInterest` pro-rata share). The funds are not stolen by an attacker but are permanently frozen in `IdleCreditVault`, which qualifies as "permanent freezing of unclaimed yield" under the acceptance criteria. The attacker/impactee is a KYC-passing lender requesting a withdraw while `unscaledApr == 0`; no privileged role is needed to trigger the loss — only the honest owner/manager calling `stopEpoch(…, 1)` to close the pool.

### Likelihood Explanation
Requires a deployment in APR0 mode and a pool-close stop while APR0 requests are pending — an intended, reachable code path, not an edge case requiring malicious privilege. The bug is deterministic once those conditions hold.

### Recommendation
In `_claimFundedWithdrawRequest`, when `epochEndDate == 0` (closed pool), settle open APR0 interest inline before deleting the user record — e.g. compute `settledInterest += principal * apr0RateByEpoch[principalEpoch] / 1e18` when `apr0RateByEpoch[principalEpoch] != 0` regardless of the `_reqEpoch >= epochNumber` early return, or force `_settleApr0` to settle when the pool is closed.

### Proof of Concept
Foundry fork PoC sketch:

```solidity
// test/foundry/Apr0ClosePoolInterestLoss.t.sol
// Setup: IdleCDOEpochVariant + IdleCreditVault, unscaledApr == 0, KYC'd user deposits AA.
function test_apr0InterestLostOnClosePool() public {
    // 1. user deposits and requests full withdraw while apr == 0 (epoch N)
    vm.prank(user);
    cdo.requestWithdraw(0, address(AAtranche)); // creates apr0Users[user].principal = P, principalEpoch = N

    // 2. honest manager closes the pool at next stopEpoch
    vm.prank(manager);
    cdo.stopEpochWithDuration(newApr, /*_interest*/ 1, 0, 0);
    // prepareStopEpochWithApr0 set apr0RateByEpoch[N] = R > 0 and bumped pendingWithdraws by apr0NetInterest
    // IdleCDO sets epochEndDate = 0 (close-pool branch)

    uint256 vaultBalBefore = underlying.balanceOf(address(strategy));

    // 3. user claims immediately (allowed because epochEndDate == 0)
    vm.prank(user);
    cdo.claimWithdrawRequest(user); // or via vault claim path

    // 4. user received only principal; expectedInterest = P * R / 1e18 is stranded
    assertEq(apr0UserSettledInterest(user), 0);          // record deleted
    assertGt(vaultBalBefore - pendingCleared, expectedApr0Interest); // interest stuck in vault
}
```

Key asserts: `apr0Users[user]` fully cleared while `P * apr0RateByEpoch[N] / 1e18 > 0` was funded via `pendingWithdraws` but never paid — permanent loss of yield equal to the user's pro-rata share of `_apr0NetInterest`.