### Title
Unfunded instant-withdraw receipts are paid in full from reserves earmarked for matured withdraw claims - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`claimInstantWithdrawRequest` burns the user's full `instantWithdrawsRequests` receipt and pays it from the strategy's underlying balance without checking how much of that receipt was actually funded via `collectInstantWithdrawFunds`. `pendingInstantWithdraws` is explicitly the *unfunded remainder*, yet the claim path ignores it, so a requester can be paid out of underlyings that were pulled in by `collectWithdrawFunds` to back other users' matured normal withdraw receipts.

### Finding Description
In `requestInstantWithdraw` the vault mints the user a strategy-token receipt and increases `pendingInstantWithdraws`. Funding arrives later when the CDO calls `collectInstantWithdrawFunds(_amount)`, which decrements `pendingInstantWithdraws` and transfers exactly `_amount` into the strategy. If the CDO collects less than the outstanding instant bucket (e.g. borrower only partially funds the instant queue at `startEpoch`, or funds it in two tranches), `pendingInstantWithdraws` stays positive — the code comments themselves describe it as "the still-unfunded remainder".

`claimInstantWithdrawRequest` then does:
```solidity
uint256 amount = instantWithdrawsRequests[_user];
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);
```
It pays `amount` unconditionally, gated only by the strategy's token balance and the `defaultRecoveryReserve` guard in `_transferFundedClaim` (lines 897–907). There is no check that this user's receipt was among the funded portion, and no correlation between `pendingInstantWithdraws` and the payout. The underlyings available in the strategy are exactly the reserves accumulated by `collectWithdrawFunds`/`collectInstantWithdrawFunds` for other claimants. Like the CVE, a pending/unreleased accounting entry (`pendingInstantWithdraws`) is not consulted when the resource (funded reserves) is consumed.

### Impact Explanation
Direct theft plus freezing of unclaimed payouts. An attacker requests an instant withdraw of `X`, the instant queue is only partially funded (funded `F < X`), and the attacker still claims `X`, extracting `X - F` that belongs to users holding matured normal withdraw receipts funded by `collectWithdrawFunds`. Those legitimate claimants' subsequent `claimWithdrawRequest` calls revert on `safeTransfer` (insufficient balance), so their funded claims are frozen/stolen. Loss equals up to the full unfunded instant bucket, bounded by the strategy's reserve balance.

### Likelihood Explanation
Requires `allowInstantWithdraw` enabled and an epoch where the instant queue is under-funded at claim time — the code explicitly supports partial funding (`collectInstantWithdrawFunds` takes an arbitrary `_amount` and the default-recovery path treats `pendingInstantWithdraws != 0` as a normal state). Any KYC'd tranche holder can request an instant withdraw while the epoch is running. No privileged misbehavior is needed beyond the honest manager collecting less than the full instant bucket in a given stop/start sequence, which the accounting deliberately permits.

### Recommendation
In `claimInstantWithdrawRequest`, cap the payable amount by the funded portion of instant receipts (e.g. track a per-user or per-epoch funded basis, or maintain `instantWithdrawsRequests` only for the unfunded remainder and settle funded receipts separately), or revert while `pendingInstantWithdraws` still covers the user's claim. Alternatively, have `collectInstantWithdrawFunds` atomically mark funded receipts so `claimInstantWithdrawRequest` can only pay against `instantWithdrawClaimsByEpoch` amounts that were actually collected.

### Proof of Concept
Foundry fork PoC sketch (mirroring `testClaimInstantWithdrawRequest` in `test/foundry/IdleCDOEpochQueue.t.sol`):

```solidity
// epoch running, allowInstantWithdraw = true
// userA requests normal withdraw in epoch N; stopEpoch funds it via
// strategy.collectWithdrawFunds(amountA) -> strategy holds amountA reserve
// userB requests instant withdraw of X in epoch N+1
// CDO collects only F < X: strategy.collectInstantWithdrawFunds(F)
// assert(strategy.pendingInstantWithdraws() == X - F);
vm.prank(userB);
cdoEpoch.claimInstantWithdrawRequest();   // pays full X from strategy balance
// userB received X but only F was funded: X - F taken from userA's reserve
vm.prank(userA);
vm.expectRevert(); // underlying safeTransfer fails, reserve drained
cdoEpoch.claimWithdrawRequest();
```

Key assertions: `pendingInstantWithdraws > 0` at claim time, `underlying.balanceOf(strategy) < instantWithdrawsRequests[userB]` would underflow only via reserve spend, and userA's funded claim reverts.

Note: I could not fully verify in this session whether `IdleCDOEpochVariant.stopEpoch`/`startEpoch` always funds the instant bucket in full before claims become possible; if the CDO guarantees `pendingInstantWithdraws == 0` whenever `claimInstantWithdrawRequest` is reachable, the attack surface collapses to the partial-funding edge case above.