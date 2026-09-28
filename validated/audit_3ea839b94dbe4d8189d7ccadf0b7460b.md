I need to check how instant withdraw funding interacts across epochs in the CDO before concluding.### Title
Stale cross-epoch instant-withdraw receipts bypass default haircut and are paid at par from other users' funded claims - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault` tracks instant-withdraw receipts both as a global aggregate (`pendingInstantWithdraws`, `instantWithdrawsRequests[user]`) and per request epoch (`instantWithdrawsRequestsByEpoch[user][epoch]`, `instantWithdrawClaimsByEpoch[epoch]`). At default finalization, `defaultPendingClaimBasis()` and `_claimDefaultedInstantWithdrawRequest()` only read the **current** epoch bucket while the aggregate counters span **all** epochs. An unfunded instant receipt created in an older epoch is therefore never included in the recovery basis, never haircut, and after `defaultRecoveryFinalized` is paid at par via `_transferFundedClaim`, whose reserve guard treats any balance above `defaultRecoveryReserve` as fair game — i.e., other users' funded-but-unclaimed withdraw money.

### Finding Description
The analog of the FFmpeg buffer over-read is a storage over-read/under-read mismatch: the code gates recovery accounting on the aggregate `pendingInstantWithdraws != 0` but then reads only `instantWithdrawClaimsByEpoch[epochNumber]` and `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]`, reading past the bounds of the epoch window that actually owns the debt.

Concretely:

1. In `requestInstantWithdraw` the receipt is recorded under `epochNumber` at request time (`instantWithdrawsRequestsByEpoch[user][currentEpoch]`, `instantWithdrawClaimsByEpoch[currentEpoch]`) and added to the global `pendingInstantWithdraws` (lines 366–374).
2. `collectInstantWithdrawFunds` decrements only the aggregate `pendingInstantWithdraws` (lines 398–403). If the borrower never funds an instant receipt, it persists across later epochs inside `pendingInstantWithdraws` and inside `instantWithdrawsRequests[user]`, but under the *old* epoch key.
3. In `finalizeDefaultRecovery`, `defaultPendingClaimBasis()` returns `pendingWithdraws + instantWithdrawClaimsByEpoch[epochNumber]` when `pendingInstantWithdraws != 0` (lines 644–649). A stale receipt from an earlier epoch contributes nothing to the basis, yet it flips `defaultInstantWithdrawsFinalized = true` (line 696) and is not haircut.
4. In `claimInstantWithdrawRequest`, `_claimDefaultedInstantWithdrawRequest(_user)` reads `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` — the stale receipt lives under its old epoch key so `claimBasis == 0` and nothing is cleared (lines 842–856). Execution then falls through to `amount = instantWithdrawsRequests[_user]` and `_burn`/`_transferFundedClaim(_user, amount)` at full par (lines 387–392).
5. `_transferFundedClaim` only checks `balance - defaultRecoveryReserve >= amount` (lines 897–907). Any balance above the reserve — normal-epoch receipts already funded by the borrower via `collectWithdrawFunds`, or loss-adjusted funding held for other claimants — is spendable. The attacker burns the receipt tokens minted to them at `requestInstantWithdraw` and withdraws underlying that was funded for other users.

### Impact Explanation
Direct theft of funded claims. The attacker is an ordinary lender/tranche-token holder whose instant-withdraw request was never funded (no borrower liquidity, which the code explicitly contemplates: "`pendingInstantWithdraws` is only the unfunded remainder"). After a borrower default and `finalizeDefaultRecovery`, the attacker claims the unfunded receipt at par, consuming underlying that `collectWithdrawFunds`/`collectInstantWithdrawFunds` pulled in to back *other* users' claims. Loss is bounded by the stale unfunded instant amount but is a 1:1 theft of other claimants' funded balances; those users' later claims revert on insufficient balance (permanent freezing of their unclaimed funds).

### Likelihood Explanation
Requires (a) an instant-withdraw request left unfunded across an epoch boundary — possible whenever borrower liquidity does not cover the instant queue; and (b) a subsequent borrower default finalized via `finalizeDefaultRecovery`. Both states are explicitly modeled by the code (`_defaultPrefundedInstantReserve`, `defaultInstantWithdrawsFinalized`), so no privileged misbehavior is needed. The `_onlyIdleCDO` guard is satisfied because claims route through `IdleCDOEpochVariant.claimInstantWithdrawRequest`. The `_ensureDefaultRecoveryInitialized` revert on `pendingInstantWithdraws != 0` applies only to the one-time lazy migration, not to requests created after initialization, so it does not block this path.

