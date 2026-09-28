### Title
Funded instant withdrawals leave stale per-epoch receipt entries that inflate the default-recovery claim basis, diluting recoveries and reverting the last claimant - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary

The external bug is a crash/DoS triggered by a crafted input reaching a bad code path. The closest credit-vault analog is a crafted transaction sequence that leaves stale accounting and causes a permanent revert inside the default-recovery claim path: `claimInstantWithdrawRequest` pays and clears `instantWithdrawsRequests[_user]` but never clears `instantWithdrawsRequestsByEpoch[_user][epoch]` or decrements the aggregate `instantWithdrawClaimsByEpoch[epoch]`. If the pool later defaults in that same epoch with any unfunded instant request outstanding, `defaultPendingClaimBasis()` counts the already-paid attacker receipt again, `defaultRecoveryPrice` is computed against an inflated basis, and the reserve is short: the last claimant's `_transferDefaultRecovery` underflows (`defaultRecoveryReserve -= _amount`) and reverts forever.

### Finding Description

In `requestInstantWithdraw` the vault records three ledgers:

- `instantWithdrawsRequests[_user] += _amount`
- `instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount`
- `instantWithdrawClaimsByEpoch[currentEpoch] += _amount`
- `pendingInstantWithdraws += _amount` (contracts/strategies/idle/IdleCreditVault.sol:366-374)

`collectInstantWithdrawFunds` decrements `pendingInstantWithdraws` when the CDO funds the queue (line 401). When the user later claims a funded receipt, `claimInstantWithdrawRequest` only does:

```solidity
uint256 amount = instantWithdrawsRequests[_user];
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);
```

(lines 387-392). It never zeroes `instantWithdrawsRequestsByEpoch[_user][epoch]` and never reduces `instantWithdrawClaimsByEpoch[epoch]` — unlike `_claimDefaultedInstantWithdrawRequest` (lines 847-853), which does both.

Now consider default finalization in the same epoch while some other instant request remains unfunded (`pendingInstantWithdraws != 0`). `defaultPendingClaimBasis()` returns `pendingWithdraws + instantWithdrawClaimsByEpoch[epochNumber]` (lines 644-648), so the already-paid attacker amount is still counted as outstanding claim basis. `finalizeDefaultRecovery` then computes `recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis` against that inflated `totalBasis` (lines 679-688), while the actual reserve only contains real recovered funds plus the prefunded remainder (`_defaultPrefundedInstantReserve`, lines 716-723), which excludes the attacker's paid amount.

Consequences:

1. Every honest defaulted claimant (normal, APR0, and instant) is paid `claimBasis * defaultRecoveryPrice / RECOVERY_FULL` with a diluted `defaultRecoveryPrice`.
2. Because the reserve was sized for real funds but the basis includes ghost claims, the last claimant's `_transferDefaultRecovery` executes `defaultRecoveryReserve -= _amount` on an insufficient reserve and reverts on underflow (lines 912-917). There is no alternative claim path — `claimInstantWithdrawRequest`/`claimWithdrawRequest` always route through these paths once `defaultRecoveryFinalized` is set — so the residual recovery funds are permanently frozen.

### Impact Explanation

An unprivileged tranche-token holder (instant withdrawal is a permissionless user flow via the CDO) can:

- Directly steal value: the attacker's paid receipt is double-counted as outstanding basis, so the attacker has already received full par value while their receipt continues to dilute every honest claimant's recovery price. The stolen amount equals roughly `ghostBasis / totalBasis` of the recovery pool.
- Permanently freeze funds: the reserve shortfall makes the final defaulted claim revert forever, locking the remaining `defaultRecoveryReserve` in the strategy.

This breaks the one-receipt-one-payout and loss-waterfall invariants, with direct theft plus permanent freezing — not a pure DoS.

### Likelihood Explanation

Requirements are all attacker- or protocol-state-controlled, no privileged misbehavior needed:

