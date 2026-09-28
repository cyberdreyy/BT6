### Title
Instant-withdraw claims drain the shared strategy balance earmarked for other users' funded withdraw receipts - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
CVE-2022-26386 is a "user-specific resource placed in a shared location where other users can affect it" bug: Firefox wrote per-user temp files into the world-accessible `/tmp`, letting other local users tamper with them. The analog in idle-tranches is `IdleCreditVault`'s funded-claim liquidity: all funded receipts — normal withdraw receipts collected via `collectWithdrawFunds`, instant receipts collected via `collectInstantWithdrawFunds`, and default-recovery funds — sit in one undifferentiated `underlyingToken` balance, while `claimInstantWithdrawRequest` pays out the full `instantWithdrawsRequests[_user]` from that shared balance with no epoch gating and no reserve accounting for other users' funded claims. `_transferFundedClaim` only isolates `defaultRecoveryReserve`; nothing protects the normal funded bucket.

### Finding Description
- `collectWithdrawFunds` pulls underlying from the CDO into the strategy to fund pending normal withdraw receipts, decrementing `pendingWithdraws`, but the money is not reserved per receipt — it just raises `underlyingToken.balanceOf(address(this))`.
- `collectInstantWithdrawFunds` similarly moves underlying in for instant receipts.
- `claimInstantWithdrawRequest(_user)` burns `instantWithdrawsRequests[_user]` receipt tokens and calls `_transferFundedClaim(_user, amount)`, which transfers `amount` of underlying from the strategy balance to the user. Its only guard is:

```solidity
uint256 reserve = defaultRecoveryReserve;
if (reserve != 0) {
  uint256 balance = underlyingToken.balanceOf(address(this));
  if (balance < reserve || balance - reserve < _amount) revert NotAllowed();
}
```

So any underlying held for *normal* funded receipts (or instant receipts of other epochs/users) is freely spendable by whoever claims first. The receipt tokens themselves are non-transferable (`_transfer` reverts unless called by the CDO), but claims are first-come-first-served against a shared pool.

Attack trace (epoch running/stopped boundary, fixed-APR mode):
1. Victim calls `requestWithdraw` during epoch N; at `stopEpoch`, borrower funding is collected via `collectWithdrawFunds` — underlying earmarked for the victim sits in the strategy.
2. Attacker (any tranche holder) calls `requestInstantWithdraw` / routes an instant request so that `instantWithdrawsRequests[attacker]` is set, then `claimInstantWithdrawRequest` before the victim claims.
3. The strategy balance holds enough underlying only because of the victim's funded receipts; the attacker's claim pays out of that shared balance.
4. Victim's later `claimWithdrawRequest` reverts in `safeTransfer` on insufficient balance — permanent freezing of the victim's funded claim until new money arrives, i.e., theft of earmarked funds.

### Impact Explanation
Direct theft / temporary-to-permanent freezing of other users' funded withdraw proceeds. Quantified loss equals the attacker's instant claim size paid out of funds collected for other users' receipts (bounded by `pendingWithdraws` funding held by the strategy).

### Likelihood Explanation
Requires an instant-withdraw path to be claimable while the strategy holds underlying collected for another bucket. This sequencing exists whenever instant withdrawals are enabled and stopEpoch funding has been collected but not yet claimed — a routine configuration per `setInstantWithdrawParams`. No privileged role is needed by the attacker.

### Recommendation
Track funded balances per bucket (e.g., a `fundedWithdraws` reserve incremented in `collectWithdrawFunds` and decremented in `_transferFundedClaim` for normal claims), and extend the reserve guard in `_transferFundedClaim` so instant claims can never spend underlying earmarked for normal receipts, and vice versa — mirroring the existing `defaultRecoveryReserve` isolation.

### Proof of Concept
A Foundry fork test would: deposit for victim and attacker, `startEpoch`, `requestWithdraw` for victim, `stopEpoch` so `collectWithdrawFunds` funds the strategy, then have the attacker execute `requestInstantWithdraw` + `claimInstantWithdrawRequest` and assert the victim's subsequent `claimWithdrawRequest` reverts on insufficient underlying. Note: I could not fully verify whether `claimInstantWithdrawRequest` is reachable before its own per-epoch funding is collected (the CDO-side sequencing in `IdleCDOEpochVariant` was not fully inspected); if instant claims always require prior `collectInstantWithdrawFunds` funding, the exploit reduces to cross-bucket first-come draining only when the contract holds more than one funded bucket, which is still a real but narrower window.