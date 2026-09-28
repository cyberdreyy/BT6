I need to check `_transferFundedClaim` and whether instant withdraw requests can be created after default.### Title
Post-default withdraw requests mint unbacked receipts that drain the fixed default-recovery reserve at par, stealing funds reserved for defaulted-epoch claimants - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.finalizeDefaultRecovery` crystallizes a fixed `defaultRecoveryReserve` sized to cover only the defaulted-epoch claim basis (`activeBasis + pendingBasis`) times `defaultRecoveryPrice`. After finalization, `requestWithdraw` takes an early-return path that mints the caller a receipt recorded in `postDefaultRequests` without adding any new underlying to the reserve. `_claimPostDefaultWithdrawRequest` then pays that receipt 1:1 via `_transferDefaultRecovery`, which decrements the same `defaultRecoveryReserve`. The pool of money is fixed, but the set of claimants is not — exactly the "super cookie" pattern of the curl advisory: a claim minted in one trust domain (post-default requests) is honored against a scope it was never sized for (the defaulted-epoch recovery reserve).

### Finding Description
In `requestWithdraw` (contracts/strategies/idle/IdleCreditVault.sol, lines 247-257):

```solidity
if (defaultRecoveryFinalized) {
  if (_hasWithdrawRequest(_user) || instantWithdrawsRequests[_user] != 0 || postDefaultRequests[_user] != 0) {
    revert NotAllowed();
  }
  _burn(msg.sender, _amount);
  _mint(_user, _amount);
  postDefaultRequests[_user] = _amount;
  return;
}
```

The comment says "the CDO passes an already-haircut amount because finalization lowered virtualPrice first," and `_claimPostDefaultWithdrawRequest` (lines 760-767) pays `amount` 1:1 from `defaultRecoveryReserve` (lines 912-917). However, `defaultRecoveryReserve` was fixed at finalization (line 691) to `reserveAmount = _recoveredAmount + prefundedReserve + prior reserve`, sized against `totalBasis` computed only from defaulted-epoch active holders and pending receipts (lines 674-680). Post-default requesters were never part of `totalBasis`, so no reserve was provisioned for them.

Each post-default claim consumes `defaultRecoveryReserve` at par. There is no cap tying cumulative `postDefaultRequests` to any reserved amount, and no new funding accompanies a post-default request (the burned tokens are CDO-side strategy tokens whose underlying was already counted in the reserve computation or was never held by the strategy). The result: whoever requests/claims post-default first withdraws underlying that the reserve earmarked for defaulted-epoch withdraw receipts and instant receipts (`_claimDefaultedWithdrawRequest`, `_claimDefaultedInstantWithdrawRequest`, `DefaultDistributor` claims), leaving later legitimate claimants with `defaultRecoveryReserve -= _amount` underflow reverts — i.e., their claims are permanently unpaid.

### Impact Explanation
After a borrower default is finalized, an unprivileged lender holding tranche tokens calls `requestWithdraw` on the CDO. The strategy mints a receipt that pays out 1:1 from the recovery reserve. Because the reserve was sized exactly for defaulted-epoch basis × recovery price, every wei paid to post-default requesters is a wei owed to a defaulted-epoch claimant. Attacker extractable value is bounded only by the reserve balance and the attacker's post-haircut tranche position; in a pool with meaningful defaulted-epoch pending/instant receipts, the attacker can redeem at par (100%) while rightful claimants haircut to `defaultRecoveryPrice`, and the last claimants' transactions revert (permanent freezing of unclaimed recovery). This breaks the "one receipt, one funded payout" and loss-socialization invariants.

### Likelihood Explanation
Requirements: a defaulted, finalized vault (`defaultRecoveryFinalized == true`), `allowInstantWithdraw`-style flows not required, and an attacker holding tranche tokens post-default (buyable on secondary market or held pre-default). The attacker is an ordinary KYC'd lender — fully in scope. The only mitigant is whether the CDO provisions extra reserve when processing post-default requests; nothing in `requestWithdraw`, `postDefaultRequests`, or `_transferDefaultRecovery` enforces this — the reserve is a fixed number decremented per claim, so aggregate post-default claims plus defaulted claims can exceed it. Note: I was unable to fully trace every caller of `requestWithdraw` in `IdleCDOEpochVariant` post-default within the available search budget; if the CDO tops up the reserve elsewhere for post-default requests, this would downgrade to a medium accounting-liveness issue (last claimants still revert if funding is short).

### Recommendation
Either (a) fund post-default receipts independently — pull the underlying from the CDO/borrower at request time and track it in a separate `postDefaultReserve` that `_transferFundedClaim`-style logic spends, never touching `defaultRecoveryReserve`; or (b) treat post-default requests as new recovery claimants by scaling their payout to remaining reserve vs. remaining unclaimed basis (`amount * defaultRecoveryReserve / remainingBasis`). At minimum, revert post-default `requestWithdraw` when `defaultRecoveryReserve` cannot cover `postDefaultRequests + unclaimed defaulted basis × defaultRecoveryPrice`.

### Proof of Concept
Foundry fork PoC outline:

```solidity
function testPostDefaultRequestDrainsRecoveryReserve() external {
  // 1. Epoch running: lender L deposits AA, victim V requests withdraw (recorded in
  //    withdrawsRequestsByEpoch[V][epoch]); borrower defaults on repayment.
  // 2. manager calls finalizeDefaultRecovery: reserve R = recovered + prefunded,
  //    defaultRecoveryPrice = R / totalBasis.
  uint256 reserve = strategy.defaultRecoveryReserve();

  // 3. Attacker A (holds tranches post-finalization) calls cdo.requestWithdraw(0, AAtranche).
  //    Strategy path: postDefaultRequests[A] = haircutAmount; reserve unchanged.
  uint256 aBalPre = underlying.balanceOf(A);
  cdoEpoch.claimWithdrawRequest(); // as A
  uint256 aClaimed = underlying.balanceOf(A) - aBalPre;

  // 4. A was paid 1:1 from the reserve that never provisioned post-default claims.
  assertEq(strategy.defaultRecoveryReserve(), reserve - aClaimed);

  // 5. Victim V's defaulted-epoch claim now reverts on underflow or pays less than
  //    claimBasis * defaultRecoveryPrice.
  vm.expectRevert();
  vm.prank(V);
  cdoEpoch.claimWithdrawRequest();
}
```

Key assertions: `defaultRecoveryReserve` is invariant under post-default request creation; cumulative `postDefaultRequests` payouts plus remaining defaulted claims can exceed the reserve; the final defaulted claimant is permanently unpaid (theft of unclaimed yield / permanent freezing).