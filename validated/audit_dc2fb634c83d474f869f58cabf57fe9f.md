### Title
Unclaimed withdraw receipts from older epochs escape the `stopEpochWithDuration` loss haircut and are paid at par, draining funded withdraw liquidity - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The external `CollectionShutdown` bug is a stale-params-reuse issue: bookkeeping indexed by a stale key lets an attacker mix old state with a new flow and claim more than was funded. The direct analog exists in `IdleCreditVault`: pending withdraw receipts are tracked per epoch in `withdrawsRequestsByEpoch`, but the loss haircut applied by `collectWithdrawFunds` is keyed to a single epoch (`epochNumber` at stop time), and `_claimLossAdjustedWithdrawRequest` only haircuts the epoch pointed to by `lastWithdrawRequest[_user]`. Receipts recorded in earlier epochs that were still pending (unclaimed) share the same `pendingWithdraws` basis that absorbed the loss, yet they are later paid at par via `_claimFundedWithdrawRequest`. This lets a user claim more underlying than the borrower actually funded, draining the strategy's claim reserve at the expense of other claimers.

### Finding Description
Relevant flow:

1. `requestWithdraw` records the request epoch into `lastWithdrawRequest[_user]`, adds `_amount` into both `withdrawsRequests[_user]` and `withdrawsRequestsByEpoch[_user][currentEpoch]`, and adds `_amount` to the global `pendingWithdraws` (contracts/strategies/idle/IdleCreditVault.sol:243-295).

2. There is no rule forcing a user to claim a matured receipt before requesting again. A user can request in epoch N, never claim, and request again in epoch N+1. `lastWithdrawRequest[_user]` is then N+1 while `withdrawsRequestsByEpoch` holds basis at both N and N+1, all of it inside `pendingWithdraws`.

