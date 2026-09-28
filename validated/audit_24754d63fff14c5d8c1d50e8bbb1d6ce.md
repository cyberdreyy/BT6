### Title
Post-default instant withdraw requests are booked into the defaulted epoch and paid from `defaultRecoveryReserve`, draining recovery funds owed to defaulted-epoch claimants - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
Analogous to the kernel bug (one path guarded the resident-attribute case, the parallel `ctx_needs_reset` path only warned and proceeded to interpret the wrong record type), `IdleCreditVault.requestWithdraw()` has an explicit `defaultRecoveryFinalized` branch that validates and isolates post-default requests, but the parallel `requestInstantWithdraw()` path has no default-state guard at all. A post-default instant request is recorded in `instantWithdrawsRequestsByEpoch[user][epochNumber]` and `instantWithdrawClaimsByEpoch[epochNumber]`, where `epochNumber` still equals `defaultRecoveryEpoch`, so `claimInstantWithdrawRequest()` treats it as a defaulted-epoch receipt and pays it out of `defaultRecoveryReserve`.

### Finding Description
In `requestWithdraw` (lines 243-258), the post-default case is explicitly handled: the user must have no open receipt, the receipt is stored in `postDefaultRequests`, and it is claimed 1:1 from the reserve via `_claimPostDefaultWithdrawRequest`.

In `requestInstantWithdraw` (lines 356-375) there is no `defaultRecoveryFinalized` check at all:

```solidity
function requestInstantWithdraw(uint256 _amount, address _user) external {
  _onlyIdleCDO();
  _ensureDefaultRecoveryInitialized();
  _burn(msg.sender, _amount);
  _mint(_user, _amount);
  instantWithdrawsRequests[_user] += _amount;
  uint256 currentEpoch = epochNumber;   // == defaultRecoveryEpoch after finalization
  instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
  instantWithdrawClaimsByEpoch[currentEpoch] += _amount;
  pendingInstantWithdraws += _amount;
}
```

After `finalizeDefaultRecovery`, `epochNumber` is not advanced (it only increments inside `deposit()` during a running epoch, and the pool is defaulted), so `currentEpoch == defaultRecoveryEpoch`. On `claimInstantWithdrawRequest` (lines 380-393), `defaultInstantWithdrawsFinalized` is true whenever any unfunded instant bucket existed at finalization, so `_claimDefaultedInstantWithdrawRequest` runs first and clears the attacker's freshly-recorded entry at `defaultEpoch`, paying `(claimBasis * defaultRecoveryPrice) / RECOVERY_FULL` via `_transferDefaultRecovery`, which decrements `defaultRecoveryReserve` (lines 842-856, 912-917).

The reserve was sized at finalization as `reserveAmount` against the fixed `totalBasis` of active holders plus default-epoch pending receipts. A new claim added after the fact draws down that fixed reserve, so when legitimate defaulted-epoch claimants later call `claimWithdrawRequest`/`claimInstantWithdrawRequest`, `defaultRecoveryReserve -= _amount` underflows (Solidity 0.8 checked arithmetic) and their claims revert permanently.

### Impact Explanation
Direct theft of the isolated default-recovery reserve and permanent freezing of other claimants' recovery. The attacker burns post-default strategy tokens (priced at the recovery haircut, i.e. cheap to acquire via deposit receipt claims or at haircut value) and withdraws reserve at `defaultRecoveryPrice` per unit of claim basis, consuming reserve that belongs to users whose receipts existed at default time. Loss = attacker's claim amount × `defaultRecoveryPrice`, up to the whole `defaultRecoveryReserve` remaining at time of attack.

### Likelihood Explanation
Requires `defaultInstantWithdrawsFinalized == true` (i.e. `pendingInstantWithdraws != 0` at finalization — a partially funded instant queue at default), and requires the IdleCDOEpochVariant's `requestInstantWithdraw` entrypoint to remain callable after default (it must be reachable by an unprivileged tranche holder burning tranche tokens; the strategy itself imposes no default check). One caveat I could not fully verify within tool limits: whether `IdleCDOEpochVariant` gates post-default instant requests independently; if it does not (consistent with the strategy-side assumption that the check lives in `requestWithdraw` only), the attack is a simple sequence — finalize default, request instant withdraw, claim instant withdraw.

### Recommendation
In `requestInstantWithdraw`, revert with `NotAllowed()` when `defaultRecoveryFinalized` is true (mirroring the explicit post-default branch in `requestWithdraw`), or record post-default instant requests in a separate bucket that cannot alias `defaultRecoveryEpoch`. Defense in depth: in `_claimDefaultedInstantWithdrawRequest`, cap `claimBasis` to the basis snapshotted at finalization rather than reading the mutable `instantWithdrawsRequestsByEpoch` entry, and revert if `epochNumber == defaultRecoveryEpoch` in `requestInstantWithdraw`.

### Proof of Concept
Foundry fork sketch (Extend `test/foundry/IdleCreditVault.t.sol` harness):

```solidity
function testPostDefaultInstantRequestDrainsReserve() external {
    // Setup: AA deposit, victim requests normal withdraw, epoch runs.
    uint256 amount = 10_000 * ONE_SCALE;
    _depositWithUser(victim, amount, true);
    vm.prank(victim);
    cdoEpoch.requestWithdraw(victimReceipt, address(AAtranche));

    _startEpochAndCheckPrices(0);
    // Borrower defaults at stopEpoch (returns 0).
    _stopEpochAndCheckPrices(0, initialProvidedApr, 0);
    _checkDefault();

    // Manager finalizes with partial recovery, e.g. 70%.
    uint256 basis = cdoEpoch.getContractValue()
        + cdoEpoch.expectedEpochInterest()
        - cdoEpoch.pendingWithdrawFees()
        + IdleCreditVault(address(strategy)).defaultPendingClaimBasis();
    uint256 recovered = basis * 7e17 / ONE_TRANCHE;
    deal(defaultUnderlying, manager, recovered);
    vm.startPrank(manager);
    IERC20Detailed(defaultUnderlying).approve(address(strategy), recovered);
    cdoEpoch.finalizeDefault(recovered, manager);
    vm.stopPrank();
    // Precondition: pendingInstantWithdraws != 0 was true at finalization
    // (arrange a partially funded instant request before default) so
    // defaultInstantWithdrawsFinalized == true.

    // Attack: attacker deposits post-default dust / uses haircut-priced tokens
    // and files an instant withdraw request; it lands in epoch == defaultRecoveryEpoch.
    vm.prank(attacker);
    cdoEpoch.requestInstantWithdraw(attackAmount, address(AAtranche));
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest(); // pays from defaultRecoveryReserve

    // Victim's defaulted-epoch claim now reverts on reserve underflow.
    vm.prank(victim);
    vm.expectRevert();
    cdoEpoch.claimWithdrawRequest();
}
```

Note: PoC validity depends on `IdleCDOEpochVariant.requestInstantWithdraw` remaining reachable post-default (not blocked by epoch-running or defaulted flags) and on `defaultInstantWithdrawsFinalized == true`; both should be confirmed in the harness. If the CDO already blocks it, no vulnerability exists at this layer.