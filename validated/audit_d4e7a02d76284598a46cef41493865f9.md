### Title
Unfunded instant-withdraw receipts can be claimed during the buffer period because `allowInstantWithdraw` is never reset between epochs - (contracts/IdleCDOEpochVariant.sol, contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.requestInstantWithdraw` mints a receipt (`instantWithdrawsRequests[user]`) that is only funded later, when the honest manager calls `startEpoch` and the CDO pushes cash via `collectInstantWithdrawFunds`. `claimInstantWithdrawRequest`, however, pays out the *entire* accumulated `instantWithdrawsRequests[user]` from whatever underlying the strategy currently holds, gated only by the CDO-level `allowInstantWithdraw` flag. That flag is set to `true` at `startEpoch`/`getInstantWithdrawFunds` and is never cleared at `stopEpoch` (except in pool-close mode), so during the buffer period a new, completely unfunded instant receipt can be claimed immediately against cash reserved for other users' funded normal withdrawals. This is the same bug class as CVE-2017-15597: `claimInstantWithdrawRequest` assumes every outstanding instant receipt is accompanied by a funded transfer (the "pin"), while `requestInstantWithdraw` creates receipts with no backing until a later epoch transition (the missing "page reference").

### Finding Description
- `requestInstantWithdraw` (IdleCreditVault.sol:356-375) burns the CDO's strategy tokens, mints an equal receipt to the user, and increments `pendingInstantWithdraws`. No underlying moves.
- Funding happens only in `startEpoch` (IdleCDOEpochVariant.sol:279-292): `collectInstantWithdrawFunds(min(pendingInstant, totUnderlyings))`, and `allowInstantWithdraw = true` is set when fully funded.
- `stopEpoch` (IdleCDOEpochVariant.sol:486) sets `allowInstantWithdraw = _isRequestingAllFunds`, which is `false` for a normal stop — but the flag was already `true` during the epoch, and nothing resets it to `false` when the epoch ends and the buffer period opens. `allowInstantWithdraw` therefore remains `true` during the buffer window when users are allowed to submit new requests.
- `claimInstantWithdrawRequest` (IdleCreditVault.sol:380-393) burns `instantWithdrawsRequests[_user]` and calls `_transferFundedClaim`, which only protects `defaultRecoveryReserve` (line 899-905). Cash held for `pendingWithdraws` receipts funded via `collectWithdrawFunds` at the previous stop, or for older funded instant receipts of other users, is unprotected.
- `instantWithdrawDeadline` does not help: it is set to `block.timestamp + instantWithdrawDelay` at each `startEpoch`, so during the buffer it is always in the past.

Concrete sequence (fixed-APR deployment, instant-withdraw enabled):

1. Epoch N runs; attacker requests an instant withdraw during buffer N (APR dropped, `_isInstantWithdrawEnabled` path at IdleCDOEpochVariant.sol:761-769).
2. Manager calls `startEpoch`; strategy collects cash for the request and `allowInstantWithdraw = true`.
3. Attacker does not claim. Epoch N runs, manager calls `stopEpoch`; `allowInstantWithdraw` stays `true`; `pendingInstantWithdraws` is `0`.
4. During buffer N+1 the APR drops again (or `instantWithdrawAprDelta` condition holds from the prior `lastEpochApr`), so the attacker calls `requestWithdraw` and creates a **second** instant receipt of amount X — unfunded, since funding only occurs at the *next* `startEpoch`.
5. Attacker immediately calls `claimInstantWithdrawRequest`. The strategy burns the whole `instantWithdrawsRequests[attacker]` (old funded + new unfunded) and transfers `old + X` underlying, where only `old` was actually funded. X is stolen from the strategy's underlying balance — i.e., from funded normal-withdraw receipts (`pendingWithdraws` cash collected by `collectWithdrawFunds`) or other claimants, which then become unclaimable (insolvency transfer to honest users).

### Impact Explanation
Direct theft of funded withdrawal proceeds held by `IdleCreditVault`. The attacker receives underlying equal to the unfunded receipt amount, and honest users' `claimWithdrawRequest`/`claimInstantWithdrawRequest` calls later revert on insufficient balance (or, post-default, the shortfall is silently socialized through `defaultRecoveryPrice`). Loss is bounded above by the attacker's tranche position value (the receipt is minted 1:1 against burned tranche value), but no capital beyond the tranche itself is at risk, and the stolen funds belong to other users, so this is user-fund theft plus permanent freezing of the victims' receipts.

### Likelihood Explanation
Requires: (a) instant withdrawals enabled (APR dropped by more than `instantWithdrawAprDelta`, so `requestWithdraw` routes to `requestInstantWithdraw`), (b) the attacker already holds a funded unclaimed instant receipt — a natural state since claiming is optional and receipts persist — and (c) a new instant request during a buffer period, which any tranche holder can make. No privileged role is needed; the attacker is an ordinary KYC'd tranche holder. Uncertainty: I did not fully verify the CDO-side `claimInstantWithdrawRequest` wrapper — if it additionally requires `pendingInstantWithdraws == 0` or resets `allowInstantWithdraw` on stop, the attack may be blocked; that wrapper should be checked before finalizing severity.

### Recommendation
- Reset `allowInstantWithdraw = false` in `stopEpoch`/`_handleBorrowerDefault` (not only in the close-pool branch), and set it `true` only after the *current* `pendingInstantWithdraws` bucket is fully collected.
- In `IdleCreditVault.claimInstantWithdrawRequest`, pay only the funded portion: track a per-user funded amount (e.g., only receipts from epochs ≤ the last funded epoch), or revert while `pendingInstantWithdraws != 0` covers the claimant's unfunded balance.
- Alternatively, reject `requestInstantWithdraw` when the user already has an unclaimed instant receipt, mirroring the `requestWithdraw` guard for loss-adjusted receipts (IdleCreditVault.sol:263-271).

### Proof of Concept
Foundry fork test sketch (against `IdleCDOEpochVariant` + `IdleCreditVault`, fixed-APR mode, non-programmable borrower):

```solidity
// setup: deposit AA as attacker, set aprs, run one full epoch where
// lastEpochApr > unscaledApr + instantWithdrawAprDelta so requestWithdraw
// routes to the instant path.

// Epoch N buffer: attacker requests instant withdraw
vm.prank(attacker);
cdoEpoch.requestWithdraw(amount, address(AAtranche));

// startEpoch funds it (borrower pulled / surplus used)
vm.prank(manager);
cdoEpoch.startEpoch();
// pendingInstantWithdraws == 0 now, allowInstantWithdraw == true

// warp to end of epoch, honest borrower repays
vm.warp(cdoEpoch.epochEndDate() + 1);
deal(underlying, borrower, expectedFunds);
vm.prank(manager);
cdoEpoch.stopEpoch(newAprLower, 0); // sets lastEpochApr; allowInstantWithdraw stays true

// Buffer N+1: attacker requests instant withdraw again -> UNFUNDED receipt
vm.prank(attacker);
cdoEpoch.requestWithdraw(amount, address(AAtranche));
assertGt(strategy.instantWithdrawsRequests(attacker), 0);
assertGt(strategy.pendingInstantWithdraws(), 0); // nothing funded yet

// strategy still holds cash for other users' funded pendingWithdraws
uint256 stratBalBefore = underlying.balanceOf(address(strategy));
uint256 attackerBalBefore = underlying.balanceOf(attacker);

// claim succeeds while unfunded -> drains other users' funded claims
vm.prank(attacker);
cdoEpoch.claimInstantWithdrawRequest();

uint256 received = underlying.balanceOf(attacker) - attackerBalBefore;
assertGt(received, fundedPortion);          // attacker got more than was funded
assertLt(underlying.balanceOf(address(strategy)),
         stratBalBefore - fundedPortion);    // deficit = stolen amount

// victim's funded normal withdraw claim now reverts (insufficient balance)
vm.prank(victim);
vm.expectRevert();
cdoEpoch.claimWithdrawRequest();
```

The PoC needs the existing test harness from `test/foundry/IdleCreditVault.t.sol` (`_startEpochAndCheckPrices`, `_stopEpochAndCheckPrices`, `_requestWithdrawWithUser`) and an APR decrease between epochs to enable the instant path.