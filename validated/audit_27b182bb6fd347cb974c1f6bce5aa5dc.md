### Title
Loss haircut escapes older pending withdraw receipts because `lastWithdrawRequest` only tracks the latest request epoch — ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
The kernel bug reads `skb->len` after `dev_kfree_skb_any()` freed the skb: a value is consumed after the object carrying it was released. The credit-vault analog is a stale-pointer/read-after-clear pattern: `collectWithdrawFunds` clears the entire aggregate `pendingWithdraws` basis and records the haircut under the *current* `epochNumber`, while `_claimLossAdjustedWithdrawRequest` locates the user's loss only through `lastWithdrawRequest[_user]` — which points to a single (latest) request epoch. Receipts booked in earlier epochs under `withdrawsRequestsByEpoch` survive the clearing and are later paid at par through the funded-claim path, even though the borrower only funded the haircutted aggregate.

### Finding Description
- `requestWithdraw` accrues per-epoch basis in `withdrawsRequestsByEpoch[_user][currentEpoch]` and always overwrites `lastWithdrawRequest[_user] = currentEpoch` (IdleCreditVault.sol:282-293). A user can therefore hold pending receipt basis in multiple epochs: request in epoch N, let `stopEpoch` bump `epochNumber` to N+1 without claiming, then request again in the N+1 buffer.
- When the borrower underpays at epoch end, `collectWithdrawFunds` computes `lossRecoveryPrice = _amount * RECOVERY_FULL / pendingBasis` over the **whole** aggregate `pendingBasis` (all epochs), then zeroes `pendingWithdraws` and stores `lossRecoveryPriceByEpoch[epochNumber]` (lines 411-425).
- At claim time, `_claimLossAdjustedWithdrawRequest` (lines 789-801) reads only `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` and calls `_clearWithdrawClaimForEpoch(_user, lossEpoch, false)`, which clears **only** that one epoch's `withdrawsRequestsByEpoch` entry and resets `lastWithdrawRequest[_user] = 0` (lines 815-836).
- The residual `withdrawsRequests[_user]` still contains the earlier epochs' amounts. With `lastWithdrawRequest == 0`, the gating check `epochNumber <= lastWithdrawRequest[_user]` in `_claimFundedWithdrawRequest` passes immediately (line 326), so the remaining basis is burned and paid at par via `_transferFundedClaim`.
- Concretely: user has `A` pending in epoch N and `B` pending in epoch N+1; borrower funds `(A+B)*p` where `p < 1`. The user receives `B*p` (haircut) + `A` (par) > `(A+B)*p`. The excess is paid from underlying that belongs to other loss-adjusted claimants or the recovery reserve.

### Impact Explanation
Direct insolvency/theft: the aggregate funded pot was sized for a haircut on the full `pendingBasis`, but multi-epoch requesters withdraw more than their pro-rata share. The `_transferFundedClaim` reserve guard (lines 897-907) only protects `defaultRecoveryReserve`, not other claimants' funded underlyings, so the last claimants to call `claimWithdrawRequest` find an empty strategy — permanent loss of unclaimed payouts up to the sum of all pre-latest-epoch pending bases that escape the haircut. Note the `lossRecoveryPrice != 0` guard in `requestWithdraw` (lines 263-271) only blocks *new* requests made *after* a loss epoch; it does not stop stacking epochs *before* the loss.

### Likelihood Explanation
Requires a lossy `stopEpochWithDuration`/`collectWithdrawFunds` shortfall (borrower underfunding, an intended code path), and an attacker — any KYC-passing lender/tranche holder — who simply requests a withdraw in two consecutive epochs without claiming in between. No privileged action is needed; the request paths are permissionless through the CDO/queue once KYC'd. The attacker must predict a loss epoch, but even without exploitation the bug silently overpays honest multi-epoch requesters and shortchanges everyone else.

### Recommendation
Apply the epoch haircut to the user's **entire** pending basis, not just `lastWithdrawRequest`'s epoch. Either:
- iterate/clear all of the user's `withdrawsRequestsByEpoch` entries (and open APR0 buckets) inside `_claimLossAdjustedWithdrawRequest`/`_clearWithdrawClaimForEpoch` so every pre-loss receipt gets `lossRecoveryPrice`, or
- revert new `requestWithdraw`s while the user still has unclaimed prior-epoch basis (extend the existing guard at lines 263-271 to `withdrawsRequests[_user] != 0` even when no loss price exists yet), so `pendingBasis` can never span multiple user epochs.

### Proof of Concept
Foundry fork PoC (against `test/foundry` harness; sketch):

```solidity
function testLossSkipsEarlierEpochReceipt() external {
    // epoch N buffer: attacker (KYC'd) requests withdraw of A
    _depositWithUser(attacker, A + B);
    vm.prank(manager); cdoEpoch.startEpoch();           // epoch N runs
    _stopCurrentEpoch();                                 // epochNumber -> N+1

    // buffer of epoch N+1: request withdraw of A tranches
    vm.prank(attacker); tranche.approve(address(cdoEpoch), type(uint256).max);
    vm.prank(attacker); cdoEpoch.requestWithdraw(A, address(tranche)); // lastWithdrawRequest=N+1? -> recorded epoch N+1
    vm.prank(manager); cdoEpoch.startEpoch();

    // buffer of epoch N+2: request withdraw of B tranches
    vm.prank(attacker); cdoEpoch.requestWithdraw(B, address(tranche)); // lastWithdrawRequest = N+2

    // borrower underfunds: manager stops epoch with a loss so collectWithdrawFunds(amount < pendingWithdraws)
    _stopEpochWithLoss(/* fund only (A+B) * p */);
    // lossRecoveryPriceByEpoch[N+2] = p

    // attacker claims: gets B*p via _claimLossAdjustedWithdrawRequest,
    // then A at par via _claimFundedWithdrawRequest in the same call
    uint256 balPre = underlying.balanceOf(attacker);
    vm.prank(attacker); cdoEpoch.claimWithdrawRequest();
    uint256 got = underlying.balanceOf(attacker) - balPre;

    assertGt(got, (A + B) * p / 1e18); // strictly more than pro-rata haircut
    // invariant broken: sum of claims > funded pot -> later claimants revert/underpaid
}
```

Expected: `claimWithdrawRequest` returns `B*p + A`, exceeding the funded `(A+B)*p`, draining underlyings owed to other loss-adjusted receipt holders.