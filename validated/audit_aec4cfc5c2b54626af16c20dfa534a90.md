### Title
Settled APR0 withdraw receipts escape `stopEpochWithDuration` loss haircut while still inflating the loss basis — funded-claim path pays them at par, breaking recovery accounting - ([File: contracts/strategies/idle/IdleCreditVault.sol](https://github.com/EzraCole/idle-tranches--025/blob/main/contracts/strategies/idle/IdleCreditVault.sol))

### Summary
`IdleCreditVault` tracks two buckets for pending withdraws: the aggregate `pendingWithdraws` (in underlying) and per-epoch receipt records (`withdrawsRequestsByEpoch`, `apr0Users`). When a lossy `stopEpochWithDuration` occurs, `collectWithdrawFunds` computes `lossRecoveryPriceByEpoch[epochNumber] = funded / pendingWithdraws` and zeroes `pendingWithdraws` (lines 411-430). However, an APR0 receipt that was already *settled* by `_settleApr0` (principal moved from `apr0User.principal` to `apr0User.settledPrincipal`, lines 545-565) is still included in `pendingWithdraws` — the `+= _amount` at request time (line 279) is never decremented on settlement — yet is no longer visible to `_withdrawClaimAmountsForEpoch`, which only looks at open `apr0User.principal` with matching `principalEpoch` (lines 862-881). The loss-adjusted claim therefore returns 0 for that user's epoch, and `_claimFundedWithdrawRequest` pays `settledPrincipal + settledInterest` at par (lines 338-349).

### Finding Description
Bug class analog (use-after-free → stale/freed state reused in accounting): the APR0 principal is "freed" from the open bucket (`apr0TotalPrincipal = 0`, `principal = 0`) at settlement, but its stale contribution to `pendingWithdraws` is reused as loss-absorbing basis while the actual claim escapes the haircut.

Concretely:

1. `requestWithdraw` in APR0 mode adds `_amount` to `pendingWithdraws` and records it in `apr0Users` — never in `withdrawsRequestsByEpoch` (lines 277-294).
2. After the epoch ends, `prepareStopEpochWithApr0` closes the global bucket (`apr0TotalPrincipal = 0`, line 540) and `collectWithdrawFunds` keeps the principal inside `pendingWithdraws` until borrower funding.
3. `_settleApr0` moves the user's principal to `settledPrincipal` once `epochNumber > principalEpoch` (lines 551-564). Nothing removes it from `pendingWithdraws`.
4. On a later lossy stop, `collectWithdrawFunds` sets `lossRecoveryPrice = funded / pendingBasis` where `pendingBasis` still contains the settled APR0 principal (lines 414-421). The recovery price is thus diluted by basis that will never be haircut.
5. `claimWithdrawRequest` → `_claimLossAdjustedWithdrawRequest` calls `_clearWithdrawClaimForEpoch`, where `_withdrawClaimAmountsForEpoch` sees `apr0User.principal == 0` and `withdrawsRequestsByEpoch == 0`, so `claimBasis == 0` — the settled user takes no loss (lines 789-801, 862-881).
6. `_claimFundedWithdrawRequest` then pays `settledPrincipal + settledInterest` at par via `_transferFundedClaim`, burning the receipt (lines 338-349).

The same gap applies in the default path: `defaultPendingClaimBasis` returns raw `pendingWithdraws` (line 645), which includes settled APR0 principal, inflating `totalBasis` in `finalizeDefaultRecovery` (line 680) and diluting `defaultRecoveryPrice`, while `_claimDefaultedWithdrawRequest` finds `claimBasis == 0` for that settled user and the funded path pays them at par from strategy underlyings ahead of recovery claimants.

### Impact Explanation
Direct insolvency/theft. The funded amount transferred by the borrower at the lossy stop is `pendingBasis - pendingLoss`, sized for all pending receipts to share the haircut. The settled APR0 user instead withdraws at par, so total payouts exceed the funded reserve: either early claimants drain funds belonging to loss-adjusted claimants, or the last claimants' `safeTransfer` reverts, permanently freezing their funded recovery. Loss is quantified as `settledAPR0Principal × (1 − lossRecoveryPrice/RECOVERY_FULL)` over-paid at par plus the price dilution suffered by every other receipt holder.

### Likelihood Explanation
Requires APR0 mode (`unscaledApr == 0`), a settle boundary (one epoch roll), then a `stopEpochWithDuration` loss or borrower default while a settled receipt is still unclaimed — all reachable by an ordinary KYC'd tranche holder sequencing honest manager calls. The guard at lines 263-271 only blocks *new requests* while a lossy epoch receipt is unclaimed; it does not catch settled APR0 principal because `_hasWithdrawRequest`/the guard check `withdrawsRequestsByEpoch`/`apr0Users.principal`, not `settledPrincipal`. No existing guard compensates.

### Recommendation
In `_withdrawClaimAmountsForEpoch` (or a parallel per-epoch record), attribute settled APR0 principal/interest to `principalEpoch` so it is included in `claimBasis`/`burnAmount` of the loss-adjusted and defaulted claim paths. Alternatively, store settled APR0 amounts in `withdrawsRequestsByEpoch[user][reqEpoch]` at settlement time so a single per-epoch ledger drives all haircuts. Also subtract the settled amount's par payout from `pendingWithdraws`-derived basis, or exclude it when computing `lossRecoveryPrice`/`defaultRecoveryPrice` if it is genuinely already funded.

### Proof of Concept
```solidity
// test/foundry/IdleCreditVaultApr0Loss.t.sol — fork test against existing harness
function testSettledApr0EscapesLossHaircut() external {
    // 1. APR0 mode: set unscaledApr == 0 on the vault (via CDO setApr path).
    // 2. Attacker (KYC'd LP) deposits AA, calls cdoEpoch.requestWithdraw(x, AAtranche)
    //    during epoch N (buffer). pendingWithdraws += x; apr0Users[atk].principal = x.
    // 3. Honest manager stopEpoch(0, interest) -> epochNumber becomes N+1; borrower
    //    fully funds, collectWithdrawFunds reduces pendingWithdraws... but in APR0
    //    rollover the attacker does NOT claim; instead requestWithdraw again or let
    //    _settleApr0 run on next claim attempt so principal moves to settledPrincipal.
    //    (attacker calls requestWithdraw(1 wei) in epoch N+1 to trigger _settleApr0,
    //    or simply waits: settlement happens lazily inside _claimFundedWithdrawRequest
    //    or _requestWithdrawApr0 — any touch settles it once epochNumber > N.)
    // 4. Attacker re-requests a normal withdraw in epoch N+1 so lastWithdrawRequest
    //    tracks the *new* epoch; settledPrincipal remains unpaid and un-haircut.
    // 5. Manager calls stopEpochWithDuration with _lossAmount > 0 ->
    //    collectWithdrawFunds(funded < pendingBasis):
    //    lossRecoveryPriceByEpoch[N+1] = funded / pendingBasis
    //    where pendingBasis still contains the attacker's settled APR0 principal x.
    // 6. Attacker calls cdoEpoch.claimWithdrawRequest():
    //    _claimLossAdjustedWithdrawRequest -> _withdrawClaimAmountsForEpoch sees
    //    apr0Users[atk].principal == 0 and (if the epoch-N+1 request was APR0 too)
    //    claimBasis == 0 -> no haircut applied to x.
    //    _claimFundedWithdrawRequest then pays settledPrincipal + settledInterest
    //    at PAR via _transferFundedClaim.
    // 7. Assert: sum of all claimants' payouts > amount funded at the lossy stop;
    //    a second victim's loss-adjusted claim reverts on insufficient balance
    //    (safeTransfer underflow) -> permanent freeze of their funded recovery.
}
```

Key lines to assert in the PoC: `collectWithdrawFunds` price computation (`contracts/strategies/idle/IdleCreditVault.sol:417-421`), settled-bucket move (`545-565`), zero `claimBasis` for settled APR0 (`862-881`), and the at-par payout (`338-349`).