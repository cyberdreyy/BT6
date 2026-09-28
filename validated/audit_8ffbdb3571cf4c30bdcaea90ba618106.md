### Title
Instant-withdraw claims resolve against the strategy's aggregate underlying balance, letting a claimant spend funds earmarked for matured normal withdraw receipts — (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The external report (CVE-2021-23177) describes an improper link-resolution flaw: an operation performed on a reference is silently applied to the *target* of the link, letting an attacker change the state/permissions of a resource they were never meant to touch. The analog in `IdleCreditVault` is receipt-claim resolution: `claimInstantWithdrawRequest` pays an instant receipt out of the strategy's pooled underlying balance instead of restricting payout to the funds actually collected for that instant request via `collectInstantWithdrawFunds`. Like the archive extractor that follows a symlink and chmods the target, the claim path "follows" the generic balance reference and spends underlyings that belong to other claim classes — most notably the already-funded normal withdraw reserves pulled in by `collectWithdrawFunds` at `stopEpoch`.

### Finding Description
Relevant flow in `contracts/strategies/idle/IdleCreditVault.sol`:

- `requestInstantWithdraw` mints the user a strategy-token receipt, records `instantWithdrawsRequests[_user]` and bumps `pendingInstantWithdraws` (lines 356–374).
- The borrower-side funding for those receipts is pulled separately: `collectInstantWithdrawFunds` decrements `pendingInstantWithdraws` and transfers underlying from the CDO into the strategy (lines 398–403).
- `claimInstantWithdrawRequest` simply burns the receipt and calls `_transferFundedClaim(_user, instantWithdrawsRequests[_user])` (lines 380–393). There is **no check that the request's epoch was actually funded**, no check against `pendingInstantWithdraws`, and no per-epoch earmarking — the payout resolves against `underlyingToken.balanceOf(address(this))` minus only `defaultRecoveryReserve` (lines 897–906).
- Meanwhile `collectWithdrawFunds` pulls underlying into the same strategy balance to back matured normal/APR0 receipts (`withdrawsRequests`, `lossRecoveryPriceByEpoch` claims) — but that funded reserve is **not** isolated in any accounting variable; `_transferFundedClaim` only protects `defaultRecoveryReserve`.

So the broken invariant is "one receipt, one earmarked payout": an instant receipt is resolved against a shared balance that includes other users' funded-but-unclaimed withdraw reserves.

Attack sequence (instant-withdraw mode, `allowInstantWithdraw == true`, epoch running):

1. A normal withdraw epoch matures: `stopEpoch` → CDO calls `collectWithdrawFunds(pendingWithdraws)`, moving underlying into the strategy to back claimants' `withdrawsRequests` receipts. Those funds sit in the strategy until claimed.
2. Attacker (KYC-passing lender holding tranche tokens) calls `IdleCDOEpochVariant.requestWithdraw`, which routes to `requestInstantWithdraw`; the CDO's tokens are burned and the attacker receives receipt strategy tokens plus `instantWithdrawsRequests[attacker]`.
3. **Before** the manager calls `getInstantWithdrawFunds`/`collectInstantWithdrawFunds` (i.e., before any borrower money was moved for this instant request), the attacker calls `claimInstantWithdrawRequest`. The strategy burns the receipt and transfers `instantWithdrawsRequests[attacker]` out of the pooled balance — paying the attacker with the matured withdraw reserve.
4. When the legitimate withdraw claimant later calls `claimWithdrawRequest` → `_claimFundedWithdrawRequest` → `_transferFundedClaim`, the strategy balance is short and the transfer reverts (or, once `defaultRecoveryReserve != 0`, trips the reserve guard and reverts with `NotAllowed`), permanently freezing their claim.

### Impact Explanation
Direct theft of funded withdraw reserves plus permanent freezing of other users' matured withdraw claims. Loss is bounded by the strategy's claimable balance excluding `defaultRecoveryReserve` — i.e., all funded-but-unclaimed normal/APR0 withdraw reserves, prefunded instant amounts, and any `sendInterestAndDeposits` funds held — up to the attacker's tranche position size (receipts mint 1:1). Quantified: attacker with X tranche tokens can extract up to X underlying from reserves belonging to other claimants, and every funded claimant whose reserve was drained becomes unclaimable.

