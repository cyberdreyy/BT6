### Title
Loss-adjusted epoch claims only haircut the latest request epoch, letting earlier pending receipts claim at par and draining the strategy - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
When `stopEpochWithDuration` realizes a loss, `collectWithdrawFunds` stores a single `lossRecoveryPriceByEpoch[epochNumber]` that haircuts the *aggregate* `pendingWithdraws` (all unfunded receipts across request epochs). But `_claimLossAdjustedWithdrawRequest` only clears the receipt recorded under the user's *latest* `lastWithdrawRequest` epoch. Any earlier-epoch pending receipt remains in `withdrawsRequests`/`withdrawsRequestsByEpoch` and is then paid out **at par** by `_claimFundedWithdrawRequest`. This mirrors the kernel bug: queued items keyed to stale state are dequeued/paid as if the removal (haircut) never applied to them.

### Finding Description
1. User calls `requestWithdraw(A)` in epoch N (buffer phase). `pendingWithdraws += A`, `withdrawsRequestsByEpoch[user][N] += A`, `lastWithdrawRequest[user] = N`.
2. After epoch N ends without the request being claimed, the user calls `requestWithdraw(B)` in epoch N+1. The guard at lines 263-271 only checks `lossRecoveryPriceByEpoch[lastWithdrawRequest]` — no loss has been recorded yet, so it passes. Now `withdrawsRequestsByEpoch[user][N] = A`, `[N+1] = B`, `pendingWithdraws = A + B`, `lastWithdrawRequest[user] = N+1`.
3. At `stopEpoch` the borrower funds only `pendingToFund = (A+B) * lossRecoveryPrice` and `collectWithdrawFunds` sets `pendingWithdraws = 0` and `lossRecoveryPriceByEpoch[epochNumber] = price` (lines 411-430).
4. `claimWithdrawRequest` runs `_claimLossAdjustedWithdrawRequest`, which looks up `lossRecoveryPriceByEpoch[lastWithdrawRequest]` (epoch N+1) and clears **only** `withdrawsRequestsByEpoch[user][N+1]` via `_clearWithdrawClaimForEpoch`, paying `B * price` (lines 789-800, 811-820).
5. `_claimFundedWithdrawRequest` then pays the remaining `withdrawsRequests[user] = A` **at par** and burns A receipt tokens (lines 319-349).

The strategy holds only `(A+B)*price` for this user but pays `B*price + A`, a deficit of `A*(1-price)`.

### Impact Explanation
Any unprivileged tranche-token holder can split pending withdrawal receipts across two epochs before a loss epoch, then recover part of their receipt unhaircutted. The excess is paid from strategy-held underlying that belongs to other pending redeemers and active LPs, causing direct insolvency/theft up to `A * (1 - lossRecoveryPrice)` per attacker (repeatable per user; multiple users compound the drain).

### Likelihood Explanation
Requires a realized `stopEpochWithDuration` loss (honest manager action) while the attacker holds unfunded receipts from two distinct request epochs — a normal, non-privileged sequence explicitly permitted by the "wait another epoch to claim both" design. No guard prevents it: the epoch guard only inspects the *latest* epoch's loss price, and `_clearWithdrawClaimForEpoch` clears only that epoch's slice.

### Recommendation
In `collectWithdrawFunds`/`_claimLossAdjustedWithdrawRequest`, either apply `lossRecoveryPrice` to the *entire* `withdrawsRequests[user]` basis (all epochs' pending receipts), or record funded-loss epochs per user and have `_claimFundedWithdrawRequest` sum only non-loss epochs, applying each epoch's recovery price to its slice. Alternatively, iterate all `withdrawsRequestsByEpoch` entries and clear every epoch included in the haircutted `pendingBasis`.

### Proof of Concept
```solidity
// Foundry fork-style PoC on IdleCreditVault + IdleCDOEpochVariant
// Setup: deposit as attacker, run epoch 0 so epochNumber advances.
uint256 tranches = cdoEpoch.depositAA(amount); // attacker, KYC'd
// Epoch 1 buffer: request A
cdoEpoch.requestWithdraw(A, address(AAtranche)); // lastWithdrawRequest = E1
// start + stop epoch 1 with normal apr (A stays unfunded/unclaimed)
// Epoch 2 buffer: request B
cdoEpoch.requestWithdraw(B, address(AAtranche)); // lastWithdrawRequest = E2
// manager: stopEpochWithDuration(loss) -> collectWithdrawFunds(pendingToFund < A+B)
//   sets lossRecoveryPriceByEpoch[E2] = price < 1e18, pendingWithdraws = 0
// advance one epoch, then:
cdoEpoch.claimWithdrawRequest();
// Attacker received B*price + A instead of (A+B)*price.
// assertEq(received, B*price/1e18 + A) -> exceeds funded amount by A*(1-price).
```
Broken invariant: one receipt, one (haircutted) payout — the loss waterfall is bypassed for every stale-epoch slice.