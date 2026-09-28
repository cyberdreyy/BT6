### Title
Post-default withdraw requests drain `defaultRecoveryReserve` without backing, freezing honest defaulted claimants - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
After a hard borrower default is finalized via `finalizeDefaultRecovery`, `requestWithdraw` still accepts new withdraw requests from tranche holders. These "post-default" requests mint a receipt and record `postDefaultRequests[_user]`, but they add **zero** underlying to `defaultRecoveryReserve`. When claimed through `_claimPostDefaultWithdrawRequest` → `_transferDefaultRecovery`, they are paid 1:1 out of the same fixed reserve that was sized only to cover the defaulted-epoch claims (`totalBasis` at `defaultRecoveryPrice`). Each post-default claim therefore steals reserve from the rightful defaulted claimants; once the reserve's token balance is exhausted, remaining `_transferDefaultRecovery` calls revert (the `safeTransfer` fails / `defaultRecoveryReserve -= _amount` underflows), permanently freezing unclaimed recovery for honest users.

### Finding Description
- `finalizeDefaultRecovery` computes `reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve` and `defaultRecoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis` (lines 686-693). The reserve exactly covers `totalBasis = activeBasis + pendingBasis` at the recovery ratio — nothing more.
- In `requestWithdraw`, the `defaultRecoveryFinalized` branch (lines 247-258) burns `_amount` strategy tokens from the CDO, mints `_amount` receipt tokens to `_user`, and sets `postDefaultRequests[_user] = _amount`. No `safeTransferFrom` of underlying occurs, and `defaultRecoveryReserve` is not increased. The comment "The CDO passes an already-haircut amount" describes the receipt's *price*, but the claim is still paid from the shared reserve.
- `claimWithdrawRequest` → `_claimPostDefaultWithdrawRequest` (lines 760-767) then pays `amount` 1:1 via `_transferDefaultRecovery`, which does `defaultRecoveryReserve -= _amount` and `underlyingToken.safeTransfer(_user, _amount)` (lines 912-917).
- The burned CDO strategy tokens do not free any cash: the strategy's underlying was already reserved. The net effect is a new, unbacked claim consuming reserve tokens. Because `defaultRecoveryReserve` bookkeeping and token balance both shrink, the *last* claimants (defaulted-epoch receipts via `_claimDefaultedWithdrawRequest`/`_claimDefaultedInstantWithdrawRequest`, or other post-default claimants) hit `defaultRecoveryReserve -= _amount` underflow or a failed `safeTransfer` and can never claim.
- Attacker path: any tranche-token holder (explicitly in scope) — including one who buys tranche tokens cheaply on a secondary market after the default is public — calls CDO `requestWithdraw` post-finalization, then `claimWithdrawRequest`. The guard at line 249 only blocks users who already have open requests, so a fresh address works. Each request converts a haircut-value tranche position into a 1:1 cash draw on recovery funds belonging to others. The reserve guard in `_transferFundedClaim` (line 904) protects the reserve from *funded* claims, but post-default claims intentionally spend the reserve — with no corresponding inflow.

### Impact Explanation
Direct theft plus permanent freezing of unclaimed recovery. Every post-default withdraw pulls `_amount` underlying from `defaultRecoveryReserve` that belongs pro-rata to defaulted active LPs and pending receipt holders. The attacker can extract up to the reserve balance (bounded by their post-default receipt amount, but the receipt amount equals their claim basis, so an attacker holding/trading a large tranche position can drain a large share). Honest claimants' `claimWithdrawRequest`/`claimInstantWithdrawRequest`/DefaultDistributor payouts then revert permanently — quantified loss equal to the reserve shortfall created, up to 100% of unclaimed recovery.

### Likelihood Explanation
Requires the vault to reach the `defaulted + defaultRecoveryFinalized` state — a real but borrower-dependent precondition (borrower default is honest/sequenced, attacker merely acts in it). Once in that state, no privileged action, race, or oracle is needed: a single unprivileged call sequence (`requestWithdraw` → `claimWithdrawRequest`) executes the drain, and secondary-market tranche purchases make it cheap for any EOA. Existing guards (`_onlyIdleCDO`, `_hasWithdrawRequest` check, epoch gating, reserve guard) do not block it because this code path was explicitly built for post-default requests.

### Recommendation
Do not pay post-default receipts from `defaultRecoveryReserve`. Either: (a) increase the reserve by the request amount at request time (pull matching underlying into the strategy or deduct from CDO-held recovery cash before burning CDO strategy tokens), or (b) route post-default claims through `_transferFundedClaim` semantics against genuinely unreserved funds and revert when only reserve remains. Additionally, make `defaultRecoveryReserve` accounting track obligations (expected claim outflows at `defaultRecoveryPrice`) rather than raw amounts so under-funding reverts at request time instead of at the last claimant's expense.

### Proof of Concept
Foundry fork sketch (mirror `test/foundry/IdleCreditVault.t.sol` setup):

```solidity
// Setup: deposit AA (victim) + attackerAA, startEpoch, warp past epochEndDate.
// Manager calls stopEpoch -> borrower defaults -> cdoEpoch.finalizeDefault(recovery, source)
//   reserve = recovery funds covering totalBasis at defaultRecoveryPrice (e.g. 0.5e18)

// Victim had a pending withdraw receipt in the defaulted epoch (claimBasis V).

// Attacker holds tranche tokens worth A post-haircut.
vm.prank(attacker);
cdoEpoch.requestWithdraw(attackerTrancheBal, address(AAtranche)); // postDefaultRequests[attacker] = A
vm.prank(attacker);
cdoEpoch.claimWithdrawRequest(); // receives A underlying 1:1 from defaultRecoveryReserve

// Reserve is now short by A. Victim's claim should pay V * price but reverts:
vm.prank(victim);
vm.expectRevert(); // defaultRecoveryReserve underflow / safeTransfer failure
cdoEpoch.claimWithdrawRequest();
```

Assertion: `underlying.balanceOf(attacker)` increases by `A` while the strategy's underlying balance falls below `defaultRecoveryReserve` obligations, and victim claims permanently revert.

Note: I verified the reserve sizing, the post-default request/claim paths, and the absence of any underlying inflow in `requestWithdraw`'s `defaultRecoveryFinalized` branch. I could not fully trace the CDO-side post-default `requestWithdraw` preconditions (e.g., whether deposits/tranche transfers remain possible and whether CDO-held recovery cash implicitly backs these claims); if the CDO separately transfers matching underlying into the strategy during post-default requests, the reserve accounting may coincidentally balance — this should be confirmed in the PoC before reporting.