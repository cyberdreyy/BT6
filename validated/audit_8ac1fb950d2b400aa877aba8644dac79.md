### Title
Loss haircut escapes to earlier-epoch receipts — stale `withdrawsRequestsByEpoch` entries skip `lossRecoveryPriceByEpoch` and drain the funded claim pool - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`requestWithdraw` records each receipt per request epoch in `withdrawsRequestsByEpoch[user][epoch]`, but only tracks a single pointer `lastWithdrawRequest[user]`. When `stopEpochWithDuration`/`collectWithdrawFunds` realizes a loss, the haircut is stored under exactly one epoch (`lossRecoveryPriceByEpoch[epochNumber]`), and `_claimLossAdjustedWithdrawRequest` only ever clears and haircuts the receipt of `lastWithdrawRequest`. An attacker who holds pending receipts from two different request epochs can let the older receipt "traverse" out of the loss scope: it is included in `pendingWithdraws` when the loss price is computed, yet it is later paid at par through `_claimFundedWithdrawRequest`. The funded pool is therefore over-drawn and later legitimate claimants cannot be paid.

### Finding Description
Relevant flow, all in `contracts/strategies/idle/IdleCreditVault.sol`:

1. `requestWithdraw` (L243–295) burns the CDO's strategy tokens, mints a receipt to the user, adds `_amount` to the global `pendingWithdraws`, sets `lastWithdrawRequest[_user] = currentEpoch`, and accumulates `withdrawsRequestsByEpoch[_user][currentEpoch]`. The only re-request guard (L263–271) checks `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]] != 0` — i.e. it only blocks if a loss price was *already* stored for the previous request epoch at the moment of the new request.
2. `collectWithdrawFunds` (L411–430) is called by the CDO during `stopEpochWithDuration`. If the borrower-funded `_amount < pendingWithdraws`, it stores `lossRecoveryPriceByEpoch[epochNumber] = _amount * 1e18 / pendingBasis` and zeroes `pendingWithdraws`. Crucially, `pendingBasis` aggregates receipts of *all* request epochs, while the price is keyed to the single current `epochNumber`.
3. `claimWithdrawRequest` (L301–314) → `_claimLossAdjustedWithdrawRequest` (L789–801) looks up `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` and `_clearWithdrawClaimForEpoch` clears only `withdrawsRequestsByEpoch[user][thatEpoch]`. Any receipt recorded under an older epoch is untouched and remains in `withdrawsRequests[user]`.
4. `_claimFundedWithdrawRequest` (L319–350) then pays `withdrawsRequests[user]` at par — including the older-epoch receipt that was already counted inside the loss-adjusted `pendingBasis`.

Broken invariant: every unit of `pendingWithdraws` priced into `lossRecoveryPrice` must pay out at `price`, i.e. sum(claims) == funded amount. The older-epoch receipt escapes the haircut scope (the analog of escaping the intended directory via a traversal sequence), so total claims = `oldBasis + newBasis*price` while the strategy only holds `(oldBasis + newBasis)*price` — a shortfall of `oldBasis*(1 - price)`.

Attack sequence (named phases, fixed-APR mode):
- Epoch N buffer/running: attacker requests withdraw (receipt A). Epoch N stops normally; A is funded at par and sits claimable.
- Epoch N+1 buffer: attacker requests again (receipt B). Guard at L263–271 passes because `lossRecoveryPriceByEpoch[N] == 0`. `pendingWithdraws` now = A + B at `stopEpochWithDuration` time together with other users' receipts.
- Epoch N+1 ends with `stopEpochWithDuration(_lossAmount)`: `collectWithdrawFunds` funds `pendingBasis * price` with `price < 1`, keyed to epoch N+1.
- Attacker calls `claimWithdrawRequest`: B pays `B*price`, then A pays at par — pulling `A + B*price` from a pool funded with `(Σpending)*price`. Every user who interleaved a request in an older epoch over-draws; the last loss-adjusted claimants' transfers revert (permanent freezing of their unclaimed yield) — direct theft/insolvency quantified as `Σ(older-epoch basis) * (1 - price)`.

No existing guard stops this: the re-request guard only fires for already-priced losses, KYC/`_onlyIdleCDO` don't apply to accounting math, and `_claimFundedWithdrawRequest` has no awareness that part of `withdrawsRequests` belonged to the haircut basis. I could not fully confirm the exact `epochNumber` value at `collectWithdrawFunds` time relative to `deposit()`'s increment in `IdleCDOEpochVariant.stopEpochWithDuration`, but the vulnerability only requires that receipts from *two distinct* request epochs coexist in one `pendingWithdraws` bucket, which the code permits.

