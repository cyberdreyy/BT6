### Title
Missing default-state check in `requestInstantWithdraw` misclassifies post-default receipts as defaulted-epoch claims — (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`requestWithdraw` checks `defaultRecoveryFinalized` before touching any epoch-scoped receipt accounting, but `requestInstantWithdraw` does not perform the equivalent state check before writing `instantWithdrawsRequestsByEpoch[_user][epochNumber]` and `instantWithdrawClaimsByEpoch[epochNumber]` (IdleCreditVault.sol:356-375). After a default is finalized, `epochNumber` no longer advances, so a new instant receipt is recorded under `defaultRecoveryEpoch` and is then claimed through `_claimDefaultedInstantWithdrawRequest`, which pays it out of the isolated `defaultRecoveryReserve` — reserve that was sized only for claims existing at finalization (IdleCreditVault.sol:686-693, 842-856).

### Finding Description
The kernel bug class is "field access before state validation": `sk->sk_protocol` was read before confirming `sk_state`. The analog is `requestInstantWithdraw`, which writes full-socket-style, epoch-scoped receipt fields without first checking whether the vault is in the post-default state:

- `requestWithdraw` short-circuits on `defaultRecoveryFinalized` and routes the request to `postDefaultRequests`, never touching `withdrawsRequestsByEpoch`/`pendingWithdraws` (IdleCreditVault.sol:247-258).
- `requestInstantWithdraw` unconditionally executes `instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount`, `instantWithdrawClaimsByEpoch[currentEpoch] += _amount`, and `pendingInstantWithdraws += _amount` (IdleCreditVault.sol:366-374). `currentEpoch == defaultRecoveryEpoch` forever after finalization because `epochNumber` is only incremented in `deposit` during `stopEpoch` (IdleCreditVault.sol:607-611), which can no longer run on a defaulted pool.

On claim, `claimInstantWithdrawRequest` enters `_claimDefaultedInstantWithdrawRequest` when `defaultInstantWithdrawsFinalized` is true (IdleCreditVault.sol:382-386). That function reads `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` — now containing the attacker's new receipt — and pays `claimBasis * defaultRecoveryPrice / 1e18` out of `defaultRecoveryReserve` (IdleCreditVault.sol:842-855). The reserve was computed as `reserveAmount = _recoveredAmount + prefunded + defaultRecoveryReserve` with `recoveryPrice = reserveAmount * 1e18 / totalBasis` at finalization (IdleCreditVault.sol:685-692), i.e., exactly sized for pre-finalization claims. Every post-default instant claim withdraws `amount * price` from this fixed reserve while the corresponding burn only destroys receipt tokens minted after finalization — the active-NAV share of the reserve that backs other claimants is drained first-come-first-served.

If instead `defaultInstantWithdrawsFinalized` is false (no pending instant bucket at finalization), the claim falls through to `_transferFundedClaim`, whose guard `balance - reserve < _amount` always fails because no funds were ever collected for the new request (IdleCreditVault.sol:897-906). The receipt then permanently reverts — the user's tranche value was already burned at request time (`_burn(msg.sender, _amount)` at line 360) — permanently freezing that user's redeemed funds.

### Impact Explanation
Two loss modes depending on `defaultInstantWithdrawsFinalized`:

1. **Reserve theft/insolvency (flag true):** an attacker holding tranche tokens requests instant withdrawals post-default; each claim pays `amount * defaultRecoveryPrice` from `defaultRecoveryReserve`, a reserve that was fully allocated to legitimate defaulted-epoch receipt holders and post-default claims. Early attacker claims consume reserve earmarked for honest claimants, causing later `claimWithdrawRequest`/`claimInstantWithdrawRequest` calls to revert on `defaultRecoveryReserve -= _amount` underflow — permanent freezing of honest users' recovery.

2. **Permanent freezing (flag false):** any user's post-default instant request can never be claimed (`_transferFundedClaim` always reverts), so the underlying backing their burned tranche tokens is locked forever.

### Likelihood Explanation
Reachability requires the IdleCDO to route a post-default `requestWithdraw` to `requestInstantWithdraw`, which depends on `allowInstantWithdraw` remaining enabled after default — `testStopEpochWithDefault` shows `allowInstantWithdraw == true` after a stop-epoch default (test/foundry/IdleCreditVault.t.sol:2694). The attacker is an ordinary tranche-token holder (in scope); no privileged role is needed. The main uncertainty is whether the CDO's post-default request path passes the already-haircut `_amount` (mitigating mode 1 to a double-haircut self-loss) — I could not fully verify `IdleCDOEpochVariant.requestWithdraw`'s amount computation within the iteration budget. Mode 2 (permanent freeze) holds regardless of the amount passed.

### Recommendation
Mirror the `requestWithdraw` ordering in `requestInstantWithdraw`: check `defaultRecoveryFinalized` (or `IIdleCDOEpochVariant(idleCDO).defaulted()`) at the top of the function and either revert `NotAllowed()` or route the receipt to `postDefaultRequests` so it is paid 1:1 from reserve rather than being bucketed under `defaultRecoveryEpoch`. Additionally, `claimInstantWithdrawRequest` should not classify receipts written after `defaultRecoveryEpoch` was set as defaulted-epoch claims.

### Proof of Concept
Foundry fork sketch (setup mirrors `testPostDefaultWithdrawRequiresClaimingOpenPriorReceipt` in test/foundry/IdleCreditVault.t.sol:4619):

```solidity
function testPostDefaultInstantWithdrawDrainsRecoveryReserve() external {
    address honest = makeAddr('honest');
    address attacker = makeAddr('attacker');
    _depositWithUser(honest, 10_000 * ONE_SCALE, true);
    _depositWithUser(attacker, 10_000 * ONE_SCALE, true);

    // honest user files a normal withdraw request that will be defaulted
    vm.prank(honest);
    cdoEpoch.requestWithdraw(IERC20(AAtranche).balanceOf(honest) / 2, address(AAtranche));

    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr, 0); // borrower defaults
    _checkDefault();

    IdleCreditVault vault = IdleCreditVault(address(strategy));
    uint256 activeBasis = cdoEpoch.getContractValue()
        + cdoEpoch.expectedEpochInterest() - cdoEpoch.pendingWithdrawFees();
    uint256 recovered = (activeBasis + vault.defaultPendingClaimBasis()) * 7e17 / 1e18;
    deal(address(underlying), manager, recovered);
    vm.startPrank(manager);
    underlying.approve(address(strategy), recovered);
    cdoEpoch.finalizeDefault(recovered, manager);
    vm.stopPrank();
    assertTrue(vault.defaultRecoveryFinalized());

    uint256 reservePre = vault.defaultRecoveryReserve();

    // attacker files an INSTANT withdraw post-default; receipt is bucketed
    // under defaultRecoveryEpoch even though it was created after finalization
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(IERC20(AAtranche).balanceOf(attacker) / 2, address(AAtranche));
    assertGt(vault.instantWithdrawsRequestsByEpoch(attacker, vault.defaultRecoveryEpoch()), 0);

    // claim pays out of the fixed recovery reserve
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest();
    assertLt(vault.defaultRecoveryReserve(), reservePre, 'reserve drained by post-default receipt');

    // honest user's defaulted claim now reverts or is underpaid
    vm.prank(honest);
    vm.expectRevert(); // defaultRecoveryReserve underflow / NotAllowed
    cdoEpoch.claimWithdrawRequest();
}
```

Note: if the CDO does not expose an instant path post-default in a given configuration, the same PoC demonstrates the permanent-freeze mode when `defaultInstantWithdrawsFinalized == false`. The core defect — writing epoch-scoped receipt state before validating vault state — is directly visible in the source at IdleCreditVault.sol:356-375 versus the guarded counterpart at IdleCreditVault.sol:247-258.