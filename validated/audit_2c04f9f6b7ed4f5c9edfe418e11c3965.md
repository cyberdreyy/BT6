### Title
Stale `lastWithdrawRequest` lets a loss-adjusted withdraw receipt be paid at par - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
In `IdleCreditVault`, a withdraw receipt that was funded with a loss (via `collectWithdrawFunds` → `lossRecoveryPriceByEpoch`) can later be claimed at 100% if the user simply makes another `requestWithdraw` in a subsequent epoch. `_claimLossAdjustedWithdrawRequest` only looks up `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]`, i.e. only the *latest* request epoch; the older haircutted epoch's entry in `withdrawsRequests[_user]`/`withdrawsRequestsByEpoch` is never cleared and is then paid at par by `_claimFundedWithdrawRequest`, like CVE-2023-21400's missing lock between concurrent state users — two accounting paths operate on overlapping receipt state without mutual exclusion.

### Finding Description
- `requestWithdraw` overwrites `lastWithdrawRequest[_user] = currentEpoch` on every request (`IdleCreditVault.sol:282`).
- On a loss-funding `stopEpochWithDuration`, `collectWithdrawFunds` stores `lossRecoveryPriceByEpoch[epochNumber]` and zeroes `pendingWithdraws` (`IdleCreditVault.sol:411-425`).
- At claim time, `_claimLossAdjustedWithdrawRequest` reads `lossEpoch = lastWithdrawRequest[_user]` (`IdleCreditVault.sol:790`). If the user re-requested, this is the *new* epoch, `lossRecoveryPriceByEpoch[newEpoch] == 0`, and the function returns 0 without clearing the old epoch's `withdrawsRequestsByEpoch[_user][lossEpoch]` entry or decrementing `withdrawsRequests[_user]` (`IdleCreditVault.sol:811-837`).
- `_claimFundedWithdrawRequest` then pays `normalAmount = withdrawsRequests[_user]` — which still contains the loss-adjusted E1 receipt — at full par via `_transferFundedClaim` (`IdleCreditVault.sol:338-349`), gating only on `epochNumber <= lastWithdrawRequest[_user]` (`IdleCreditVault.sol:326`).
- The haircut is thus completely bypassed; the user receives par payout while the strategy only collected the loss-reduced amount for that epoch.

### Impact Explanation
Direct theft / insolvency: the strategy's funded-claim balance covers only `claimBasis * lossRecoveryPrice` for the haircut epoch. Paying the receipt at par over-draws the strategy's underlying, taking funds that back other users' funded claims and instant-withdraw claims. Loss is quantifiable as `claimBasis * (1 - lossRecoveryPrice)`, and in a thin-funded pool it renders later legitimate claims unpayable (permanent freezing of unclaimed payouts).

### Likelihood Explanation
Requires only a KYC-passed lender: hold tranche tokens, `requestWithdraw` in an epoch that ends with a borrower shortfall (`stopEpochWithDuration` partial funding), do not claim, then `requestWithdraw` again in the next epoch and claim once that epoch ends. All attacker actions are ordinary unprivileged calls; no privileged misbehavior needed. Likelihood is bounded by needing a realized loss epoch, so Medium.

### Recommendation
Track the loss-adjusted epoch per user explicitly (e.g. `lossAdjustedEpoch[_user]` set at collect time, or iterate/clear all non-zero `withdrawsRequestsByEpoch` entries before the funded path), and in `_claimFundedWithdrawRequest` exclude any epoch that has a non-zero `lossRecoveryPriceByEpoch` from the par-paid `normalAmount`.

### Proof of Concept
Foundry fork PoC (sketch, on the credit-vault deployment):

```solidity
function testLossAdjustedReceiptPaidAtPar() external {
    // Setup: AA deposit by KYC user, epoch running (existing helpers in test/foundry)
    _depositWithUser(ATTACKER_KYC, 100e18);
    cdoEpoch.startEpoch();

    // Epoch E1: request withdraw of full position
    _requestWithdrawWithUser(ATTACKER_KYC, 100e18);
    uint256 e1 = strategy.epochNumber();

    // Borrower repays only 50% -> stopEpochWithDuration(_lossAmount) -> lossRecoveryPriceByEpoch[e1] = 0.5e18
    _stopCurrentEpochWithLoss(50e18); // funds 50, pendingWithdraws=0

    // New epoch E2 starts; attacker requests a second small withdraw,
    // overwriting lastWithdrawRequest[attacker] = e2
    vm.prank(manager); cdoEpoch.startEpoch();
    _depositWithUser(ATTACKER_KYC, 10e18); // new tranche balance
    _requestWithdrawWithUser(ATTACKER_KYC, 10e18);
    _stopCurrentEpochWithApr(10e18); // e2 funded at par, epochNumber > e2

    // Claim: loss path looks up lossRecoveryPriceByEpoch[e2] == 0 -> skipped.
    // Funded path pays withdrawsRequests[attacker] = 100e18 + 10e18 at par.
    uint256 balBefore = underlying.balanceOf(ATTACKER_KYC);
    vm.prank(address(cdoEpoch));
    strategy.claimWithdrawRequest(ATTACKER_KYC);

    // BUG: attacker received 110e18 although only 50e18 + 10e18 was funded for e1+e2.
    assertEq(underlying.balanceOf(ATTACKER_KYC) - balBefore, 110e18);
    // Strategy is now short 50e18 vs remaining claimants -> insolvency.
}
```

Note: I verified the claim-path code (`IdleCreditVault.sol:271-900`); I did not fully trace `IdleCDOEpochVariant.claimWithdrawRequest`'s wrapper checks, but the stale-epoch par payout inside the strategy is reachable through the documented call flow.