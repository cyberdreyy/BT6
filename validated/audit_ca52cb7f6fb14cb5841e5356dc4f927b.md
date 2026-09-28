### Title
Post-default instant withdraw receipts drain `defaultRecoveryReserve` via stale `defaultEpoch` tagging - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The ChakraCore report is a memory-corruption/type-confusion bug: an object is interpreted under the wrong state, producing an out-of-bounds effect. The closest analog in this codebase is epoch-tag state confusion in the instant-withdraw receipt path of `IdleCreditVault`. `requestInstantWithdraw` accepts receipts after `defaultRecoveryFinalized` and files them under the current `epochNumber`, which still equals `defaultRecoveryEpoch` while the pool is defaulted. `claimInstantWithdrawRequest` then misclassifies these brand-new receipts as defaulted-epoch claims and pays them out of `defaultRecoveryReserve` at `defaultRecoveryPrice`, even though their claim basis was never included in `totalBasis` at finalization. Each such claim dilutes the isolated recovery reserve and can leave legitimate defaulted-epoch claimants unpayable (insolvency of the reserve).

### Finding Description
Relevant code (all in `contracts/strategies/idle/IdleCreditVault.sol`):

- `requestInstantWithdraw` (lines 356–375) calls `_ensureDefaultRecoveryInitialized()` but has **no** `defaultRecoveryFinalized` check, unlike `requestWithdraw` which explicitly routes post-default requests into `postDefaultRequests` (lines 247–257). It unconditionally does:
  - `instantWithdrawsRequestsByEpoch[_user][epochNumber] += _amount`
  - `instantWithdrawClaimsByEpoch[epochNumber] += _amount`
  - `pendingInstantWithdraws += _amount`
- `finalizeDefaultRecovery` (lines 690–696) sets `defaultRecoveryEpoch = epochNumber` and `defaultInstantWithdrawsFinalized = pendingInstantWithdraws != 0`, and computes `recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis` where `totalBasis` only counts receipts that existed at finalization (`defaultPendingClaimBasis`, lines 644–649).
- `claimInstantWithdrawRequest` (lines 380–393) calls `_claimDefaultedInstantWithdrawRequest(_user)` whenever `defaultRecoveryFinalized && defaultInstantWithdrawsFinalized`. That helper (lines 842–856) reads `instantWithdrawsRequestsByEpoch[_user][defaultEpoch]`, decrements `pendingInstantWithdraws` and `instantWithdrawClaimsByEpoch[defaultEpoch]`, and pays `claimBasis * defaultRecoveryPrice / RECOVERY_FULL` via `_transferDefaultRecovery`, which decrements `defaultRecoveryReserve` (lines 912–917).

Because a defaulted pool does not advance `epochNumber` (no further `stopEpoch`/`deposit`-driven increment while defaulted), a receipt created *after* finalization is keyed to the same `defaultEpoch` and is indistinguishable from a genuine defaulted receipt — classic stale-index/state confusion.

### Impact Explanation
`defaultRecoveryReserve` is sized exactly as `recoveryPrice * totalBasis / RECOVERY_FULL` for the claims recorded at finalization. Every post-default instant receipt pays `claimBasis * defaultRecoveryPrice / RECOVERY_FULL` from that reserve without having contributed to `totalBasis`. An attacker (any tranche-token holder able to call the CDO's instant-withdraw entry while defaulted) can create receipts summing to ~`totalBasis` equivalent basis and drain the reserve down to dust, after which legitimate defaulted-epoch claimants' `_transferDefaultRecovery` calls revert on underflow — direct theft by dilution / reserve insolvency, quantified as up to the full reserve minus the honest claims already paid. The broken invariant is the isolated-recovery accounting: one finalized basis, one reserve, no new claimants admitted post-finalization.

### Likelihood Explanation
Preconditions: pool enters default, `finalizeDefaultRecovery` runs with `pendingInstantWithdraws != 0` (so `defaultInstantWithdrawsFinalized` is true), and the CDO still exposes `requestInstantWithdraw` after default. On the strategy side there is no gate — `_onlyIdleCDO` is the only check. The residual uncertainty is whether `IdleCDOEpochVariant` blocks instant-withdraw requests once `defaulted()` is true; that gating could not be fully verified in this pass. If reachable, the attack costs the attacker only their (already haircut) tranche position and is repeatable.

### Recommendation
Mirror the `requestWithdraw` post-default handling: in `requestInstantWithdraw`, if `defaultRecoveryFinalized`, either revert or route the request into a post-default bucket paid at par from separately funded liquidity (never keyed to `defaultRecoveryEpoch`). Additionally, key defaulted-epoch instant claims to `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` only for receipts created before finalization (e.g., store a per-user `instantRequestFinalizedAt` marker), and make `_claimDefaultedInstantWithdrawRequest` reject claims whose basis was not part of `totalBasis`.

### Proof of Concept
```solidity
// Foundry fork test sketch — assumes IdleCDOEpochVariant still forwards
// requestInstantWithdraw while defaulted (verify gating on the CDO side).
function testPostDefaultInstantReceiptDrainsReserve() external {
    // 1. Normal epoch, borrowers funded, users deposit.
    // 2. Create a genuine instant withdraw request so pendingInstantWithdraws != 0.
    // 3. Warp past epochEndDate, manager calls stopEpoch(0,0) -> defaulted.
    // 4. Manager calls finalizeDefault(recovered, source) -> finalizeDefaultRecovery
    //    sets defaultRecoveryEpoch = epochNumber, defaultRecoveryPrice, reserve.
    uint256 reservePre = strategy.defaultRecoveryReserve();

    // 5. Attacker (unprivileged tranche holder) requests instant withdraw NOW.
    //    epochNumber == defaultRecoveryEpoch, so the receipt lands in the
    //    defaulted-epoch bucket with basis never counted in totalBasis.
    vm.prank(attacker);
    cdoEpoch.requestInstantWithdraw(attackAmount, attackerTranche);

    // 6. CDO claims on behalf of attacker:
    vm.prank(address(cdoEpoch));
    strategy.claimInstantWithdrawRequest(attacker);
    // attacker received attackAmount * defaultRecoveryPrice / RECOVERY_FULL
    // from defaultRecoveryReserve.

    // 7. Honest defaulted claimant now reverts / receives less:
    // assertLt(strategy.defaultRecoveryReserve(), reservePre - expectedHonestShare);
    // vm.expectRevert -> strategy.claimInstantWithdrawRequest(honestUser)
    // or claimWithdrawRequest underflows reserve.
}
```

Caveat: the strategy-side defect is confirmed by code reading; reachability depends on `IdleCDOEpochVariant.requestInstantWithdraw` not reverting while `defaulted() == true`, which should be the first assertion checked in the PoC.