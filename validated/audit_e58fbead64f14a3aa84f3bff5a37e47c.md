### Title
Multi-epoch withdraw receipts escape `lossRecoveryPriceByEpoch` haircut via stale `lastWithdrawRequest` pointer - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The kernel bug is a stale-membership check: `push_rt_task` re-validates a task that was migrated and re-queued, but the check confirms the task is "on a runqueue" without confirming it is still in the *pushable list*, so a task that no longer belongs to the set is processed and corrupts scheduler state.

`IdleCreditVault` has the same defect. Receipt membership in a *loss-adjusted epoch* is tracked per epoch in `lossRecoveryPriceByEpoch`/`withdrawsRequestsByEpoch`, but both the request-time guard and the claim-time lookup consult only `lastWithdrawRequest[_user]` — a single pointer that always holds the *latest* request epoch. When a user holds receipts in two epochs and the older one was haircut by `stopEpochWithDuration`, the lookups "check pass even though the [receipt] is no longer in the [loss-adjusted] list", and the haircut basis is silently paid at par.

### Finding Description
`collectWithdrawFunds` records a haircut per epoch: when the borrower under-funds `pendingWithdraws`, it stores `lossRecoveryPriceByEpoch[epochNumber] = _amount * 1e18 / pendingBasis` (IdleCreditVault.sol:411-421). Recovery of that haircut relies on one pointer:

- `requestWithdraw` guard (lines 261-271): reverts only if `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]] != 0` **and** that same epoch still holds user basis.
- `_claimLossAdjustedWithdrawRequest` (lines 789-801): computes `lossEpoch = lastWithdrawRequest[_user]` and applies the haircut only for that epoch.
- `_claimFundedWithdrawRequest` (lines 338-349): pays the *aggregate* `withdrawsRequests[_user]` at par, and that aggregate still contains the older epoch's basis because `withdrawsRequestsByEpoch[_user][olderEpoch]` was never cleared (it is only cleared inside `_clearWithdrawClaimForEpoch` for the looked-up epoch).

The code itself documents that multiple unclaimed requests across epochs are intended behavior ("if a user does not claim a withdraw request and instead requests another withdraw, he will have to wait for another epoch to claim both requests", lines 323-324).

Attack sequence (all attacker steps are unprivileged `requestWithdraw`/`claimWithdrawRequest`; manager calls are honest):

