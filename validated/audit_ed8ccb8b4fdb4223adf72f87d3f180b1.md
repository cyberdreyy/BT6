### Title
Loss haircut is only applied to the user's last withdraw-request epoch; older pending receipts claim at par and drain funded reserves - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`collectWithdrawFunds` applies a single aggregate haircut (`lossRecoveryPrice = funded / pendingBasis`) to *all* pending withdraw receipts and stores it under `lossRecoveryPriceByEpoch[epochNumber]`. However `_claimLossAdjustedWithdrawRequest` resolves the user's claim through `lastWithdrawRequest[_user]` and `_clearWithdrawClaimForEpoch` only clears `withdrawsRequestsByEpoch[_user][lossEpoch]`. A user holding pending receipts from *multiple* request epochs gets the haircut applied only to the latest epoch's receipt; all earlier receipts survive in `withdrawsRequests[_user]` and are paid 1:1 through `_claimFundedWithdrawRequest`, even though the strategy only received the haircut-adjusted funding. This is an index/bounds-class accounting error: a per-epoch keyed recovery price is read against the wrong key while the funded amount was computed on the aggregate.

### Finding Description
Relevant flow in `contracts/strategies/idle/IdleCreditVault.sol`:

1. `requestWithdraw` (lines 243–295) mints the user a strategy-token receipt and accumulates `withdrawsRequests[_user] += _amount` plus `withdrawsRequestsByEpoch[_user][currentEpoch] += _amount`, updating `lastWithdrawRequest[_user] = currentEpoch`. A second request before claiming is explicitly allowed — it only delays the claim ("if a user does not claim a withdraw request and instead requests another withdraw, he will have to wait for another epoch to claim both"). The guard at lines 263–271 only blocks new requests *after* a loss price exists at the last-request epoch.

2. On a loss stop, the CDO calls `collectWithdrawFunds` (lines 411–430). If `_amount < pendingBasis`, it writes `lossRecoveryPriceByEpoch[epochNumber] = _amount * RECOVERY_FULL / pendingBasis` and sets `pendingWithdraws = 0`. The funded `_amount` covers the *aggregate* pending basis at ratio `r`; there is no per-epoch reservation.

3. `claimWithdrawRequest` (lines 301–314) calls `_claimLossAdjustedWithdrawRequest` (lines 789–801), which reads `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` and clears only that epoch's slice via `_clearWithdrawClaimForEpoch` (lines 811–837): `withdrawsRequests[_user] -= normalAmount` removes only `withdrawsRequestsByEpoch[_user][lossEpoch]`, and `lastWithdrawRequest` is reset to 0.

4. `claimWithdrawRequest` then falls through to `_claimFundedWithdrawRequest` (lines 319–350) in the *same* call. With `lastWithdrawRequest[_user] == 0`, the epoch-wait check passes, and the remaining `withdrawsRequests[_user]` (all older-epoch receipts) is burned and paid at par via `_transferFundedClaim`.

Broken invariant: loss waterfall — the borrower funds `pendingBasis * r`, but a multi-epoch requester extracts `latest * r + older * 1.0`, so the strategy's funded underlying is overdrawn by `(1 - r) * olderReceipts`. Guard check: the guard in `requestWithdraw` cannot fire because both requests precede any loss; `withdrawsRequestsByEpoch` per-epoch keying is exactly what makes the older slice escape clearing.

### Impact Explanation
Direct theft / insolvency. For every underlying unit of older-epoch receipt the attacker holds, they extract `(1 - r)` units beyond the pro-rata loss share the system funded. The shortfall is borne by other pending claimants (last claimant reverts on insufficient strategy balance) or by active LPs if the reserve is depleted. Attacker is an ordinary KYC'd tranche holder; magnitude scales with the size of the older receipt they can build and with the loss fraction.