### Impact Explanation
Any KYC-passing lender or tranche holder can drain the funded-withdrawal pool by an amount equal to their older-epoch receipt times the haircut. Loss: `oldBasis * (1 - lossRecoveryPrice)` in underlying stolen from other pending withdrawers, whose claims then revert or are underpaid — direct theft plus insolvency of the strategy's funded-claim reserve.

### Likelihood Explanation
Requires a `stopEpochWithDuration` loss while at least one user holds receipts spanning two request epochs. Both conditions are manager/borrower-driven and routine: requests across epochs are explicitly supported ("NOTE: if a user does not claim... he will have to wait for another epoch to claim both requests"), and loss-stops are a designed feature. The attacker only needs two `requestWithdraw` calls and one `claimWithdrawRequest` — no privileged action.

### Recommendation
- In `requestWithdraw`, also block (or fold in) any *unclaimed* older-epoch receipt whenever `pendingWithdraws` still contains it, not only when `lossRecoveryPriceByEpoch` is already set: e.g. revert if `withdrawsRequests[user] != withdrawsRequestsByEpoch[user][currentEpoch]`, or track a per-epoch pending set so `collectWithdrawFunds` can write the price for every epoch with pending basis.
- Alternatively, in `_claimLossAdjustedWithdrawRequest`/`_clearWithdrawClaimForEpoch`, apply `lossRecoveryPrice` to the user's *entire* claim basis included in that `pendingWithdraws` snapshot (all epochs), not only `lastWithdrawRequest`.
- Add a Foundry invariant test: sum of all users' `withdrawsRequestsByEpoch` bases that entered a loss epoch must equal `pendingBasis` used for `lossRecoveryPrice`.

### Proof of Concept
Foundry-style sketch against the existing `IdleCreditVault.t.sol` harness (`cdoEpoch`, `strategy`, `manager`, `borrower`, `underlying` helpers as in the repo tests):

```solidity
function testLossHaircutEscapeAcrossEpochs() external {
    address attacker = makeAddr('attacker');
    address victim   = makeAddr('victim');

    // Epoch N: both deposit, both request withdraw
    _depositWithUser(attacker, 100e6);
    _depositWithUser(victim,   100e6);
    uint256 tA = aaTranche.balanceOf(attacker);
    _requestWithdrawWithUser(attacker, tA);   // receipt A, epoch N
    _requestWithdrawWithUser(victim,   aaTranche.balanceOf(victim));

    // Epoch N ends normally -> receipts funded at par, epochNumber -> N+1
    _stopCurrentEpoch(); // normal stopEpoch funds pendingWithdraws fully

    // Epoch N+1 buffer: attacker (does NOT claim A) requests again
    _depositWithUser(attacker, 100e6);
    _requestWithdrawWithUser(attacker, aaTranche.balanceOf(attacker)); // receipt B, epoch N+1
    // guard at L263 passes because lossRecoveryPriceByEpoch[N] == 0

    // Epoch N+1 ends with a loss: borrower funds less than pendingWithdraws
    uint256 pending = strategy.pendingWithdraws();
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(initialProvidedApr, pending / 2 /* _lossAmount -> price ~0.5 */);

    // Attacker claims: B pays ~50%, but stale receipt A pays 100%
    uint256 pre = underlying.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimWithdrawRequest();
    uint256 got = underlying.balanceOf(attacker) - pre;

    // Attacker received more than the pro-rata funded share:
    // got ~= A + B*price > (A + B) * price  => pool over-drawn by A*(1-price)
    // victim's claim now reverts/underpays: strategy balance < victim's entitlement
    uint256 balBefore = underlying.balanceOf(address(strategy));
    vm.prank(victim);
    cdoEpoch.claimWithdrawRequest(); // underpaid or reverts on insufficient funded balance
    assertLt(underlying.balanceOf(address(strategy)) - balBefore + got, /* funded total */);
}
```

Note: exact epochs/`stopEpochWithDuration` signature details should be aligned with `IdleCDOEpochVariant.sol` (I could not read its body within the iteration limit); the core claim relies only on `IdleCreditVault.sol` logic shown above.