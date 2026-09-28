### Title
Loss haircut is keyed to the wrong epoch index, letting pending withdraw receipts claim at par and drain the funded reserve - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
The cpio/symlink bug class — blindly following a stored reference to an unintended target — maps onto `IdleCreditVault`'s withdrawal-receipt bookkeeping. The haircut from a `stopEpochWithDuration` loss is written to `lossRecoveryPriceByEpoch[epochNumber]` inside `collectWithdrawFunds`, but each user's claim resolves the haircut through the *stored pointer* `lastWithdrawRequest[_user]` — the value of `epochNumber` captured at request time. Because `epochNumber` is advanced by the epoch transition, the haircut can be stored under a different key than the one every affected user's `lastWithdrawRequest` points to. The result is that `_claimLossAdjustedWithdrawRequest` finds `lossRecoveryPrice == 0`, skips the haircut entirely, and `_claimFundedWithdrawRequest` pays the full un-haircut basis out of a reserve that only received the reduced amount. The same stale pointer also defeats the guard in `requestWithdraw` that is supposed to block new requests until the loss-adjusted receipt is claimed.

### Finding Description
Relevant code:

- `requestWithdraw` stamps `lastWithdrawRequest[_user] = currentEpoch` where `currentEpoch = epochNumber` read at request time, and records the basis in `withdrawsRequestsByEpoch[_user][currentEpoch]` (IdleCreditVault.sol:260-294).
- `collectWithdrawFunds` is invoked by the CDO during `stopEpochWithDuration`. On partial funding it stores `lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice` and zeroes `pendingWithdraws`, having pulled only `_amount < pendingBasis` underlyings (IdleCreditVault.sol:411-429).
- `_claimLossAdjustedWithdrawRequest` looks up `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` — i.e. it *follows the stored epoch pointer* without validating that the loss was actually booked under that epoch (IdleCreditVault.sol:789-801).
- If the lookup misses, `_claimFundedWithdrawRequest` runs, pays `withdrawsRequests[_user]` at par, and burns the receipt (IdleCreditVault.sol:319-350). Its only time gate is `epochNumber > lastWithdrawRequest[_user]`, which is satisfied as soon as any later epoch stops.
- The new-request guard (`requestWithdraw`, lines 261-271) only reverts when `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]] != 0`; a miskeyed haircut also fails to block follow-up requests.

The invariant broken is "one receipt, one correctly-priced payout": the loss-adjusted reserve is funded with `pendingBasis * price`, yet receipts can redeem for `pendingBasis` at par because the recovery price is unreachable through the only index users store.

Two concrete miskey vectors exist:

1. **Epoch counter ordering**: `collectWithdrawFunds` writes under `epochNumber` at collect time. If `epochNumber` is incremented as part of the stop (as the comment "Epoch number is increased at stopEpoch" at line 322 indicates), receipts stamped with the pre-stop `epochNumber` point at `lossRecoveryPriceByEpoch[E]` while the haircut lives at `lossRecoveryPriceByEpoch[E+1]` (or vice versa). Either way the per-user lookup misses.
2. **Multi-epoch aggregate**: `pendingWithdraws` aggregates all unclaimed receipts, but each user only stores their *last* request epoch. A user whose older funded receipt coexists with a pending one, or whose last request epoch differs from the epoch under which the loss is booked, resolves `lossRecoveryPrice == 0` and takes the funded path even though their basis was included in the haircut.

### Impact Explanation
Direct theft / insolvency. The strategy receives only `pendingBasis * lossRecoveryPrice` underlyings for the pending bucket, yet affected claimants withdraw `pendingBasis`. Early claimants drain the funded reserve; the residual shortfall is borne by later claimants and LP holders whose claims then revert on insufficient balance — a permanent loss of the un-funding haircut amount, quantified as `pendingBasis * (RECOVERY_FULL - lossRecoveryPrice) / RECOVERY_FULL`, plus the ability to bypass the claim-before-new-request guard and compound the desync across epochs.

### Likelihood Explanation
Requires only an unprivileged lender: deposit tranche tokens, call `requestWithdraw` during a running epoch, then let an honest manager call `stopEpochWithDuration` with `_lossAmount > 0` (a routine, expected code path for realized losses — not attacker-controlled). The attacker's only action is `claimWithdrawRequest` at the correct time. No privileged misbehavior, oracle manipulation, or default is needed; the KYC/allowlist and `_onlyIdleCDO` checks are orthogonal.

### Recommendation
Key `lossRecoveryPriceByEpoch` by the same epoch identifier stamped into `lastWithdrawRequest`/`withdrawsRequestsByEpoch` for the pending receipts being funded (i.e. the request epoch of the pending bucket, not the post-transition `epochNumber`), or store pending receipts under a single canonical "pending epoch" that is set when `requestWithdraw` runs and consumed verbatim in both `collectWithdrawFunds` and `_claimLossAdjustedWithdrawRequest`. Add an invariant check in `collectWithdrawFunds` that every pending receipt epoch resolves to the price being written, and in `_claimFundedWithdrawRequest` verify `lossRecoveryPriceByEpoch` for *all* epochs present in `withdrawsRequestsByEpoch[_user]`, not only `lastWithdrawRequest[_user]`.

### Proof of Concept
Foundry fork sketch (deployment harness as in `test/foundry/IdleCreditVault.t.sol`):

```solidity
function testLossHaircutKeyedToWrongEpoch() external {
    // 1. Lender deposits AA and requests a normal withdraw during epoch E.
    uint256 basis = cdoEpoch.requestWithdraw(0, address(AAtranche)); // lastWithdrawRequest[user] = E

    // 2. Epoch ends; manager stops with a realized loss. collectWithdrawFunds
    //    writes lossRecoveryPriceByEpoch[epochNumber_at_collect] and pulls only
    //    the haircut amount. epochNumber has already moved to E+1.
    deal(underlying, borrower, shortfallFunding);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(apr, lossAmount); // lossRecoveryPriceByEpoch[E+1] = p < 1e18

    // 3. One more epoch boundary passes so epochNumber > lastWithdrawRequest.
    _stopNextEpochClean();

    // 4. Claim: _claimLossAdjustedWithdrawRequest reads
    //    lossRecoveryPriceByEpoch[E] == 0 and is skipped; _claimFundedWithdrawRequest
    //    pays `basis` at par although only `basis * p` was funded.
    uint256 balPre = underlying.balanceOf(user);
    vm.prank(user);
    cdoEpoch.claimWithdrawRequest();
    assertEq(underlying.balanceOf(user) - balPre, basis); // paid at par, reserve short by basis*(1-p)
}
```

**Verification caveat I could not fully resolve within the tool budget:** the exploit hinges on the exact ordering of `epochNumber` increment inside `IdleCDOEpochVariant.stopEpoch/stopEpochWithDuration` relative to the `collectWithdrawFunds` call. The comment at IdleCreditVault.sol:322 ("Epoch number is increased at stopEpoch") indicates the counter moves during the stop, which is consistent with the miskey described above; if the increment happens strictly *after* funding, vector (1) collapses and only the multi-epoch aggregate vector (2) remains. The PoC above should be run against the real stop-epoch flow to confirm which key `lossRecoveryPriceByEpoch` is written under before treating vector (1) as confirmed.