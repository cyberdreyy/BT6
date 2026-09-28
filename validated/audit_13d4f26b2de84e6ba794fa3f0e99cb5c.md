### Title
Post-default withdraw requests drain the fixed recovery reserve at 1:1, stealing defaulted-epoch claimants' recovery - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The CVE-2019-5018 bug class is "use after free": memory released for one purpose is later reused while stale references still exist. The analog in `IdleCreditVault` is a *freed claim basis being reused*: `finalizeDefaultRecovery` sizes `defaultRecoveryReserve` exactly for the claims outstanding at finalization (`reserveAmount` vs `totalBasis`), but `requestWithdraw` can afterwards mint brand-new `postDefaultRequests` receipts that are paid **1:1 out of that same fixed reserve**, even though they were never part of `totalBasis`. Each post-default claim consumes recovery funds that belong to defaulted-epoch withdraw/instant claimants, so later claimants are left unpayable.

### Finding Description
`finalizeDefaultRecovery` computes `reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve` and `recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis`, then stores `defaultRecoveryReserve = reserveAmount`. The reserve therefore covers exactly `totalBasis * recoveryPrice` of claims (`contracts/strategies/idle/IdleCreditVault.sol:686-692`).

After finalization, `requestWithdraw` enters the `defaultRecoveryFinalized` branch (`lines 247-257`): it only requires that the user has no pending request, then burns the CDO's strategy tokens and mints a receipt to the user, recording `postDefaultRequests[_user] = _amount`. Critically, **no underlying is moved into the reserve** — the comment says the CDO "passes an already-haircut amount because finalization lowered virtualPrice", i.e. the haircut is applied via the receipt's nominal value, but the payout source is unchanged.

`claimWithdrawRequest` then calls `_claimPostDefaultWithdrawRequest` (`lines 760-767`), which pays `amount` in full via `_transferDefaultRecovery`, decrementing `defaultRecoveryReserve` (`lines 912-916`). The same reserve backs `_claimDefaultedWithdrawRequest` (`lines 772-784`) and `_claimDefaultedInstantWithdrawRequest` (`lines 842-856`) at `recoveryPrice`. Since post-default claims were never in `totalBasis`, every unit they withdraw directly reduces what honest defaulted claimants can recover.

There is also no vesting or epoch wait on post-default claims: `_claimPostDefaultWithdrawRequest` pays immediately in the same transaction flow, so an attacker can request and claim atomically ahead of victims.

### Impact Explanation
Direct theft / permanent freezing of unclaimed recovery. A post-default requester converts a haircutted tranche position (which can be bought on the market at the post-default price) into a 1:1 draw on the isolated recovery reserve. Once the reserve is depleted below what remaining defaulted claimants are owed (`claimBasis * defaultRecoveryPrice`), their claims either revert or pay zero — the recovery they are entitled to is permanently captured by earlier post-default claimants. Loss magnitude is up to `min(postDefaultRequests claimed, defaultRecoveryReserve)`, i.e. effectively the entire reserve if an attacker cycles large tranche balances.

### Likelihood Explanation
Requires only that the pool has defaulted and finalized recovery — an attacker needs tranche tokens (any EOA can hold or buy defaulted AA/BB tranches cheaply) and calls `requestWithdraw` + `claimWithdrawRequest` via the CDO. No privileged role, no timing race beyond ordering before honest claimants. Any non-trivial reserve makes this profitable since the attacker pays post-haircut value and receives full reserve units. Note the guard at line 249 only blocks users with *existing* requests; a fresh attacker address is unrestricted.

### Recommendation
Do not pay post-default requests from `defaultRecoveryReserve` unless the reserve is explicitly topped up for them. Options: (a) require the CDO to transfer the receipt's backing into the strategy and credit `defaultRecoveryReserve` (or a separate `postDefaultReserve`) inside `requestWithdraw`'s post-default branch; (b) track `postDefaultRequests` as a separate liability bucket and pay them only from newly recovered funds added via `reserveDefaultRecovery` after defaulted claims are fully satisfied; (c) cap post-default payouts at `defaultRecoveryPrice` (the same haircut) so they can never exceed their pro-rata share.

### Proof of Concept
Foundry fork PoC sketch (mirroring `testFinalizeDefaultClaimsOldFundedAndDefaultedRedeemsInOneCall` in `test/foundry/IdleCreditVault.t.sol:4388`):

```solidity
// Setup: user V deposits 20k, requests withdraw, epoch stops, borrower defaults,
// finalizeDefault(recovered, manager) is called with recoveryRatio = 0.7.
// reserve = recovered; priced to cover V's claim at 0.7.

address attacker = makeAddr("attacker");
// Attacker acquires defaulted tranche tokens at post-default price
// (or held them pre-default). Their claimBasis vs active NAV is haircut to 0.7.
dealTranches(attacker, X);

uint256 reservePre = creditVault.defaultRecoveryReserve();

vm.startPrank(attacker);
uint256 receipt = cdoEpoch.requestWithdraw(0, address(AAtranche)); // mints postDefaultRequests
cdoEpoch.claimWithdrawRequest();                                  // pays receipt 1:1 from reserve
vm.stopPrank();

// Attacker received `receipt` underlying; reserve reduced by the same amount.
assertEq(reservePre - creditVault.defaultRecoveryReserve(), receipt);

// Victim V now tries to claim her defaulted-epoch receipt:
vm.prank(user);
vm.expectRevert(); // reserve exhausted -> safeTransfer fails or pays less than claimBasis*price
cdoEpoch.claimWithdrawRequest();
```

Key assertions: `postDefaultRequests[attacker] == receipt`, payout equals `receipt` (1:1, not `receipt * recoveryPrice`), and `defaultRecoveryReserve` decremented by `receipt` while V's claim basis remains unpaid.