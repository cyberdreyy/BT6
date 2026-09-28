### Title
Instant-withdraw receipts can be claimed before they are funded, letting a user steal underlyings reserved for other users' matured withdraw requests — (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.claimInstantWithdrawRequest` pays out a user's instant-withdraw receipt purely by burning the recorded `instantWithdrawsRequests[_user]` balance. Unlike `claimWithdrawRequest`/`_claimFundedWithdrawRequest`, it performs no epoch-maturity check and no check that the borrower actually funded the instant requests via `collectInstantWithdrawFunds`. Any underlyings sitting in the vault — most importantly underlyings already collected at `stopEpoch` to back other users' matured `withdrawsRequests` — can be drained by an instant receipt that was minted seconds earlier. This is the vault analog of a use-after-free: a receipt (pointer) is dereferenced against shared storage (the vault's underlying balance) before the backing allocation for that receipt exists, so it consumes an allocation owned by someone else.

### Finding Description
Relevant code in `contracts/strategies/idle/IdleCreditVault.sol`:

- `requestInstantWithdraw` (lines 356–375) burns CDO strategy tokens, mints the user a receipt, and increments `instantWithdrawsRequests[_user]` and `pendingInstantWithdraws`. No underlying is moved; funding is deferred to the next `startEpoch`/`collectInstantWithdrawFunds` (lines 398–403), which pulls tokens from the CDO and decrements `pendingInstantWithdraws`.
- `claimInstantWithdrawRequest` (lines 380–393) simply reads `instantWithdrawsRequests[_user]`, burns it, zeroes it, and calls `_transferFundedClaim(_user, amount)`. There is no `epochNumber`-based wait, no record of how much of `pendingInstantWithdraws` was actually collected, and `pendingInstantWithdraws` is not decremented on claim.
- `_transferFundedClaim` (lines 897–907) only guards the `defaultRecoveryReserve`; it does not reserve underlyings that back funded `withdrawsRequests`/`instantWithdrawsRequests` of other users.

Compare with `_claimFundedWithdrawRequest` (lines 319–350), which enforces `epochNumber <= lastWithdrawRequest[_user]` → revert, i.e. a normal receipt cannot be spent until a full epoch after request, which is exactly the window in which `stopEpoch` funds it. The instant path has no equivalent maturity/funding gate.