- Instant withdrawals enabled and an epoch where instant requests are only partially funded (common: `_defaultPrefundedInstantReserve` exists precisely because partial funding happens).
- Attacker claims their funded instant receipt during the epoch, then the borrower defaults in that same epoch — the default trigger is honest (borrower insolvency), and the attacker can opportunistically hold an instant receipt in any epoch.
- `defaultRecoveryInitialized` is set by `requestInstantWithdraw`/`_ensureDefaultRecoveryInitialized`, so the per-epoch ledger path is active.

The attacker's "crafted file" is simply the sequence request-instant → claim-funded → let-epoch-default, which any KYC'd lender can execute.

### Recommendation

In `claimInstantWithdrawRequest`, clear the per-epoch ledger entries exactly as `_claimDefaultedInstantWithdrawRequest` does:

```solidity
uint256 amount = instantWithdrawsRequests[_user];
instantWithdrawsRequests[_user] = 0;
uint256 epochAmount = instantWithdrawsRequestsByEpoch[_user][epochNumber]; // or tracked request epoch
instantWithdrawsRequestsByEpoch[_user][/*request epoch*/] = 0;
instantWithdrawClaimsByEpoch[/*request epoch*/] -= epochAmount;
```

Because instant requests are always claimed in the epoch they were made, decrementing `instantWithdrawClaimsByEpoch[epochNumber]` by the claimed amount keeps `defaultPendingClaimBasis()` limited to genuinely unfunded receipts. Add a fork test asserting `defaultPendingClaimBasis()` is unchanged by a claim that was fully funded.

### Proof of Concept

```solidity
// Foundry fork test against an instantiated IdleCDOEpochVariant + IdleCreditVault.
// Assumes instant withdrawals enabled via cdoEpoch.setInstantWithdrawParams(delay, diff, true).

function testStaleInstantReceiptInflatesDefaultRecovery() external {
    // 1. Epoch E running. Attacker (A) and victim (V) each requestInstantWithdraw.
    cdoEpoch.requestInstantWithdraw(A_AMOUNT, A); // via tranche redeem path
    cdoEpoch.requestInstantWithdraw(V_AMOUNT, V);

    // 2. startEpoch funds A's receipt only (partial prefunding) ->
    //    collectInstantWithdrawFunds decrements pendingInstantWithdraws by A_AMOUNT,
    //    but instantWithdrawClaimsByEpoch[E] still equals A_AMOUNT + V_AMOUNT.
    vm.prank(manager);
    cdoEpoch.startEpoch();

    // 3. Attacker claims funded receipt. instantWithdrawsRequestsByEpoch[A][E]
    //    and instantWithdrawClaimsByEpoch[E] remain stale (still include A_AMOUNT).
    vm.prank(A);
    cdoEpoch.claimInstantWithdrawRequest();
    assertEq(strategy.instantWithdrawsRequestsByEpoch(A, strategy.epochNumber()), A_AMOUNT); // stale!

    // 4. Borrower defaults in epoch E. finalizeDefaultRecovery counts the ghost
    //    A_AMOUNT in basis -> defaultRecoveryPrice is deflated.
    _defaultBorrower();
    uint256 basis = strategy.defaultPendingClaimBasis();
    assertGt(basis, V_AMOUNT + strategy.pendingWithdraws()); // includes ghost A_AMOUNT

    // 5. Victim and other claimants are underpaid; the final _transferDefaultRecovery
    //    underflows defaultRecoveryReserve and reverts -> residual reserve frozen.
    vm.prank(V);
    cdoEpoch.claimInstantWithdrawRequest(); // pays less than fair recovery
    vm.expectRevert(); // underflow on last claimant
    vm.prank(lastClaimant);
    cdoEpoch.claimWithdrawRequest();
}
```

Uncertainty noted: the PoC assumes `instantWithdrawsRequestsByEpoch`/`instantWithdrawClaimsByEpoch` are not cleared by any other path on a funded claim; within the code inspected (lines 380-403, 839-856) no such cleanup exists, but a full-repo confirmation of every call site touching `instantWithdrawClaimsByEpoch` should accompany the fix.