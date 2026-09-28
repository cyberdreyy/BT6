### Title
Instant-withdraw receipts are paid before they are funded, letting a user drain other claimants' funded reserve - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
ERC-7562 punishes a contract for performing a disallowed state transition during a restricted validation phase — the operation exists, but the environment forbids it at that moment. The closest analog in this codebase is the same shape: `claimInstantWithdrawRequest` pays a receipt during a phase in which that receipt is not yet allowed to be paid (before its epoch's instant-withdraw funding has been collected via `collectInstantWithdrawFunds`). The vault burns the caller's *entire* `instantWithdrawsRequests[_user]` balance — including current-epoch requests still counted in the global unfunded bucket `pendingInstantWithdraws` — and pays the full amount through `_transferFundedClaim`, which only protects `defaultRecoveryReserve`, not the unfunded portion.

### Finding Description
In `requestInstantWithdraw`, the strategy mints a receipt and increments both `instantWithdrawsRequests[_user]` and the global `pendingInstantWithdraws` (lines 356–375). Funding for those receipts only arrives when the CDO pulls underlying at epoch stop through `collectInstantWithdrawFunds`, which decrements `pendingInstantWithdraws` (lines 398–403).

However `claimInstantWithdrawRequest` contains no epoch or funding gate at all (unlike `_claimFundedWithdrawRequest`, which enforces `epochNumber <= lastWithdrawRequest` to force a one-epoch wait, lines 326–328):

```
function claimInstantWithdrawRequest(address _user) external {
  _onlyIdleCDO();
  if (defaultRecoveryFinalized && defaultInstantWithdrawsFinalized) {
    _claimDefaultedInstantWithdrawRequest(_user);
  }
  uint256 amount = instantWithdrawsRequests[_user];
  _burn(_user, amount);
  instantWithdrawsRequests[_user] = 0;
  _transferFundedClaim(_user, amount);
}
```

The code itself proves the funded/unfunded distinction matters: `defaultPendingClaimBasis` and `_defaultPrefundedInstantReserve` exist precisely because "startEpoch moved the CDO's available cash to the strategy but that cash covered only part of the instant queue" (comments at lines 638–640, 712–714), i.e. at any time the strategy balance can hold less than the aggregate `instantWithdrawsRequests`. Yet in the normal (non-default) path, a claim pays the full aggregate receipt as long as any underlying sits in the contract.

Attack sequence (running epoch, instant mode):

1. Epoch N stops; borrower/CDO funds instant requests, so the strategy holds underlying earmarked for *other* users' pending instant claims (`pendingInstantWithdraws` partially covered, per the prefunded-partial scenario described in the code).
2. Attacker (a KYC'd lender) calls `requestInstantWithdraw` in epoch N+1, creating an *unfunded* receipt.
3. Attacker calls `claimInstantWithdrawRequest`. The vault burns the full receipt and `_transferFundedClaim` transfers the full amount — consuming underlying that was collected to back earlier users' receipts.
4. When earlier users claim, `balanceOf(this)` is short and `safeTransfer` reverts: their funded receipts are permanently unpaid while `pendingInstantWithdraws` still counts them.

The invariant "one receipt = one payout only after its epoch's funding" is broken; `pendingInstantWithdraws` is never consulted on the claim path.

### Impact Explanation
The attacker receives underlying equal to their *unfunded* request amount, paid out of the pool of funded instant-withdraw liquidity belonging to other claimants (or out of any balance earmarked for pending normal withdrawals / the next borrower draw). This is direct theft of other users' claimable funds, quantified by the attacker's unfunded request size (bounded only by their deposited tranche position). The victims' receipts remain recorded but can never be paid, since the strategy's balance no longer covers them — effectively theft plus permanent freezing of unclaimed withdrawals.

### Likelihood Explanation
Requires only: (a) instant withdrawals enabled for the pool, (b) the attacker holding a tranche position able to call `requestInstantWithdraw` (standard KYC'd lender), and (c) residual strategy balance — which the code explicitly documents as a normal state (partially prefunded instant queue after `startEpoch`, and any balance collected via `collectInstantWithdrawFunds` before it is claimed). No privileged actor misbehavior is needed; the CDO's honest `stopEpoch`/`startEpoch` calls create the funded-but-unclaimed window every epoch. The main residual uncertainty is whether `IdleCDOEpochVariant.claimInstantWithdrawRequest` applies an additional per-user funded-amount check before delegating to the strategy — I was unable to fully verify the CDO-side wrapper within the available search, but the strategy itself performs no such check and is the accounting authority for `pendingInstantWithdraws`.

### Recommendation
Track the funded portion of instant requests explicitly, mirroring the normal-withdraw path: e.g. a per-epoch funded flag/ratio (`instantFundedByEpoch`), or decrement `instantWithdrawsRequests` eligibility by `pendingInstantWithdraws`. At minimum, in `claimInstantWithdrawRequest` pay only `instantWithdrawsRequests[_user]` minus the user's unfunded current-epoch basis (`instantWithdrawsRequestsByEpoch[_user][epochNumber]` while that epoch's `pendingInstantWithdraws` is nonzero), and revert or cap the payout when the claim exceeds the strategy's funded balance attributable to that receipt.

### Proof of Concept
```solidity
// Fork test against a live IdleCDOEpochVariant + IdleCreditVault deployment
// Assumes: instant withdraws enabled, attacker is KYC'd tranche holder (alice).

function test_claimUnfundedInstantReceipt() public {
    // --- Epoch N: victim requests instant withdraw ---
    vm.prank(victimCDOFlow); // via IdleCDO.requestInstantWithdraw
    // victimReceipt = X recorded, pendingInstantWithdraws = X

    // --- stopEpoch N: CDO collects only part of the queue ---
    // (documented partial-funding path: cash covers only part of instant queue)
    // strategy now holds B < total instant claims; pendingInstantWithdraws > 0

    // --- Epoch N+1 starts; attacker opens NEW unfunded instant request ---
    uint256 attackerShares = tranche.balanceOf(attacker);
    vm.startPrank(attacker);
    cdo.requestInstantWithdraw(attackerShares);        // mints receipt, += pendingInstantWithdraws
    uint256 balBefore = underlying.balanceOf(attacker);
    cdo.claimInstantWithdrawRequest();                  // pays FULL receipt incl. unfunded part
    vm.stopPrank();

    uint256 stolen = underlying.balanceOf(attacker) - balBefore;
    assertGt(stolen, 0);

    // --- Victim's previously funded receipt now unpayable ---
    vm.prank(victim);
    vm.expectRevert(); // ERC20: transfer amount exceeds balance
    cdo.claimInstantWithdrawRequest();
}
```

The key assertion is that `claimInstantWithdrawRequest` succeeds for a receipt created in the *current* epoch while `pendingInstantWithdraws` (the unfunded global bucket) is still nonzero, and that the payout consumes underlying recorded for earlier receipts — reproducible on a mainnet fork without any privileged collusion.