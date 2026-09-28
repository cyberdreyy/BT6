### Title
Post-default withdraw requests pay out 1:1 from the unfunded default recovery reserve, draining haircut claimants' funds - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
After `finalizeDefaultRecovery`, the recovery reserve is sized exactly to cover only the default-epoch claim basis at `defaultRecoveryPrice` (a haircut). Yet `requestWithdraw` still accepts brand-new withdraw requests (`postDefaultRequests`), and `_claimPostDefaultWithdrawRequest` pays them **1:1 from that same reserve** with no new underlying ever being added. A tranche holder can therefore mint a receipt after finalization and immediately drain unclaimed recovery funds at par, leaving legitimate defaulted-epoch claimants unable to withdraw.

### Finding Description
The external bug class — a credential/token replayed to extract value — maps here to receipt claims that are honored against a fixed pool without corresponding funding.

At `contracts/strategies/idle/IdleCreditVault.sol:686-692`, `finalizeDefaultRecovery` computes `recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis` where `reserveAmount` and `totalBasis` cover only active NAV + default-epoch pending receipts. The reserve is then frozen as `defaultRecoveryReserve`.

At `contracts/strategies/idle/IdleCreditVault.sol:247-257`, post-finalization `requestWithdraw` calls `_burn(msg.sender, _amount)` on the CDO's strategy tokens and `_mint(_user, _amount)` as a receipt, recording `postDefaultRequests[_user] = _amount`. Critically, it does **not** pull any underlying into the vault and does not touch `pendingWithdraws` — the comment says the amount "is already-haircut" because the CDO's `virtualPrice` was lowered, but the haircut happens on the *tranche→strategy-token* conversion, not on the underlying payout.

At `contracts/strategies/idle/IdleCreditVault.sol:760-767`, `_claimPostDefaultWithdrawRequest` pays `amount` **1:1** via `_transferDefaultRecovery`, which decrements `defaultRecoveryReserve` (lines 912-917). So a post-default request for `X` tranches (priced at the post-haircut `virtualPrice`) mints a receipt of `X * haircutPrice` strategy tokens and then redeems that full amount of underlying at par from a reserve that was only funded to pay legitimate claimants `recoveryPrice` cents on the dollar.

Every unit paid to a post-default claimant at 100% is a unit stolen from defaulted-epoch receipt holders and active-LP recovery, who were sized for `recoveryPrice < RECOVERY_FULL`. The reserve underflows (`defaultRecoveryReserve -= _amount` reverts) once post-default claims plus early legitimate claims exceed the funded basis, permanently freezing the remaining claimants' recovery.

### Impact Explanation
Direct theft of unclaimed default-recovery funds and permanent freezing of legitimate claims. Any unprivileged tranche-token holder can, after default finalization, call the CDO's `requestWithdraw` (burning tranches priced at the already-haircut virtual price) and immediately `claimWithdrawRequest` to extract underlying at 1:1 from `defaultRecoveryReserve`. Repeating this (or being first while honest claimants wait) drains the reserve below what defaulted-epoch claims are owed, so later legitimate `_claimDefaultedWithdrawRequest` / `_claimDefaultedInstantWithdrawRequest` calls revert on underflow — the "one receipt one payout, reserve isolation" invariant is broken. Loss is quantified as the entire unclaimed `defaultRecoveryReserve` up to the sum of post-default claims.

### Likelihood Explanation
Requires a borrower default that is finalized with `recoveryPrice < RECOVERY_FULL` — a rare but explicitly supported state (the code deliberately preserves "request/claim UX after default"). Once in that state, the attacker only needs tranche tokens (buyable on secondary or already held) and an `isWalletAllowed` pass; no privileged role is involved. The economic incentive is direct: claim at 100% a receipt that was minted at the haircut price, an arbitrage equal to `(1/recoveryPrice - 1)` per unit plus theft of the remaining reserve. No existing guard stops it: `_transferFundedClaim`'s reserve guard is bypassed because payment goes through `_transferDefaultRecovery`, and nothing caps `postDefaultRequests` against the residual reserve.

### Recommendation
Fund post-default claims independently of the recovery reserve. Options:
- Pull the corresponding underlying from the CDO/idle liquidity at request time (e.g., have the CDO transfer `virtualPrice`-priced underlying alongside the request, routed to a separate post-default reserve), or
- Pay post-default claims through `_transferFundedClaim` backed by explicitly accounted funds rather than `defaultRecoveryReserve`, or
- Disallow new `requestWithdraw` after `defaultRecoveryFinalized` until the reserve is fully claimed.

At minimum, `_claimPostDefaultWithdrawRequest` must never decrement `defaultRecoveryReserve` for value that was not added to it.

### Proof of Concept
Foundry fork outline (mainnet fork, pool in defaulted + finalized state):

```solidity
// setup: pool defaulted, manager called finalizeDefaultRecovery
//       defaultRecoveryPrice = P < RECOVERY_FULL, reserve = R
// attacker holds `t` AA tranche tokens and is wallet-allowed

uint256 reserveBefore = vault.defaultRecoveryReserve();
uint256 price = cdoEpoch.virtualPrice(address(AAtranche)); // already haircut

// 1) request withdraw post-default: mints receipt, no funding added
cdoEpoch.requestWithdraw(t, address(AAtranche));
uint256 receipt = vault.postDefaultRequests(attacker); // = t * price / 1e18
assertEq(vault.defaultRecoveryReserve(), reserveBefore); // reserve unchanged

// 2) claim immediately: paid 1:1 from reserve
uint256 balPre = underlying.balanceOf(attacker);
cdoEpoch.claimWithdrawRequest();
uint256 paid = underlying.balanceOf(attacker) - balPre;
assertEq(paid, receipt);                          // par payout
assertEq(vault.defaultRecoveryReserve(), reserveBefore - paid); // drained

// 3) repeat with more tranches until reserve is exhausted;
//    honest defaulted-epoch claimant then reverts:
vm.prank(victim);
vm.expectRevert(); // defaultRecoveryReserve underflow
cdoEpoch.claimWithdrawRequest();
```

Uncertainty note: I could not fully verify the `IdleCDOEpochVariant.requestWithdraw` wrapper (whether it gates post-finalization requests or skips KYC for existing holders). The vault-level code explicitly implements and comments the post-default request path (`postDefaultRequests`, `_claimPostDefaultWithdrawRequest`), strongly indicating it is intended to be reachable; if the CDO wrapper unconditionally blocks it, the issue reduces to dead code. A quick read of `contracts/IdleCDOEpochVariant.sol` around `requestWithdraw` would confirm reachability.