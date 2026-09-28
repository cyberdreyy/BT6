### Title
Loss-adjusted withdraw receipts escape their haircut when `lastWithdrawRequest` is overwritten by a newer request — (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault._claimLossAdjustedWithdrawRequest` locates a user's haircutted receipt solely via `lastWithdrawRequest[_user]`, but the haircutted amount is still tracked in the aggregate `withdrawsRequests[_user]` / `withdrawsRequestsByEpoch[_user][epoch]` ledgers. A user who makes a second `requestWithdraw` in a later epoch overwrites `lastWithdrawRequest`, so the loss-adjusted lookup misses (`lossRecoveryPriceByEpoch[newEpoch] == 0`) and the stale haircutted receipt falls through to `_claimFundedWithdrawRequest`, which pays the entire aggregate — including the haircutted epoch's amount — at par. This is the direct analog of a use-after-free: a stale ledger entry remains live and is consumed through the wrong (full-price) path after its epoch-scoped recovery price was orphaned.

### Finding Description
When `stopEpoch` ends an epoch with a realized loss, `IdleCDOEpochVariant` calls `collectWithdrawFunds(_amount)` with `_amount < pendingWithdraws`. The vault then zeroes `pendingWithdraws` and stores a per-epoch haircut:

- `lossRecoveryPriceByEpoch[epochNumber] = _amount * RECOVERY_FULL / pendingBasis` (contracts/strategies/idle/IdleCreditVault.sol:411-430).

Crucially, the individual user receipts are *not* removed from `withdrawsRequests[_user]` or `withdrawsRequestsByEpoch[_user][reqEpoch]` at that point — they are only cleared lazily by `_clearWithdrawClaimForEpoch` when the user claims.

At claim time, `claimWithdrawRequest` runs three stages in order (lines 301-314):
1. `_claimPostDefaultWithdrawRequest` / `_claimDefaultedWithdrawRequest` (only if `defaultRecoveryFinalized`),
2. `_claimLossAdjustedWithdrawRequest` — looks up `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` (lines 789-801),
3. `_claimFundedWithdrawRequest` — pays `withdrawsRequests[_user] + apr0 settled amounts` at par via `_transferFundedClaim` (lines 319-350).

`requestWithdraw` unconditionally sets `lastWithdrawRequest[_user] = currentEpoch` for every new request (line 282) while *adding* to `withdrawsRequests[_user]` and `withdrawsRequestsByEpoch[_user][currentEpoch]` (lines 292-293). Nothing in `requestWithdraw` forces settlement of the previous epoch's loss-adjusted receipt first.

Attack sequence (unprivileged user, no privileged misbehavior):
1. Buffer phase, epoch N running APR > 0: user calls `requestWithdraw` → `withdrawsRequestsByEpoch[user][N] = X`, `lastWithdrawRequest[user] = N`, receipt tokens minted to user.
2. Manager calls `stopEpochWithDuration` with a partial loss; borrower funds only `X * (1 - h)`. `collectWithdrawFunds` stores `lossRecoveryPriceByEpoch[N] < RECOVERY_FULL` and clears `pendingWithdraws`.
3. In epoch N+1 buffer, user calls `requestWithdraw` again with a dust amount (1 wei of tranche). This sets `lastWithdrawRequest[user] = N+1` and adds dust to `withdrawsRequests`.
4. Epoch N+1 ends fully funded.
5. User calls `claimWithdrawRequest`:
   - `_claimLossAdjustedWithdrawRequest` reads `lossRecoveryPriceByEpoch[N+1] == 0` → returns 0; the epoch-N haircutted receipt is never cleared.
   - `_claimFundedWithdrawRequest` passes the `epochNumber > lastWithdrawRequest` gate and pays `withdrawsRequests[user]` — which still contains the full un-haircutted `X` from epoch N — at 1:1.

The user receives `X` instead of `X * lossRecoveryPriceByEpoch[N] / RECOVERY_FULL`, silently escaping `h%` of the socialized loss. The same overwrite also orphans the receipt permanently if the user never claims via the loss path — the haircut is simply never applied.

### Impact Explanation
Direct theft / insolvency: the escaped haircut `X * (1 - lossRecoveryPrice)` is paid out of strategy underlying that was only funded for the reduced amount. Since `pendingWithdraws` was zeroed at loss time, this extra payout draws on funds backing other pending receipts, active LPs, or subsequent withdraw funding, breaking the loss-waterfall invariant ("loss-adjusted receipts claim only at `lossRecoveryPrice`") and causing either insolvency for later claimants or an effective transfer of the loss to honest users. Loss magnitude is bounded by the attacker's requested amount times the realized haircut, and scales with the epoch loss — near-total pending-loss epochs let the attacker recover almost the full par value of a receipt that should be nearly worthless.

### Likelihood Explanation
High whenever `stopEpochWithDuration` realizes a partial loss (`_amount < pendingBasis`) and the affected requester still holds tranche tokens or can acquire dust tranches on the open market to file a second request. `requestWithdraw` has no guard against existing unclaimed receipts (the code even documents that a second request just delays claiming), so the overwrite path is reachable in normal operation — an honest user doing a second withdraw unknowingly triggers it too, meaning the haircut mechanism is unreliable even without malicious intent.

### Recommendation
In `requestWithdraw` (and `requestInstantWithdraw`), before overwriting `lastWithdrawRequest[_user]`, force-settle any pending loss-adjusted receipt for the prior epoch — i.e., invoke the `_clearWithdrawClaimForEpoch` + `lossRecoveryPriceByEpoch` accounting and either pay it out or move it into a dedicated `lossAdjustedClaims[_user]` balance decoupled from `lastWithdrawRequest`. Alternatively, track loss-adjusted receipts by their own per-epoch key (`withdrawsRequestsByEpoch`) rather than the single mutable `lastWithdrawRequest` pointer, so every epoch's haircut is looked up by the epoch in which the request was actually made.

### Proof of Concept
Foundry fork test sketch against `IdleCreditVault` + `IdleCDOEpochVariant` (mirroring `test/foundry/IdleCreditVault.t.sol` helpers `_startEpochAndCheckPrices`, `_stopEpochAndCheckPrices`):

```solidity
function testLossHaircutEscapeViaSecondRequest() external {
    uint256 amount = 10_000 * ONE_SCALE;
    address victim = makeAddr("victim");
    address attacker = makeAddr("attacker");

    // Both deposit in buffer of epoch N
    _depositWithUser(victim, amount, true);
    uint256 attackerTranches = _depositWithUser(attacker, amount, true);

    _startEpochAndCheckPrices(0);

    // Both request withdraw in epoch N
    vm.prank(victim);
    cdoEpoch.requestWithdraw(0, address(AAtranche));
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(0, address(AAtranche));

    // stopEpoch with a partial loss: borrower repays only 50% of pendingWithdraws
    uint256 pending = IdleCreditVault(address(strategy)).pendingWithdraws();
    deal(defaultUnderlying, borrower, pending / 2);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(lossParams); // triggers collectWithdrawFunds(pending/2)

    uint256 epochN = IdleCreditVault(address(strategy)).epochNumber() - 1;
    assertLt(
        IdleCreditVault(address(strategy)).lossRecoveryPriceByEpoch(epochN),
        RECOVERY_FULL,
        "no haircut stored"
    );

    // Attacker files a dust withdraw request in epoch N+1, overwriting lastWithdrawRequest
    // (attacker still holds tranche receipt tokens from the minted-receipt design,
    //  or acquires dust tranches; requestWithdraw mints receipt regardless of pending claims)
    // ... deposit dust or reuse remaining tranche balance, then:
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(1, address(AAtranche)); // dust request, epoch N+1

    // Run epoch N+1 to completion, fully funded
    _startEpochAndCheckPrices(1);
    _stopEpochAndCheckPrices(1, initialProvidedApr, _expectedFundsEndEpoch());

    // Victim claims honestly -> gets haircutted amount
    uint256 victimPre = underlying.balanceOf(victim);
    vm.prank(victim);
    cdoEpoch.claimWithdrawRequest();
    uint256 victimGot = underlying.balanceOf(victim) - victimPre;

    // Attacker claims -> loss-adjusted lookup keyed on N+1 misses,
    // funded path pays full aggregate at par
    uint256 attackerPre = underlying.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimWithdrawRequest();
    uint256 attackerGot = underlying.balanceOf(attacker) - attackerPre;

    uint256 haircut = RECOVERY_FULL -
        IdleCreditVault(address(strategy)).lossRecoveryPriceByEpoch(epochN);

    // Attacker escapes ~haircut% more than the honest victim on identical epoch-N basis
    assertGt(attackerGot, victimGot + amount * haircut / RECOVERY_FULL / 2, "haircut not escaped");
}
```

Key assertion anchors: `withdrawsRequests[attacker]` still contains the epoch-N amount after `collectWithdrawFunds` (only cleared in `_clearWithdrawClaimForEpoch`), `lossRecoveryPriceByEpoch[N+1] == 0`, and `_claimFundedWithdrawRequest` pays `normalAmount = withdrawsRequests[user]` unreduced (lines 338-349).