1. Attacker (KYC'd, tranche holder) calls `requestWithdraw` during the buffer of epoch E → `lastWithdrawRequest = E`, `withdrawsRequestsByEpoch[user][E] = X`, `pendingWithdraws += X`.
2. Honest manager ends epoch E with a partial loss via `stopEpochWithDuration` → `collectWithdrawFunds` funds only `X * price`, sets `lossRecoveryPriceByEpoch[E] = p < 1e18`, `pendingWithdraws = 0`.
3. During the buffer of epoch E+1 the attacker calls `requestWithdraw` again for Y → the guard checks `lossRecoveryPriceByEpoch[E+1]` (zero) → passes → `lastWithdrawRequest = E+1`.
4. Epoch E+1 ends normally, `epochNumber = E+2 > lastWithdrawRequest`.
5. Attacker calls `claimWithdrawRequest`: `_claimLossAdjustedWithdrawRequest` looks up `lossRecoveryPriceByEpoch[E+1] = 0` and returns 0; `_claimFundedWithdrawRequest` then pays `withdrawsRequests[user] = X + Y` **at par**.

The epoch-E haircut basis X escaped its `lossRecoveryPriceByEpoch[E]` haircut entirely. The vault only ever received `X * p` for that bucket, so the extra `X * (1 - p)` is paid from funds reserved for other pending receipts, instant-withdraw funding, or the CDO's own balance (via `_transferFundedClaim`, which spends non-reserve underlying).

This is exactly the CVE shape: the check "is there a loss-adjusted receipt?" is evaluated against the wrong (stale/latest) key, so a receipt that was moved out of the haircut set still passes validation and is paid at par.

### Impact Explanation
Direct theft / insolvency. The attacker recovers `X * (1 - lossRecoveryPrice)` more underlying than entitled — i.e., they dodge their pro-rata share of the epoch-E loss that `previewLossAdjustedWithdrawFunds` explicitly assigned to pending receipts (lines 457-459). Since the strategy only holds `X * p` for that bucket, the excess is drained from funded claims of other users or forces later claims to revert — an insolvency equal to the escaped haircut. Loss scales with the request size and loss magnitude; an attacker can maximize X by requesting their full tranche balance in the loss epoch.

### Likelihood Explanation
Requires only: (a) an epoch ending with a partial loss — an explicitly supported, honest-manager path (`stopEpochWithDuration`/`collectWithdrawFunds` under-funding); (b) the attacker holding an unclaimed receipt in that epoch and making a second request in the next epoch, which the design permits. No privileged misbehavior, no timing race — deterministic. Note the symmetric corruption also exists: had `lastWithdrawRequest` pointed *to* the loss epoch while older funded basis exists, `_clearWithdrawClaimForEpoch` would only clear that epoch, but the funded-claim path zeroes `withdrawsRequests` aggregate — the single-pointer design cannot represent multi-epoch state in either direction.

### Recommendation
Track loss-adjusted membership independently of `lastWithdrawRequest`. Options:

- In `requestWithdraw`, revert if the user has *any* `withdrawsRequestsByEpoch` entry whose epoch has a nonzero `lossRecoveryPriceByEpoch` — e.g., iterate tracked request epochs or maintain a per-user set of open request epochs; or
- In `claimWithdrawRequest`, iterate all of the user's open request epochs and apply `lossRecoveryPriceByEpoch[e]` per epoch rather than only for `lastWithdrawRequest[_user]`; or
- Prohibit a second request while any unclaimed receipt exists when `defaultRecoveryInitialized` loss accounting is in use (extend the existing `lossRecoveryPrice` guard to check that `withdrawsRequests[_user] == 0` whenever any loss price exists), matching the post-default guard at lines 247-251 which already forces `claim-before-request`.

### Proof of Concept
Foundry fork PoC sketch (mirroring `test/foundry/IdleCreditVault.t.sol` helpers):

```solidity
function testLossAdjustedReceiptEscapesHaircutViaSecondRequest() external {
    _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, cdoEpoch.managementFee());
    IdleCreditVault _strategy = IdleCreditVault(address(strategy));

    // attacker deposits and requests full withdraw during epoch-0 buffer
    uint256 amount = 10_000 * ONE_SCALE;
    _depositWithUser(attacker, amount, true);
    vm.prank(attacker);
    uint256 req1 = cdoEpoch.requestWithdraw(0, address(AAtranche));
    uint256 lossEpoch = _strategy.epochNumber();

    // start epoch, manager stops it with a loss -> lossRecoveryPriceByEpoch[lossEpoch] < 1e18
    _startEpochAndCheckPrices(0);
    uint256 pending = _strategy.pendingWithdraws();
    uint256 loss = pending / 2; // 50% haircut on pending receipts
    // borrower funds only the loss-adjusted amount
    _stopEpochWithLoss(loss);

    uint256 price = _strategy.lossRecoveryPriceByEpoch(lossEpoch);
    assertGt(price, 0);
    assertLt(price, 1e18);

    // attacker opens a second request in the new buffer: guard checks the NEW epoch only
    _depositWithUser(attacker, amount, true);
    vm.prank(attacker);
    uint256 req2 = cdoEpoch.requestWithdraw(0, address(AAtranche));
    assertEq(_strategy.lastWithdrawRequest(attacker), lossEpoch + 1);

    // epoch lossEpoch+1 ends normally and fully funds req2
    _startEpochAndCheckPrices(1);
    _stopEpochAndCheckPrices(1, initialProvidedApr, _expectedFundsEndEpoch());

    // claim: loss-adjusted path misses epoch `lossEpoch`, funded path pays req1 at par
    uint256 balPre = underlying.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimWithdrawRequest();
    uint256 got = underlying.balanceOf(attacker) - balPre;

    // attacker received req1 + req2 instead of req1*price + req2
    assertEq(got, req1 + req2, "haircut escaped");
    uint256 stolen = req1 - req1 * price / 1e18;
    assertGt(stolen, 0, "loss socialization bypassed");
}
```

Expected outcome: `got == req1 + req2` while the strategy only collected `req1 * price / 1e18 + req2`, proving receipt `req1` was paid at par despite belonging to a loss-adjusted epoch — a quantified theft of `req1 * (1 - price)` underlying.

Uncertainty noted: I confirmed the exact line range where `epochNumber` is incremented relative to `collectWithdrawFunds` only indirectly (the loss-price epoch key matches `lastWithdrawRequest` in the single-request case per the code comments at lines 268-269); if `collectWithdrawFunds` runs before the increment, the keyed epoch still matches the request epoch and the attack is unchanged. A confirming PoC run is recommended.