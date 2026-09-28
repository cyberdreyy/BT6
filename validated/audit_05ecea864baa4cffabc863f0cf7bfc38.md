### Title
Unfunded instant-withdraw receipts can be claimed at par before default finalization, draining the recovery reserve - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
Analogous to the `NoResolution` pledge bug — where a claim pays a fixed entitlement without verifying the claim was actually backed — `IdleCreditVault.claimInstantWithdrawRequest` pays out `instantWithdrawsRequests[_user]` in full via `_transferFundedClaim` without checking whether that instant-withdraw request was ever funded (`pendingInstantWithdraws`). A holder of an unfunded instant receipt can withdraw underlying at par while the pool is defaulted and before `finalizeDefault` applies the recovery haircut, taking funds that belong to the shared recovery basis.

### Finding Description
In `contracts/strategies/idle/IdleCreditVault.sol`:

- `requestInstantWithdraw` mints the user a strategy-token receipt and increments `instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch`, `instantWithdrawClaimsByEpoch[currentEpoch]` and `pendingInstantWithdraws` (lines 356–375).
- `collectInstantWithdrawFunds` is the only place `pendingInstantWithdraws` is decremented, when the CDO actually transfers the underlying (lines 398–403). So `pendingInstantWithdraws > 0` means part of the current-epoch instant bucket was never funded.
- `claimInstantWithdrawRequest` (lines 380–393) does:

```solidity
uint256 amount = instantWithdrawsRequests[_user];
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);
```

There is no check on `pendingInstantWithdraws` and no epoch gating equivalent to `_claimFundedWithdrawRequest`'s `epochNumber <= lastWithdrawRequest[_user]` check (line 326). The only guard is on the CDO side: `IdleCDOEpochVariant.claimInstantWithdrawRequest` checks `allowInstantWithdraw` (lines 975–979).

The haircut path `_claimDefaultedInstantWithdrawRequest` only applies once `defaultRecoveryFinalized && defaultInstantWithdrawsFinalized` (lines 382–386, 696). Between `stopEpoch`/default detection and `finalizeDefault`, an unfunded instant receipt still pays out 1:1 from whatever underlying the strategy holds — including `defaultRecoveryReserve`, prefunded instant funds reserved for other users, and funded normal-withdraw claim money.

This mirrors the report's flaw exactly: the payout amount is derived from a recorded entitlement (`instantWithdrawsRequests[_user]`, analogous to `_numberOfPledges = 1`) rather than from the actually-backed/funded portion of that entitlement.

### Impact Explanation
Direct theft of other users' funds: after a default where the instant-withdraw bucket was underfunded (partial borrower funding or failed `_depositToVault`), the first unfunded instant claimant receives par value paid out of the recovery pool and other users' funded claims, reducing `defaultRecoveryPrice`/reserve for everyone else or reverting later honest claims on insufficient balance. Loss is bounded by the unfunded portion of `instantWithdrawsRequests`, i.e., up to the full `instantWithdrawClaimsByEpoch[epochNumber] - collected` amount.

### Likelihood Explanation
Requires: instant withdrawals enabled (`allowInstantWithdraw`), at least one instant request in the epoch, and a default/stop where the instant bucket is not fully collected. This is a realistic sequence around honest manager/owner calls (stopEpoch + delayed finalizeDefault), needing only an unprivileged receipt holder — matching the medium difficulty/severity of the source report.

### Recommendation
In `claimInstantWithdrawRequest`, only pay the funded portion of `instantWithdrawsRequests[_user]` — e.g., pro-rate by `pendingInstantWithdraws` vs `instantWithdrawClaimsByEpoch[epochNumber]` or block par claims while `pendingInstantWithdraws != 0` once the pool is defaulted, forcing them through `_claimDefaultedInstantWithdrawRequest`. At minimum, decrement `pendingInstantWithdraws` on claim to keep the unfunded-remainder accounting consistent.

### Proof of Concept
Caveat — I was unable to fully verify the exact funding/claim ordering inside `stopEpoch`/`_depositToVault` (whether unfunded instant receipts can coexist with a non-defaulted running epoch) within the available iterations, so the PoC sketch targets the post-default window where `pendingInstantWithdraws != 0` is confirmed by `_defaultPrefundedInstantReserve` logic:

```solidity
// fork mainnet, standard epoch variant, enable instant withdraws
cdoEpoch.setInstantWithdrawParams(delay, minAprDiff, false); // manager
// user deposits, epoch starts, user calls requestInstantWithdraw
cdoEpoch.requestInstantWithdraw(...) // or requestWithdraw in instant mode
// epoch ends; borrower underfunds -> stopEpoch marks defaulted,
// pendingInstantWithdraws > 0 (partial/no collectInstantWithdrawFunds)
// BEFORE finalizeDefault is called:
uint256 balPre = underlying.balanceOf(user);
vm.prank(user);
cdoEpoch.claimInstantWithdrawRequest();
// user received full par amount while receipt was only partially funded
assertGt(underlying.balanceOf(user) - balPre, fundedPortionOf(user));
// assert defaultRecoveryReserve / strategy balance decreased by the unfunded delta
```

If the claim path is in fact blocked until full funding (e.g., `pendingInstantWithdraws` is always zeroed or the claim reverts on missing strategy balance via `_transferFundedClaim`), the analogous invariant holds and this should be downgraded.