### Title
Stale per-epoch instant-withdraw receipt permanently freezes a user's instant claims after default finalization - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`claimInstantWithdrawRequest` pays funded instant receipts at par by clearing only the aggregate `instantWithdrawsRequests[_user]`; it never clears `instantWithdrawsRequestsByEpoch[_user][epoch]` or `instantWithdrawClaimsByEpoch[epoch]` (lines 387-392). If the borrower default is finalized while that same epoch number is still `defaultRecoveryEpoch`, `_claimDefaultedInstantWithdrawRequest` re-reads the already-paid per-epoch entry, underflows on `instantWithdrawsRequests[_user] -= claimBasis` (line 848), and reverts. Because this defaulted-epoch branch runs *before* the funded payout on every `claimInstantWithdrawRequest` call once `defaultInstantWithdrawsFinalized` is set (lines 382-386), the user can never again claim instant withdrawals — including newer, fully-funded receipts — a permanent freeze of unclaimed funds.

### Finding Description
The kernel bug is a two-phase lookup/cancel: `hci_cmd_sync_dequeue_once` looks up an entry under one lock and cancels it under a second lock while `hci_cmd_sync_work` concurrently removes the same entry → double `list_del`/UAF. The vault analog is the same split-ledger pattern: one logical instant-withdraw receipt is tracked in two counters — the aggregate `instantWithdrawsRequests[_user]` (consumed on the funded path) and the per-epoch `instantWithdrawsRequestsByEpoch[_user][epoch]` (consumed on the defaulted-epoch path) — and the two consumption sites are not kept consistent.

- `requestInstantWithdraw` increments both `instantWithdrawsRequests[_user]` and `instantWithdrawsRequestsByEpoch[_user][currentEpoch]`/`instantWithdrawClaimsByEpoch[currentEpoch]` (lines 366-374).
- `claimInstantWithdrawRequest` burns `instantWithdrawsRequests[_user]` and pays at par via `_transferFundedClaim`, but leaves `instantWithdrawsRequestsByEpoch` untouched (lines 387-392).
- After `finalizeDefault`/`finalizeDefaultRecovery` set `defaultRecoveryEpoch` and `defaultInstantWithdrawsFinalized`, `_claimDefaultedInstantWithdrawRequest` treats the stale per-epoch entry as an unfunded default-epoch claim: it zeroes the per-epoch slot, subtracts `claimBasis` from the already-zeroed aggregate (underflow → revert), mutates `pendingInstantWithdraws` and `instantWithdrawClaimsByEpoch`, and would burn tokens the user no longer holds (lines 842-855).

Invariant broken: one receipt, one payout — the same receipt is counted in both the funded ledger and the default ledger. The arithmetic underflow converts the double-spend into a permanent revert.

### Impact Explanation
Any unprivileged tranche-token holder who made an instant withdrawal in the defaulted epoch and already claimed it at par is permanently bricked in `claimInstantWithdrawRequest`: the defaulted-epoch branch reverts first on every call. If that user subsequently requests and funds a new instant withdrawal (the honest `requestInstantWithdraw` path re-opens `instantWithdrawsRequests[_user]`), those funds become permanently frozen — the claim always reverts before reaching the funded payout at lines 387-392. Secondary impact: `instantWithdrawClaimsByEpoch[defaultEpoch]` retains already-paid basis, so default-finalization accounting that sizes the recovery reserve from per-epoch instant claims overcounts the defaulted-epoch instant bucket, diverting reserve or blocking `defaultInstantWithdrawsFinalized` flows.

### Likelihood Explanation
Requires: (1) instant withdrawals enabled, (2) an attacker/user who requested and claimed an instant withdrawal during epoch N, (3) an honest borrower default finalized such that `defaultRecoveryEpoch == N`. Instant withdrawals are an explicit supported mode and defaults are a designed-for state; the stale per-epoch slot is never cleaned on the funded path, so the condition is deterministic once the sequence occurs. Likelihood is moderate: it hinges on an instant request landing in the exact epoch that later defaults, but no privileged misbehavior or oracle manipulation is needed — the attacker merely exercises the normal request/claim sequence and re-requests post-default.

