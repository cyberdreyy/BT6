### Title
A new withdraw request after a loss-adjusted epoch erases the haircut lookup key, letting the user claim loss-adjusted receipts at par - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
`IdleCreditVault._claimLossAdjustedWithdrawRequest` selects the loss epoch via `lastWithdrawRequest[_user]` only. If a user files a new `requestWithdraw` in a later epoch, `lastWithdrawRequest` is overwritten, `lossRecoveryPriceByEpoch[newEpoch]` is 0, the loss-adjusted branch returns 0, and `_claimFundedWithdrawRequest` pays the **aggregate** `withdrawsRequests[_user]` — which still contains the haircutted epoch basis — at par. The loss is escaped; the strategy is drained by `A * (1 - lossRecoveryPrice)`.

### Finding Description
The bug-class analog of the Dagu report (an unvalidated identifier resolving outside its intended scope) maps to epoch-keyed claim resolution in `IdleCreditVault`:

- `requestWithdraw` records `withdrawsRequestsByEpoch[_user][epoch]`, aggregates into `withdrawsRequests[_user]`, and unconditionally overwrites `lastWithdrawRequest[_user] = currentEpoch` (lines 281-293).
- On a loss-adjusted stop, `collectWithdrawFunds` stores `lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice < RECOVERY_FULL`, zeroes `pendingWithdraws`, and funds only `pendingBasis * price` underlyings to the strategy (lines 411-430). The user's per-epoch basis stays inside `withdrawsRequests[_user]`.
- At claim time, `claimWithdrawRequest` runs `_claimLossAdjustedWithdrawRequest` first (line 312), which looks up `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` (lines 789-792). If the user made **any** subsequent request, that key no longer points at the loss epoch, the price lookup returns 0, and `_clearWithdrawClaimForEpoch` is never called for the loss epoch.
- `_claimFundedWithdrawRequest` then pays `withdrawsRequests[_user]` in full via `_transferFundedClaim` (lines 338-349), even though only the haircutted amount was ever funded.

### Impact Explanation
Direct theft / insolvency: after a `stopEpochWithDuration` loss where pending receipts were haircut to price `p`, a user holding receipt basis `A` in the loss epoch makes a dust-sized request `B` in the next buffer period, waits one epoch, then calls `claimWithdrawRequest`. They receive `A + B` underlyings while the strategy was funded only `A*p + B`. The excess `A*(1-p)` is paid out of underlyings backing other users' funded claims (or active LP value held in the strategy), breaking the one-receipt-one-payout and loss-waterfall invariants. `_transferFundedClaim` only protects `defaultRecoveryReserve`; absent a finalized default there is no containment check.

### Likelihood Explanation
Requirements are all unprivileged: a tranche holder who requested withdrawal in the epoch where a realized loss occurred, plus one subsequent `requestWithdraw` (dust amount, needs tranche tokens and KYC). The only honest-actor dependency is a loss-bearing `stopEpochWithDuration`/`collectWithdrawFunds`, which is a normal mode of operation. No guard in `requestWithdraw` blocks new requests while a loss-adjusted receipt is unclaimed (`_hasWithdrawRequest` is unused for gating this path).

### Recommendation
Resolve loss-adjusted claims by iterating/checking the user's actual per-epoch basis rather than trusting `lastWithdrawRequest[_user]` as the epoch key — e.g., have `_claimLossAdjustedWithdrawRequest` check `lossRecoveryPriceByEpoch` for every epoch present in `withdrawsRequestsByEpoch[_user]`, or keep a per-user set/bitmap of loss epochs. Alternatively, revert `requestWithdraw` when the user has an uncleared basis in an epoch with a non-zero `lossRecoveryPriceByEpoch`, or subtract the haircutted basis from `withdrawsRequests[_user]` at loss-recording time and track it in a dedicated per-epoch haircut bucket.

### Proof of Concept
Foundry fork sketch (mode: APR>0, instant off, prefunded off):

```solidity
// Epoch N buffer: attacker deposits and requests withdraw of A
uint256 A = 1_000e6;
uint256 trA = _depositWithUser(attacker, A);       // deal + approve + depositAA
vm.prank(attacker);
cdoEpoch.requestWithdraw(trA, address(AAtranche));
uint256 lossEpoch = strategy.epochNumber();         // == lastWithdrawRequest[attacker]

// Manager stops epoch N with a realized loss on pending receipts
// borrower funds only pendingToFund < pendingWithdraws via collectWithdrawFunds
// => lossRecoveryPriceByEpoch[lossEpoch] = p < 1e18, pendingWithdraws = 0
_stopCurrentEpochWithPartialFunding();              // e.g. p = 0.5e18

// Epoch N+1 buffer: attacker files a dust request B to overwrite the epoch key
uint256 dust = _depositWithUser(attacker, 1e6);
vm.prank(attacker);
cdoEpoch.requestWithdraw(dust, address(AAtranche)); // lastWithdrawRequest = N+1

// Epoch N+1 stops fully funded
_stopCurrentEpochFullyFunded();                     // epochNumber = N+2

// Attacker claims
uint256 balPre = underlying.balanceOf(attacker);
vm.prank(attacker);
cdoEpoch.claimWithdrawRequest();
uint256 got = underlying.balanceOf(attacker) - balPre;

// Expect: attacker received A + dust-worth at par
assertEq(got, A + dustUnderlyingValue);
// But strategy was funded only A*p + dust => strategy drained by A*(1-p)
assertLt(underlying.balanceOf(address(strategy)),
         requiredBackingForRemainingClaims);
```

Key assertions mirroring the code: `strategy.lossRecoveryPriceByEpoch(lossEpoch) == p > 0`, `strategy.lastWithdrawRequest(attacker) == lossEpoch + 1`, `strategy.withdrawsRequestsByEpoch(attacker, lossEpoch) == A` still non-zero after the claim — proving the loss-epoch basis was paid at par and never routed through the haircut path.