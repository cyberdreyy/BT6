### Title
Post-default withdraw requests are paid at par from the finite default recovery reserve, draining funds owed to defaulted-epoch claimants - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The Mattermost bug class — a pending resource that is never invalidated and keeps consuming a shared allocation — maps to `IdleCreditVault`'s post-default withdraw flow. After `finalizeDefaultRecovery` sizes `defaultRecoveryReserve` exactly for the claim basis that existed at finalization, `requestWithdraw` still accepts new requests (stored in `postDefaultRequests`) and `_claimPostDefaultWithdrawRequest` pays them 1:1 out of that same reserve. These later requests were never part of `totalBasis`, so every post-default payout dilutes/steals recovery owed to defaulted-epoch receipt holders.

### Finding Description
In `finalizeDefaultRecovery` (lines 661-710), the reserve is fixed:

```solidity
uint256 reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve;
uint256 recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;
```

`totalBasis = activeBasis + pendingBasis` covers only claims existing at finalization (lines 674-680). Nothing invalidates or accounts for future requests.

After finalization, `requestWithdraw` (lines 247-257) lets any tranche holder create a new receipt:

```solidity
_burn(msg.sender, _amount);
_mint(_user, _amount);
postDefaultRequests[_user] = _amount;
```

Burning CDO strategy tokens post-finalization releases no underlyings — active NAV was already crystallized at `activeFinalNAV`. Yet `_claimPostDefaultWithdrawRequest` (lines 760-767) pays the full amount from `_transferDefaultRecovery`, which draws on `defaultRecoveryReserve`:

```solidity
amount = postDefaultRequests[_user];
postDefaultRequests[_user] = 0;
_burn(_user, amount);
_transferDefaultRecovery(_user, amount);
```

`claimWithdrawRequest` (lines 301-314) services post-default claims first, with no epoch wait and no check against remaining reserve, while `_claimDefaultedWithdrawRequest` (lines 772-784) pays pre-finalization claims `claimBasis * defaultRecoveryPrice / RECOVERY_FULL` from the same reserve. The reserve was sized so that `sum(claims) = reserveAmount`; each post-default payout breaks that equality.

### Impact Explanation
Direct theft / permanent freezing of unclaimed recovery. With recovery price `p < 1`, an attacker holding tranche tokens post-default calls `requestWithdraw` then `claimWithdrawRequest` and receives underlying at par from the reserve. Each unit paid to a post-default requester removes one unit that was earmarked for defaulted-epoch claimants. Since defaulted claims are paid `claimBasis * p`, the last claimants find the reserve exhausted: their claims either revert on transfer or pay dust — a permanent loss of up to the attacker's receipt size, bounded only by `defaultRecoveryReserve`. FCFS ordering means a fast post-default requester can drain the reserve ahead of legitimate defaulted-epoch claimants.

### Likelihood Explanation
Requirements are minimal: the attacker only needs tranche tokens after default finalization (any KYC-passing lender / tranche holder qualifies under the threat model). No privileged action is needed beyond the honest manager calling `finalizeDefault`, which is a normal flow. The attack is atomic — request and claim can happen in the same transaction immediately after finalization, before defaulted-epoch claimants react. The guard at line 249 (`_hasWithdrawRequest / instantWithdrawsRequests / postDefaultRequests`) only throttles per-user repeats; it does not prevent reserve drainage, and multiple attackers or fresh wallets bypass it.

### Recommendation
Do not pay post-default requests from `defaultRecoveryReserve`. Either (a) require post-default redemptions to draw only on newly deposited underlying (e.g., a separate post-default liquidity bucket refilled by borrower repayments), or (b) expand `totalBasis`/reserve at request time so post-default claims are accounted into the recovery math, or (c) disable `requestWithdraw` after `defaultRecoveryFinalized` until the reserve is fully claimed. Add an invariant check: cumulative `_transferDefaultRecovery` payouts must never exceed `defaultRecoveryReserve` entitlements computed at finalization.

### Proof of Concept
Foundry fork sketch (modeled on `test/foundry/IdleCreditVault.t.sol` default tests around lines 4621-4653):

```solidity
// 1. Epoch running, victim + attacker are AA holders.
_depositWithUser(victim, 10_000 * ONE_SCALE, true);
uint256 attackerTranches = _depositWithUser(attacker, 10_000 * ONE_SCALE, true);

// 2. Victim requests withdraw in the default epoch.
vm.prank(victim);
cdoEpoch.requestWithdraw(victimTrancheBal, address(AAtranche));

// 3. stopEpoch with repayment failure -> defaulted; manager finalizes with recoveryRatio < 1.
_stopEpochAndCheckPrices(1, initialProvidedApr, 0); // borrower short -> default
deal(defaultUnderlying, manager, recovered);
vm.startPrank(manager);
IERC20Detailed(defaultUnderlying).approve(address(strategy), recovered);
cdoEpoch.finalizeDefault(recovered, manager);   // sets defaultRecoveryReserve = recovered
vm.stopPrank();

// 4. Attacker creates a post-default request and claims BEFORE victim.
vm.startPrank(attacker);
cdoEpoch.requestWithdraw(attackerTranches, address(AAtranche)); // mints postDefaultRequests
cdoEpoch.claimWithdrawRequest();                                 // paid 1:1 from reserve
vm.stopPrank();

// 5. Victim's defaulted-epoch claim now underpays or reverts: reserve was drained
//    by payouts that were never in totalBasis at finalization.
uint256 balPre = underlying.balanceOf(victim);
vm.prank(victim);
cdoEpoch.claimWithdrawRequest();
assertLt(underlying.balanceOf(victim) - balPre,
         victimBasis * strategy.defaultRecoveryPrice() / RECOVERY_FULL);
```

Uncertainty note: the body of `_transferDefaultRecovery` was not fully inspected in the available context; the PoC assumes it decrements `defaultRecoveryReserve` and transfers underlying from it, consistent with its usage at lines 766, 783 and 855. If it instead sources funds elsewhere, the reserve-drain mechanism should be re-verified, but the accounting mismatch — post-default payouts absent from `totalBasis` — remains.