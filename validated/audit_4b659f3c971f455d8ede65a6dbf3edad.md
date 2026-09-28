### Title
Loss-adjusted withdraw receipts escape the haircut when the user re-requests a withdraw in a later epoch - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
Analog of the uninitialized-buffer read: `_claimLossAdjustedWithdrawRequest` reads `lossRecoveryPriceByEpoch` keyed only by `lastWithdrawRequest[_user]`, a single slot that is overwritten on every new `requestWithdraw`. The recovery price written for the loss epoch is therefore never read once a newer request epoch exists, and the still-recorded per-epoch basis is later paid at par through the funded-claim path — the stored haircut data is written but never read.

### Finding Description
In `requestWithdraw`, the vault records the request per epoch and unconditionally overwrites `lastWithdrawRequest[_user] = currentEpoch` (IdleCreditVault.sol:282-293). When a `stopEpochWithDuration` loss occurs, `collectWithdrawFunds` funds only `pendingToFund < pendingBasis` and stores the haircut `lossRecoveryPriceByEpoch[epochNumber]` (IdleCreditVault.sol:411-430).

On claim, `claimWithdrawRequest` calls `_claimLossAdjustedWithdrawRequest`, which looks up `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` — only the *latest* request epoch (IdleCreditVault.sol:789-800). If the user made any subsequent withdraw request in a later epoch `M > N`, `lastWithdrawRequest` is `M`, `lossRecoveryPriceByEpoch[M] == 0`, and the function returns 0. Execution then falls to `_claimFundedWithdrawRequest`, which pays the aggregate `withdrawsRequests[_user]` — which still contains the uncleared epoch-`N` basis via `withdrawsRequestsByEpoch[_user][N]` — at full par, once `epochNumber > M` (IdleCreditVault.sol:319-349). The epoch-`N` haircut is silently dropped.

### Impact Explanation
The strategy only holds `pendingToFund` (the post-haircut amount) for epoch-`N` receipts plus the fully-funded later-epoch amounts. An attacker who had a receipt in the loss epoch can add a minimal new withdraw request after the loss stop, wait one epoch, and claim the epoch-`N` basis at 100% instead of `lossRecoveryPrice`. This directly drains the claim reserve: remaining epoch-`N` claimants receive less than their haircut-adjusted entitlement or their claims revert on insufficient balance — theft of other users' funded recovery and violation of the "one receipt, one haircut-adjusted payout" invariant.

### Likelihood Explanation
Requires only a KYC-passing lender with a pending receipt in an epoch that is stopped via `stopEpochWithDuration` with a partial loss, plus one subsequent `requestWithdraw` (arbitrarily small) in a later epoch that ends normally. No privileged role is needed; `requestWithdraw` has no restriction against users with unresolved loss-adjusted receipts. The per-epoch data structure exists precisely to support per-epoch pricing, but the claim path only reads the last epoch's key.

### Recommendation
In `_claimLossAdjustedWithdrawRequest` (or `claimWithdrawRequest`), iterate or check all epochs with nonzero `withdrawsRequestsByEpoch[_user]` for which `lossRecoveryPriceByEpoch[epoch] != 0`, rather than only `lastWithdrawRequest[_user]`. Alternatively, in `requestWithdraw`, first settle/clear any prior loss-adjusted receipt for the user before overwriting `lastWithdrawRequest`, so a stale epoch price cannot be orphaned.

### Proof of Concept
```solidity
// SPDX-License-Identifier: MIT
// Foundry fork test against an IdleCDOEpochVariant + IdleCreditVault deployment.
function testLossReceiptEscapesHaircut() external {
    // 1) Attacker (KYC'd lender) deposits and requests withdraw in epoch N
    uint256 tranches = _depositWithUser(attacker, 100e6);
    _requestWithdrawWithUser(attacker, tranches);          // recorded in epoch N

    // 2) Epoch N stops with a realized loss: borrower funds only part of pendingWithdraws
    //    -> collectWithdrawFunds stores lossRecoveryPriceByEpoch[N] = e.g. 50% (RECOVERY_FULL/2)
    uint256 loss = /* partial loss < totalBasis */;
    _stopEpochWithDurationLoss(loss);
    assertEq(strategy.lossRecoveryPriceByEpoch(N), RECOVERY_FULL / 2);

    // 3) Buffer period of epoch N+1: attacker makes a dust re-request.
    //    lastWithdrawRequest[attacker] is overwritten to N+1.
    _requestWithdrawWithUser(attacker, 1);

    // 4) Epoch N+1 ends normally and is fully funded
    _stopCurrentEpochWithApr(10e18);   // epochNumber = N+2 > lastWithdrawRequest

    // 5) Claim: _claimLossAdjustedWithdrawRequest reads lossRecoveryPriceByEpoch[N+1] == 0,
    //    so the epoch-N basis is paid at par by _claimFundedWithdrawRequest.
    uint256 balPre = underlying.balanceOf(attacker);
    vm.prank(attackerCdo);             // via cdo.claimWithdrawRequest()
    cdoEpoch.claimWithdrawRequest();
    uint256 paid = underlying.balanceOf(attacker) - balPre;

    uint256 fair = 100e6 * RECOVERY_FULL / 2 / RECOVERY_FULL + dustRequest;
    assertGt(paid, fair);              // attacker received ~2x the haircutted amount
    // Subsequent epoch-N claimants now face an underfunded reserve.
}
```