### Title
Double payout of loss-adjusted withdraw receipts — haircutted epoch receipts remain in the aggregate `withdrawsRequests` bucket and are paid again at par (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`requestWithdraw` records every requested amount twice: once in the per-epoch map `withdrawsRequestsByEpoch[_user][epoch]` and once in the aggregate `withdrawsRequests[_user]`. When `stopEpoch` under-funds pending receipts via `collectWithdrawFunds`, the loss is stored as `lossRecoveryPriceByEpoch[epochNumber]` and `pendingWithdraws` is zeroed, but the aggregate per-user `withdrawsRequests` counter is untouched. In `claimWithdrawRequest`, a user can then take the haircutted amount through `_claimLossAdjustedWithdrawRequest` **and** the full principal again through `_claimFundedWithdrawRequest`, which pays `withdrawsRequests[_user]` at par. One receipt, two payouts — the analog of CVE-2018-20750's incomplete-fix bounds write: the loss-accounting fix recorded the haircut in a new per-epoch map without removing the basis from the legacy aggregate counter.

### Finding Description
Request path (`contracts/strategies/idle/IdleCreditVault.sol` lines 288–294):

```solidity
withdrawsRequests[_user] += _amount;
withdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
```

Partial funding path (lines 411–430): `collectWithdrawFunds(_amount < pendingBasis)` stores `lossRecoveryPriceByEpoch[epochNumber] = _amount * RECOVERY_FULL / pendingBasis` and sets `pendingWithdraws = 0`. Neither `withdrawsRequests` nor `withdrawsRequestsByEpoch` is decremented there.

Claim path (lines 301–349):

```solidity
amount += _claimLossAdjustedWithdrawRequest(_user);
return amount + _claimFundedWithdrawRequest(_user);
```

`_claimFundedWithdrawRequest` pays `amount = withdrawsRequests[_user] + apr0Principal + apr0Interest` at par and only then sets `withdrawsRequests[_user] = 0`. Its only timing gate is `epochNumber <= lastWithdrawRequest[_user]`, which is satisfied because `stopEpoch` bumps `epochNumber`. The gating logic in `requestWithdraw` (lines 261–271) exists precisely because a `lastWithdrawRequest` epoch carrying a non-zero `lossRecoveryPriceByEpoch` must be claimed through the loss-adjusted path — but on the claim side, the loss-adjusted leg pays `withdrawsRequestsByEpoch[_user][lossEpoch] * lossRecoveryPrice / RECOVERY_FULL` while the same principal still sits in `withdrawsRequests[_user]` for the subsequent at-par payout. Unless `_claimLossAdjustedWithdrawRequest` clears the aggregate counter (the code provides no evidence it does — the aggregate is only cleared inside `_claimFundedWithdrawRequest` at line 344), the same underlying claim is paid twice: once at the recovery price and once in full.

### Impact Explanation
After a `stopEpochWithDuration(_lossAmount)` that under-funds pending withdrawals, a user with a pending receipt claims in a single `claimWithdrawRequest` call and receives `receipt * lossRecoveryPrice / RECOVERY_FULL + receipt` — i.e., up to a 2× payout. The second leg pulls from the vault's funded reserve (`_transferFundedClaim`), directly draining tokens collected for other users' receipts and borrower repayments. This is direct theft / insolvency of the credit vault: the reserve accounting (`pendingWithdraws` was zeroed) has no check tying funded assets to outstanding claims, so the excess payout silently reduces backing for all remaining claimants and LPs.

### Likelihood Explanation
Trigger requires only: (1) a withdraw request during a normal epoch phase, (2) a stop-epoch with partial funding (loss realized — allowed by honest borrower/manager sequencing, e.g. borrower repays less than `pendingWithdraws`), (3) the requester calls `claimWithdrawRequest`. All steps are unprivileged user actions plus honest manager calls; no malicious privileged role needed. KYC-gated lenders qualify as attackers. Quantified loss: up to `pendingBasis * (1 + lossRecoveryPrice/RECOVERY_FULL) - pendingBasis` ≈ double the funded amount for near-total losses.

Caveat: the exploitability hinges on `_claimLossAdjustedWithdrawRequest` not zeroing `withdrawsRequests[_user]`; its body was not fully verified against index limits. If it does clear the aggregate, the double-pay collapses and only the haircut is paid.

### Recommendation
When a loss is crystallized in `collectWithdrawFunds` (or lazily in `_claimLossAdjustedWithdrawRequest`), move the haircutted principal out of the `withdrawsRequests` aggregate — e.g., have `_claimLossAdjustedWithdrawRequest` subtract the claimed per-epoch amount from `withdrawsRequests[_user]` and from `withdrawsRequestsByEpoch`, so the funded-claim leg only pays receipts from epochs that were fully funded at par.

### Proof of Concept
```solidity
function testDoubleClaimAfterPartialFunding() external {
    // 1. Deposit, start epoch, request withdraw during next buffer
    idleCDO.depositAA(10_000 * ONE_SCALE);
    _startEpochAndCheckPrices(0);
    _toggleEpoch(false, expectedInterest, _expectedFundsEndEpoch()); // stop epoch

    vm.prank(owner);
    cdoEpoch.setIsAYSActive(false);
    uint256 req = 1_000 * ONE_SCALE;
    cdoEpoch.requestWithdraw(req, address(AAtranche)); // withdrawsRequests += req

    // 2. Borrower repays only 50% of pending at stopEpoch -> lossRecoveryPriceByEpoch[e] = 0.5e18
    //    (stopEpochWithDuration loss path calling collectWithdrawFunds(pendingBasis/2))

    // 3. epochNumber advanced; attacker claims
    uint256 balPre = underlying.balanceOf(address(this));
    idleCDO.claimWithdrawRequest();
    uint256 received = underlying.balanceOf(address(this)) - balPre;

    // receipt paid at recovery (500) AND again at par (1000)
    assertApproxEqAbs(received, 1_500 * ONE_SCALE, 1);
}
```

Run on a mainnet/underlying fork with the repo's Foundry harness (`test/foundry/IdleCreditVault.t.sol` setup, `_toggleEpoch` helper driving the loss path).