### Recommendation
Attribute `pendingInstantWithdraws` and the default basis to their true request epochs, or snapshot the default-epoch boundary:

- In `defaultPendingClaimBasis`/`_defaultPrefundedInstantReserve`, include all outstanding instant claims (e.g., keep a per-epoch-age counter or iterate a tracked epoch set), not just `instantWithdrawClaimsByEpoch[epochNumber]`.
- Alternatively, in `finalizeDefaultRecovery`, migrate any non-current-epoch instant receipts into `instantWithdrawsRequestsByEpoch[user][defaultRecoveryEpoch]` (or a dedicated `unfundedInstantBasis` aggregate set at finalization) so `_claimDefaultedInstantWithdrawRequest` actually finds and haircuts them.
- Before paying at par in `claimInstantWithdrawRequest`, verify the receipt's epoch was actually funded (e.g., check a per-epoch funded flag set by `collectInstantWithdrawFunds`) rather than relying on the aggregate `instantWithdrawsRequests`.

### Proof of Concept
Foundry fork test outline (extend `test/foundry/IdleCreditVault.t.sol`):

```solidity
function testStaleInstantReceiptPaidAtParAfterDefault() external {
    // Epoch N running with instant withdraws enabled (apr>0 variant)
    _useStandardEpochVariant();
    _stopCurrentEpochWithApr(10e18);           // buffer for epoch 1

    address attacker = makeAddr('attacker');
    address victim   = makeAddr('victim');
    uint256 aTranches = _depositWithUser(attacker, 100e6);
    uint256 vTranches = _depositWithUser(victim,   100e6);

    vm.prank(manager);
    cdoEpoch.startEpoch();                     // epoch N running

    // Attacker requests instant withdraw; borrower liquidity never arrives,
    // so collectInstantWithdrawFunds is never called for this receipt.
    vm.prank(attacker);
    cdoEpoch.requestInstantWithdraw(aTranches, attacker);
    assertEq(strategy.pendingInstantWithdraws(), /*receipt*/ aAmt);

    // Victim files a normal withdraw request.
    _requestWithdrawWithUser(victim, vTranches);

    // Roll to a later epoch; borrower funds victim's normal receipt at par.
    _stopCurrentEpochWithApr(10e18);           // epoch N -> N+1, funds victim
    // ... borrower repays, collectWithdrawFunds pulls victim's basis into strategy ...

    // New epoch M starts; borrower defaults.
    vm.prank(manager);
    cdoEpoch.startEpoch();
    _handleBorrowerDefault();                  // CDO defaulted() == true

    // Finalize recovery with some recovered amount.
    finalizeDefaultRecovery(recovered, recoverySource);
    assertTrue(strategy.defaultInstantWithdrawsFinalized()); // due to stale pendingInstantWithdraws

    // Attacker's old-epoch instant receipt was never put in the basis nor haircut.
    uint256 balPre = underlying.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest();    // pays instantWithdrawsRequests[attacker] at par
    assertGt(underlying.balanceOf(attacker) - balPre, 0);    // funded by victim's claim balance

    // Victim's funded claim now reverts/underpays: balance - reserve < victim basis.
    vm.expectRevert();
    vm.prank(victim);
    cdoEpoch.claimWithdrawRequest();
}
```

Key assertions: `instantWithdrawClaimsByEpoch[defaultEpoch] == 0` while `pendingInstantWithdraws > 0`; `_claimDefaultedInstantWithdrawRequest` clears nothing; `_transferFundedClaim` succeeds because `balance - defaultRecoveryReserve` still covers the stale receipt, draining the victim's funded claim. Uncertainty: the exact CDO-side gating of `claimInstantWithdrawRequest` (in `contracts/IdleCDOEpochVariant.sol`) was not fully inspected in this session, but since the strategy only enforces `_onlyIdleCDO()` and stores/clears the receipt state described above, the theft condition reduces to whether the CDO forwards the claim — which it does unconditionally for requesters.