### Likelihood Explanation
Requires `allowInstantWithdraw` enabled (a supported vault mode — `setInstantWithdrawParams` exists and tests exercise the flow), an attacker holding tranche tokens (any KYC-passing lender), and a nonzero strategy balance earmarked for funded claims — a routine state between `stopEpoch` funding and user claims, or whenever partially-prefunded instant claims or returned borrower funds sit in the strategy. No privileged misbehavior is needed; sequencing around honest manager/owner calls suffices.

### Recommendation
Earmark instant-claim backing the same way `defaultRecoveryReserve` is earmarked:

- Track a `fundedInstantWithdraws` counter incremented in `collectInstantWithdrawFunds` and decremented in `claimInstantWithdrawRequest`; revert if `instantWithdrawsRequests[_user]` exceeds the user's funded portion (or gate claims per-epoch on `instantWithdrawClaimsByEpoch` funding status).
- Symmetrically, add a `fundedWithdrawClaims` reserve counter updated in `collectWithdrawFunds` and consumed in `_transferFundedClaim`/`_claimLossAdjustedWithdrawRequest`, and have *all* payout paths subtract every reserve bucket — not only `defaultRecoveryReserve` — before paying.
- Alternatively require `pendingInstantWithdraws` coverage: revert `claimInstantWithdrawRequest` for receipts whose epoch was never collected.

### Proof of Concept
Foundry fork sketch (mainnet fork as in `test/foundry/IdleCreditVault.t.sol`, using `cdoEpoch`, `strategy`, `AAtranche`, honest `manager`/`borrower`):

```solidity
// setup: enable instant withdraws
vm.prank(manager);
cdoEpoch.setInstantWithdrawParams(instantDelay, 1000, true); // allowInstantWithdraw = true

// 1. victim deposits and requests a normal withdraw; epoch runs
uint256 amount = 10_000 * ONE_SCALE;
_depositWithUser(victim, amount, true);
vm.prank(victim);
cdoEpoch.requestWithdraw(victimReceipt, address(AAtranche));

// 2. stopEpoch funds the pending receipts: strategy now holds victim's reserve
_stopEpochAndCheckPrices(0, apr, expectedFunds); // triggers collectWithdrawFunds
uint256 stratBal = IERC20Detailed(underlying).balanceOf(address(strategy));
assertGt(stratBal, 0); // funded reserve sitting in strategy

// 3. attacker (tranche holder) requests instant withdraw in the new running epoch
_startEpochAndCheckPrices(1);
dealTranche(attacker, amount);
vm.prank(attacker);
cdoEpoch.requestWithdraw(amount, address(AAtranche)); // mints instant receipt

// 4. attacker claims BEFORE manager calls getInstantWithdrawFunds/collectInstantWithdrawFunds
vm.prank(attacker);
cdoEpoch.claimInstantWithdrawRequest();
// attacker's payout came out of victim's funded reserve

// 5. victim's matured claim now reverts/underpays
vm.prank(victim);
vm.expectRevert(); // SafeERC20 transfer exceeds remaining balance (or NotAllowed via reserve guard)
cdoEpoch.claimWithdrawRequest();
```

Key assertion: `underlying.balanceOf(attacker)` increases by the instant receipt amount while `pendingInstantWithdraws` was never funded — proving the claim resolved against the pooled balance (the "link target") rather than its own earmarked backing.

Caveat: I could not fully read the body of `IdleCDOEpochVariant.requestWithdraw`'s instant-routing branch or confirm whether the CDO additionally gates `claimInstantWithdrawRequest` on manager funding; the strategy-level code itself contains no such gate, and the only balance protection (`defaultRecoveryReserve` in `_transferFundedClaim`) does not cover funded normal-withdraw reserves.