3. On a lossy `stopEpochWithDuration`, the CDO calls `previewLossAdjustedWithdrawFunds` and `collectWithdrawFunds`. The loss denominator is the *entire* `pendingWithdraws` (all epochs' basis), but the haircut is stored under a single epoch key: `lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice`, and `pendingWithdraws` is zeroed (contracts/strategies/idle/IdleCreditVault.sol:411-430).

4. At claim time, `claimWithdrawRequest` first calls `_claimLossAdjustedWithdrawRequest`, which only looks up `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` — i.e. only the *latest* request epoch — and clears only that epoch's slice via `_clearWithdrawClaimForEpoch` (contracts/strategies/idle/IdleCreditVault.sol:789-837). The older epoch-N slice remains in `withdrawsRequests[_user]` and falls through to `_claimFundedWithdrawRequest`, which burns the receipt and pays the full basis at par via `_transferFundedClaim` (contracts/strategies/idle/IdleCreditVault.sol:319-350).

The broken invariant is the loss waterfall / one-receipt-one-haircut invariant: the borrower funded `pendingBasis - pendingLoss` (the pending bucket's pro-rata share of the realized loss was socialized across *all* pending receipts), but only latest-epoch receipts are haircut. Older-epoch pending receipts redeem 1:1 even though their basis was counted in the loss-absorbing denominator. The excess paid out comes from the strategy contract's underlying balance, which is the shared pool backing all funded claims.

### Impact Explanation
Direct theft / insolvency with quantified loss. If pending basis is `P`, realized loss allocated to the pending bucket is `L_p`, and an attacker's stale-epoch basis is `S`, then:

- funded amount = `P - L_p`
- legitimate haircutted payout to the attacker for their latest-epoch receipt plus full `S` for the stale receipt exceeds their fair share `attackerBasis * (P - L_p) / P` by `S * L_p / P`.

Concretely: attacker requests 100 underlying in epoch N (never claims) and 100 in epoch N+1; other users' pending basis is 800; a stop loss assigns `L_p = 100` to the pending bucket (recovery price 0.9). The strategy receives 900 but the attacker claims `100 * 0.9 + 100 = 190` instead of `180`. The extra 10 (and aggregated across many stale receipts, up to the full loss share of all older-epoch basis) is paid out of other claimers' funded reserves — first claimers are made whole at the expense of later ones, leaving the contract insolvent for the remainder. Because `_clearWithdrawClaimForEpoch` never clears the older `withdrawsRequestsByEpoch` entries, the stale basis is also reused in the same spirit as the external report's stale `CollectionShutdownParams`.

A secondary variant of the same class exists if `epochNumber` is incremented inside `deposit()` (contracts/strategies/idle/IdleCreditVault.sol:607-611) before `collectWithdrawFunds` runs within the same `stopEpoch`: the haircut would be stored under the *new* epoch number while `lastWithdrawRequest` still points at the request epoch, so `lossRecoveryPriceByEpoch[lastWithdrawRequest]` reads 0 and *all* pending receipts bypass the haircut entirely. I could not fully verify the in-`stopEpoch` ordering between the epoch bump and `collectWithdrawFunds` within the available iterations, but the multi-epoch variant above holds regardless of that ordering.

### Likelihood Explanation
- Only unprivileged actions: deposit, `requestWithdraw`, waiting across epochs, and `claimWithdrawRequest` — all available to any KYC-passing lender.
- The trigger (`stopEpochWithDuration` with `_lossAmount > 0` while pending withdrawals exist) is a manager/borrower flow, which is in scope as an honest privileged action the attacker sequences around; it occurs on any partial borrower repayment.
- No guard prevents holding unclaimed receipts across multiple epochs; the only related check (contracts/strategies/idle/IdleCreditVault.sol:263-271) reuses the same stale `lastWithdrawRequest` key and only fires *after* a loss price exists.
- Preconditions: `allowAAWithdrawRequest`/`allowBBWithdrawRequest` enabled and a subsequent lossy stop — both normal operating modes.

### Recommendation
- Apply the recovery price per request epoch to *every* outstanding entry in `withdrawsRequestsByEpoch[_user]` that was part of `pendingWithdraws` at the lossy stop, not only the one keyed by `lastWithdrawRequest[_user]`. E.g., track the set of pending epochs per user or store a global `pendingLossEpoch`/price applied to any unclaimed receipt whose basis contributed to `pendingWithdraws`.
- Alternatively, forbid opening a new `requestWithdraw` while the user has any unclaimed pending receipt (extend the existing check at lines 263-271 to `withdrawsRequests[_user] != 0 || apr0Users[_user].principal != 0`), eliminating multi-epoch pending receipts entirely.
- Ensure the epoch key used by `collectWithdrawFunds` (`lossRecoveryPriceByEpoch[epochNumber]`) is provably the same epoch in which the receipts' `lastWithdrawRequest`/`withdrawsRequestsByEpoch` basis was recorded — i.e. write the haircut under the epoch the requests were made in, before any `epochNumber` increment inside `deposit()` during `stopEpoch`.
- When clearing claims in `_clearWithdrawClaimForEpoch`, also clear or haircut any earlier-epoch entries in `withdrawsRequestsByEpoch[_user]` so stale basis cannot be reused at par.

### Proof of Concept
Foundry fork test (drop into `test/foundry/IdleCreditVault.t.sol` style harness; assumes a running epoch CDO with AA tranche and manager-controlled `stopEpochWithDuration`):

```solidity
function test_StaleEpochReceiptEscapesLossHaircut() public {
    IdleCreditVault cv = IdleCreditVault(address(strategy));
    address attacker = makeAddr('attacker');
    address victim   = makeAddr('victim');

    // Both users deposit during buffer
    _depositWithUser(attacker, 200 * ONE_SCALE, true);
    _depositWithUser(victim,   800 * ONE_SCALE, true);

    // Epoch 1: attacker requests 100, never claims
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(100 * ONE_SCALE, address(AAtranche));

    // stop epoch 1 fully funded, start epoch 2
    _startEpochAndCheckPrices(0);
    deal(defaultUnderlying, borrower, _expectedFundsEndEpoch());
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(initialProvidedApr, 0);   // funds pending, epochNumber -> +1

    _startEpochAndCheckPrices(0);                // buffer then startEpoch

    // Epoch 2: attacker requests another 100 (stale epoch-1 receipt still unclaimed)
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(100 * ONE_SCALE, address(AAtranche));

    // Victim also has pending basis in epoch 2
    vm.prank(victim);
    cdoEpoch.requestWithdraw(800 * ONE_SCALE, address(AAtranche));

    // Lossy stop: borrower repays only part; pending bucket takes its pro-rata loss
    uint256 pending = cv.pendingWithdraws();     // 1000 ether basis
    deal(defaultUnderlying, borrower, /* partial repayment causing loss */ LOSS_AMOUNT);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(LOSS_AMOUNT, 0); // sets lossRecoveryPriceByEpoch[<current epoch>]

    // Attacker claims: epoch-2 slice is haircut, but epoch-1 slice pays at PAR
    uint256 pre = underlying.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimWithdrawRequest();
    uint256 got = underlying.balanceOf(attacker) - pre;

    uint256 fair = 200 * ONE_SCALE * cv.lossRecoveryPriceByEpoch(cv.epochNumber()) / 1e18;
    // Attacker receives strictly more than fair share of the funded amount:
    assertGt(got, fair);
    // funded total < total claims at par -> victim's claim now reverts/underpays (insolvency)
}
```

The assertion `got > fair` demonstrates the stale-epoch receipt escaping the haircut; running the victim's `claimWithdrawRequest` afterward shows the drained reserve (transfer shortfall or insolvency for the remaining funded claims).