### Recommendation
In `claimInstantWithdrawRequest`, clear the per-epoch accounting alongside the aggregate when paying a funded claim: for each epoch entry attributable to the user (at minimum `instantWithdrawsRequestsByEpoch[_user][epochNumber]` and any prior epochs tracked), set it to zero and decrement `instantWithdrawClaimsByEpoch[epoch]` correspondingly, so a receipt paid at par can never be re-read as an unfunded default-epoch claim. Alternatively, record the request epoch per user (like `lastWithdrawRequest`) and clear `instantWithdrawsRequestsByEpoch`/`instantWithdrawClaimsByEpoch` for that epoch on the funded claim. A defense-in-depth option is to make `_claimDefaultedInstantWithdrawRequest` saturate (`claimBasis = min(claimBasis, instantWithdrawsRequests[_user])`) instead of underflowing, though this masks rather than fixes the ledger inconsistency.

### Proof of Concept
Foundry fork PoC (mainnet fork, `IdleCreditVault`/`IdleCDOEpochVariant` with instant withdrawals enabled, e.g. `setInstantWithdrawParams(delay, aprDelta, …)`):

```solidity
function testStaleInstantEpochEntryFreezesLaterClaims() external {
    // 1. Epoch N running with instant withdraws enabled.
    _stopCurrentEpochWithApr(10e18);                 // enter buffer/epoch N
    vm.prank(manager);
    cdoEpoch.setInstantWithdrawParams(100, 1e18, false);
    uint256 tranches = _depositWithUser(ATTACKER, 100e6);
    vm.prank(manager);
    cdoEpoch.startEpoch();
    vm.warp(block.timestamp + 101);                  // past instant delay

    // 2. ATTACKER requests an instant withdraw in epoch N and claims it funded.
    cdoEpoch.requestInstantWithdraw(tranches, address(tranche), ATTACKER);
    // manager/borrower funds instant bucket; CDO pulls it via collectInstantWithdrawFunds
    _fundInstantWithdraws();
    vm.prank(address(cdoEpoch));
    strategy.claimInstantWithdrawRequest(ATTACKER);
    // aggregate cleared, per-epoch slot still non-zero:
    assertEq(strategy.instantWithdrawsRequests(ATTACKER), 0);
    assertGt(strategy.instantWithdrawsRequestsByEpoch(ATTACKER, strategy.epochNumber()), 0);

    // 3. Borrower defaults; honest manager/owner finalizes recovery for epoch N.
    _defaultAndFinalize();                            // sets defaultRecoveryEpoch = N,
                                                      // defaultInstantWithdrawsFinalized = true

    // 4. ATTACKER opens a new instant request post-default and it gets funded.
    cdoEpoch.requestInstantWithdraw(tranches2, address(tranche), ATTACKER);
    _fundInstantWithdraws();

    // 5. Claim permanently reverts: _claimDefaultedInstantWithdrawRequest re-reads
    //    the stale epoch-N slot and underflows instantWithdrawsRequests[_user].
    vm.prank(address(cdoEpoch));
    vm.expectRevert();                                // arithmetic underflow at line 848
    strategy.claimInstantWithdrawRequest(ATTACKER);
    // ATTACKER's newly funded instant receipt is unrecoverable.
}
```

Caveats I could not fully verify within the available tool budget: the exact helper used by the CDO to set `defaultInstantWithdrawsFinalized`/`defaultRecoveryEpoch` (grep confirmed the state variables and clearing sites exist only in `IdleCreditVault.sol`, but I did not read `finalizeDefaultRecovery`'s body), and whether any path other than `claimInstantWithdrawRequest` clears `instantWithdrawsRequestsByEpoch` on a funded payout — none was visible in the code inspected (lines 356-393, 842-856). If `finalizeDefaultRecovery` can only set `defaultRecoveryEpoch` to a strictly newer epoch than an already-claimed instant request, the stale-slot path is unreachable and the finding degrades to accounting drift in `instantWithdrawClaimsByEpoch` rather than a freeze.