Attack trace (unprivileged KYC'd lender, instant-withdraw deployment, running epoch):

1. Victims previously called `requestWithdraw`; at `stopEpoch` the borrower funded `pendingWithdraws` via `collectWithdrawFunds`, so the vault now holds, say, 1,000,000 underlying reserved for those funded receipts.
2. Manager lowers APR (honest action): `lastEpochApr > unscaledApr + instantWithdrawAprDelta`, enabling the instant-withdraw branch in `IdleCDOEpochVariant` (lines 761–770).
3. Attacker calls `cdoEpoch.requestWithdraw(amount, tranche)` → `requestInstantWithdraw` mints them a receipt and increments `pendingInstantWithdraws`. Borrower has not yet funded anything for this request (that happens at the next `startEpoch`).
4. Attacker immediately calls `claimInstantWithdrawRequest` (gated only by `allowInstantWithdraw` in the CDO). The vault burns the receipt and transfers `amount` underlyings — taken from the balance reserved for the victims' funded normal withdraw claims.
5. When the victims later call `claimWithdrawRequest`, `_transferFundedClaim` either reverts (if `defaultRecoveryReserve` accounting trips) or simply has insufficient balance — their funded receipts become unpayable.

### Impact Explanation
Direct theft of funded withdrawal reserves. The attacker converts an unfunded receipt into an immediate underlying payout, extracting up to the entire vault underlying balance that was collected for other users' matured withdraw (and previously funded instant) requests. Loss equals the attacker's instant-withdraw principal, bounded only by tranche holdings and the vault's liquid balance; the corresponding victims' claims are permanently underfunded, since `instantWithdrawsRequests`/`withdrawsRequests` accounting decrements while the backing tokens are gone.

### Likelihood Explanation
Requires an instant-withdraw-enabled deployment (`allowInstantWithdraw` + `instantWithdrawAprDelta` path in `IdleCDOEpochVariant`), a genuine APR drop so `_isInstantWithdrawEnabled()` passes, and the vault holding funded-but-unclaimed withdraw reserves — a routine state, since claims are lazy and can sit unclaimed across epochs (tests at `test/foundry/IdleCreditVault.t.sol:2938` explicitly exercise claiming old requests much later). No privileged misbehavior needed; all privileged calls are honest sequencing. Caveat I could not fully verify in scope: whether `startEpoch`/`getInstantWithdrawFunds` ordering or an external guard reliably prevents a claim between request and borrower funding — nothing in `claimInstantWithdrawRequest` itself enforces it.

### Recommendation
Track funded instant liquidity separately from outstanding receipts. Either:

- Gate `claimInstantWithdrawRequest` on funding: e.g. record `fundedInstantWithdraws` incremented by `collectInstantWithdrawFunds` and require `amount <= fundedInstantWithdraws attributable to the user's request epoch`, or store a per-user `instantWithdrawsEpoch`/`instantWithdrawsFundedEpoch` and revert while the request epoch is unfunded.
- Decrement `pendingInstantWithdraws` on claim and maintain an explicit per-user claimable balance that is only increased when the corresponding epoch's instant funds are collected.
- Extend `_transferFundedClaim`'s reserve guard to also subtract underlyings earmarked for outstanding `withdrawsRequests` and funded instant requests.

### Proof of Concept
Foundry fork-style PoC sketch (pattern follows `test/foundry/IdleCreditVault.t.sol` helpers `_startEpochAndCheckPrices`/`_stopEpochAndCheckPrices`/`_depositWithUser`):

```solidity
function testInstantClaimDrainsFundedWithdrawReserves() external {
  uint256 amount = 100_000 * ONE_SCALE;

  // 1) Victim deposits and requests a normal withdraw
  _depositWithUser(victim, amount, true);           // AA
  vm.prank(victim);
  uint256 victimReq = cdoEpoch.requestWithdraw(0, address(AAtranche));

  // 2) Epoch runs and stops; borrower funds pendingWithdraws
  _startEpochAndCheckPrices(0);
  vm.warp(cdoEpoch.epochEndDate() + 1);
  uint256 pending = IdleCreditVault(address(strategy)).pendingWithdraws();
  deal(defaultUnderlying, borrower, cdoEpoch.expectedEpochInterest() + pending);
  vm.prank(manager);
  cdoEpoch.stopEpoch(0, 0);
  // vault now holds `pending` underlyings reserved for victim

  // 3) APR drops (honest manager action) enabling instant withdraws
  vm.prank(manager);
  IdleCreditVault(address(strategy)).setAprs(0, 0); // or any apr < lastEpochApr - delta

  // 4) Attacker deposits, requests instant withdraw during buffer
  _depositWithUser(attacker, amount, true);
  vm.prank(attacker);
  cdoEpoch.requestWithdraw(0, address(AAtranche)); // routes to requestInstantWithdraw

  // 5) Attacker claims BEFORE next startEpoch funds instant requests
  uint256 balPre = underlying.balanceOf(attacker);
  vm.prank(attacker);
  cdoEpoch.claimInstantWithdrawRequest();
  assertGt(underlying.balanceOf(attacker) - balPre, 0);

  // 6) Victim's funded claim now fails / is underfunded
  vm.prank(victim);
  vm.expectRevert(); // or assert balance shortfall
  cdoEpoch.claimWithdrawRequest();
}
```

Note: PoC assumes `allowInstantWithdraw` is true and `instantWithdrawAprDelta` is configured so the instant branch triggers; the key assertion is that `claimInstantWithdrawRequest` succeeds while `collectInstantWithdrawFunds` for that epoch has not yet run.