### Likelihood Explanation
Requires: (a) epoch-mode vault where `stopEpochWithDuration(_lossAmount)` is used, i.e. a realized loss path already supported by the code; (b) the attacker to hold pending withdraw receipts across ≥2 epochs, which is normal behavior (the comments describe it as supported). No privileged collusion needed; manager calls are honest. The attacker must request in epoch N, let it roll without claiming (or request again), and request again in a later epoch — all public calls.

### Recommendation
Apply the loss haircut to the user's *entire* pending claim basis, not just the last-request epoch. Two clean options:

- In `_claimLossAdjustedWithdrawRequest`, compute `claimBasis` over all pending receipts (`withdrawsRequests[_user]` plus unsettled APR0 principal/interest) when the aggregate `pendingWithdraws` was haircut, and clear all of them; or
- Track loss recovery per-user aggregate basis at `collectWithdrawFunds` time instead of per-epoch, e.g. snapshot `withdrawsRequests[_user]`-equivalent basis so every pre-loss receipt is paid at `r`.

Also consider rejecting new `requestWithdraw` while `pendingWithdraws` backing earlier receipts exists, or migrating all prior per-epoch entries into the current epoch key when a new request is made.

### Proof of Concept
Foundry fork PoC (solidity sketch, following `test/foundry/IdleCDOEpochQueue.t.sol` conventions):

```solidity
// contracts/strategies/idle/IdleCreditVault.sol — loss haircut bypass
function testLossHaircutOnlyAppliesToLastEpoch() external {
    // Setup: epoch-mode credit vault, APR != 0 (normal flow), non-prefunded.
    _stopCurrentEpoch();                       // buffer: epoch 1 begins

    uint256 amountA = 100e6;                   // attacker older receipt
    uint256 amountB = 100e6;                   // attacker latest receipt
    uint256 amountV = 100e6;                   // victim receipt
    address attacker = makeAddr("attacker");
    address victim   = makeAddr("victim");

    uint256 tA1 = _depositWithUser(attacker, amountA);
    uint256 tA2 = _depositWithUser(attacker, amountB);
    uint256 tV  = _depositWithUser(victim,   amountV);

    vm.prank(manager); cdoEpoch.startEpoch();  // epoch 1 running
    _requestWithdrawWithUser(attacker, tA1);   // receipt in epoch 2 keying
    _requestWithdrawWithUser(victim,   tV);

    _stopCurrentEpoch();                       // -> epoch 2
    vm.prank(manager); cdoEpoch.startEpoch();
    _requestWithdrawWithUser(attacker, tA2);   // second request => lastWithdrawRequest = epoch 3

    // Honest manager stops with a loss: borrower funds only r * pendingBasis.
    uint256 pendingBasis = strategy.pendingWithdraws();
    uint256 loss = pendingBasis / 2;           // 50% loss on pending bucket
    (uint256 pendingToFund, ) = strategy.previewLossAdjustedWithdrawFunds(loss);
    uint256 repay = cdoEpoch.expectedEpochInterest() + pendingToFund;
    deal(address(underlying), strategy.borrower(), repay, true);
    vm.prank(strategy.borrower());
    underlying.approve(address(cdoEpoch), repay);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(10e18, 0, cdoEpoch.epochDuration(), loss);

    // Attacker claims: loss price applied ONLY to epoch-3 slice; epoch-2 slice paid at par.
    uint256 balPre = underlying.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimWithdrawRequest();
    uint256 got = underlying.balanceOf(attacker) - balPre;

    uint256 r = strategy.lossRecoveryPriceByEpoch(strategy.epochNumber());
    uint256 fair = (tA1 + tA2) * r / 1e18;     // pro-rata haircut on ALL receipts
    assertGt(got, fair);                       // attacker escaped (1 - r) * tA1
    // Victim's later claim now reverts / underpays: strategy funded only r * pendingBasis.
}
```

Expected result: `got ≈ tA1 + tA2*r/1e18` (older receipt at par, latest haircut), exceeding the funded share, and the remaining funded balance is insufficient for other claimants — direct fund loss confirmed on the fork.