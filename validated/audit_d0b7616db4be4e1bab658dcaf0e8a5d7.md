### Title
Loss-adjusted withdraw receipts can be retargeted to a later epoch and claimed at par, escaping the `stopEpochWithDuration` haircut - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault` validates which epoch's loss haircut applies to a user's withdraw receipt via the mutable marker `lastWithdrawRequest[_user]`, but then pays out from a different, aggregate ledger (`withdrawsRequests[_user]`). A receipt holder can make a new dust `requestWithdraw` after a loss epoch, which retargets `lastWithdrawRequest` to the fresh epoch where `lossRecoveryPriceByEpoch` is zero. The loss-adjusted claim path then silently skips the haircut and `_claimFundedWithdrawRequest` pays the entire historical basis — including the defaulted/loss-adjusted receipt — at par. This mirrors the external bug's "validate one path, use another" TOCTOU structure: the epoch used for the haircut check is swapped between validation and payout.

### Finding Description
In `requestWithdraw`, each request overwrites `lastWithdrawRequest[_user] = currentEpoch` and adds to `withdrawsRequests[_user]` / `withdrawsRequestsByEpoch[_user][currentEpoch]` (`IdleCreditVault.sol:281-293`).

When a stop epoch realizes a loss, `collectWithdrawFunds` sets `pendingWithdraws = 0` and records `lossRecoveryPriceByEpoch[epochNumber]` (`IdleCreditVault.sol:417-421`). Per-user ledgers are untouched — the full pre-haircut basis remains in `withdrawsRequests[_user]`.

On claim, `claimWithdrawRequest` calls `_claimLossAdjustedWithdrawRequest`, which looks up the loss epoch as `lossEpoch = lastWithdrawRequest[_user]` and returns 0 if `lossRecoveryPriceByEpoch[lossEpoch] == 0` (`IdleCreditVault.sol:789-800`). It then calls `_claimFundedWithdrawRequest`, whose only temporal guard is `epochNumber <= lastWithdrawRequest[_user]`, and which pays `withdrawsRequests[_user]` at par via `_transferFundedClaim` (`IdleCreditVault.sol:319-349`).

Attack sequence:
1. Epoch N: attacker requests withdraw of most of their tranche tokens. `lastWithdrawRequest[attacker] = N`, `withdrawsRequests[attacker] = B`.
2. Honest manager calls `stopEpochWithDuration` / a loss stop; `collectWithdrawFunds` funds less than `pendingBasis`, recording `lossRecoveryPriceByEpoch[N] < RECOVERY_FULL`. The haircut amount is meant to be borne by epoch-N receipts.
3. Epoch N+1: attacker calls `requestWithdraw(dust)` with their remaining tranche tokens. `lastWithdrawRequest[attacker] = N+1`, `withdrawsRequests[attacker] = B + dust`.
4. After the next stop (or immediately if the pool is closed and `epochEndDate == 0`), attacker calls `claimWithdrawRequest`. `_claimLossAdjustedWithdrawRequest` reads `lossEpoch = N+1`, finds `lossRecoveryPriceByEpoch[N+1] == 0`, returns 0. `_claimFundedWithdrawRequest` passes its guard (`epochNumber > N+1`, or closed pool) and transfers `B + dust` at par.

The haircutted epoch-N receipt is paid in full. The same trick works symmetrically: a request made *before* a loss can also hide an older funded receipt, but the profitable direction is escaping the haircut.

### Impact Explanation
The attacker extracts `B * (1 - lossRecoveryPrice/RECOVERY_FULL)` underlying that was assigned as loss to pending receipts. Those underlyings come from the strategy's funded claim reserve, so the theft is borne by other pending-receipt holders and, when the reserve is exhausted, causes revert-based freezing of later claims. Broken invariant: loss waterfall / fair burn — one receipt is paid at a price the system explicitly decided it should not receive.

### Likelihood Explanation
Requires only a tranche-token holder (explicitly in-scope attacker) with a pending withdraw request in an epoch that ends in a `stopEpochWithDuration` loss — a normal, designed code path, not an edge case. The attacker must hold a small residual tranche balance to place the retargeting request and wait one additional epoch (or none, in the closed-pool path where `epochEndDate() == 0` bypasses the wait check). No privileged role, oracle, or reentrancy is needed; cost is dust plus gas.

### Recommendation
Do not derive the loss-claim epoch from the mutable `lastWithdrawRequest` marker. Iterate or track each user's per-epoch receipt epochs (e.g., check `withdrawsRequestsByEpoch[_user][e]` against `lossRecoveryPriceByEpoch[e]` for all epochs with nonzero basis), or record the loss epoch explicitly per user at `collectWithdrawFunds` time. Additionally, `_claimFundedWithdrawRequest` should exclude any basis still covered by a nonzero `lossRecoveryPriceByEpoch` entry rather than paying the whole aggregate at par.

### Proof of Concept
Foundry fork PoC outline (against a mainnet-forked `IdleCDOEpochVariant` + `IdleCreditVault` deployment):

```solidity
// 1. Attacker (whitelisted EOA) deposits AA, gets tranche tokens.
// 2. During epoch N buffer: cdoEpoch.requestWithdraw(B, address(AAtranche));
//    -> lastWithdrawRequest[atk] = N, withdrawsRequests[atk] = B
// 3. Warp past epochEndDate; manager calls stopEpochWithDuration with _lossAmount>0
//    -> collectWithdrawFunds funds pendingToFund < B, sets lossRecoveryPriceByEpoch[N] < 1e18
// 4. In epoch N+1: cdoEpoch.requestWithdraw(1, address(AAtranche));  // dust
//    -> lastWithdrawRequest[atk] = N+1
// 5. Manager stopEpoch normally (or pool closes, epochEndDate()==0).
// 6. cdoEpoch.claimWithdrawRequest();
//    - _claimLossAdjustedWithdrawRequest: lossEpoch=N+1 -> price 0 -> returns 0
//    - _claimFundedWithdrawRequest: guard passes -> transfers B+1 at PAR
// assertEq(underlying.balanceOf(atk) - balPre, B + 1); // haircut escaped
// Later claimants' _transferFundedClaim reverts on insufficient funded reserve.
```

Uncertainty: I could not fully verify within this scan whether `IdleCDOEpochVariant.stopEpochWithDuration` leaves the strategy's underlying balance sufficient to cover a par payout before other claims drain it — if the funded amount equals `pendingToFund` exactly, the attacker's par claim succeeds only by front-running other claimants, after which their claims revert (permanent freezing of unclaimed funds, still in-scope impact). The retargeting flaw itself is confirmed by the code at `IdleCreditVault.sol:789-800` and `319-349`.