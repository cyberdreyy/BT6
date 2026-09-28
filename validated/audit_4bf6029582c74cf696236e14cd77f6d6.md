### Title
Stale instant-withdraw claim basis after `claimInstantWithdrawRequest` dilutes default-recovery price and permanently locks recovery funds - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`requestInstantWithdraw` records per-epoch receipt basis in `instantWithdrawsRequestsByEpoch` and `instantWithdrawClaimsByEpoch`, but the normal claim path `claimInstantWithdrawRequest` only clears `instantWithdrawsRequests` and never decrements either per-epoch counter. When the borrower defaults during an epoch that had already-claimed (fully paid) instant withdrawals, `finalizeDefaultRecovery` counts the stale basis via `defaultPendingClaimBasis`, crushing `defaultRecoveryPrice` for genuinely unfunded claimants while the excess reserve stays locked in the strategy forever.

### Finding Description
Instant-withdraw accounting maintains three structures:

- `instantWithdrawsRequests[_user]` — aggregate receipt balance (lines 106-107 area, used at lines 366, 387).
- `instantWithdrawsRequestsByEpoch[_user][epoch]` — per-user per-epoch basis, incremented at line 371.
- `instantWithdrawClaimsByEpoch[epoch]` — per-epoch total basis, incremented at line 372.

The normal claim path cleans up only the first one:

```solidity
// IdleCreditVault.sol:387-392
uint256 amount = instantWithdrawsRequests[_user];
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);
```

`instantWithdrawsRequestsByEpoch` and `instantWithdrawClaimsByEpoch` are only ever decremented inside `_claimDefaultedInstantWithdrawRequest` (lines 847-853), i.e. they are treated as "cleanup deferred to default finalization". That cleanup never runs for receipts already paid at par.

At default finalization, `defaultPendingClaimBasis` (lines 644-649) adds `instantWithdrawClaimsByEpoch[epochNumber]` whenever `pendingInstantWithdraws != 0`:

```solidity
basis = pendingWithdraws;
if (pendingInstantWithdraws != 0) {
  basis += instantWithdrawClaimsByEpoch[epochNumber];
}
```

`finalizeDefaultRecovery` (lines 679-692) then computes `recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis` with this inflated `totalBasis`. Every unfunded defaulted receipt — normal pending withdraws and unfunded instant receipts alike — is paid `claimBasis * defaultRecoveryPrice / RECOVERY_FULL`, so all of them are underpaid. Because the inflated basis is never actually claimable (the already-paid users have `instantWithdrawsRequests[_user] == 0`, and any attempt to re-claim via `_claimDefaultedInstantWithdrawRequest` reverts on the `instantWithdrawsRequests[_user] -= claimBasis` underflow at line 848), the corresponding share of `defaultRecoveryReserve` can never be distributed and there is no sweep function to recover it.

### Impact Explanation
Direct, quantified loss for unfunded withdraw-request holders plus permanent freezing of recovery funds. Example: during epoch N, attacker (any KYC'd tranche holder) requests a 9,000,000 USDC instant withdrawal and claims it normally once funds are collected — `instantWithdrawClaimsByEpoch[N]` keeps 9,000,000. A victim requests 100,000 instant in the same epoch but stays unfunded (`pendingInstantWithdraws = 100,000`). The borrower defaults; the recovery source supplies exactly 100,000 as full recovery for the instant bucket (assume no other pending basis). Correct price would be 1e18 (par); actual price is `100,000e18 / 1,000,000,000`-scaled = ~1.1% of par. The victim receives ~1.1% of their 100,000 instead of 100%, and ~98,900 USDC of the reserve remains stranded in the strategy with no withdrawal path. The inflation scales linearly with total already-claimed instant volume in the epoch, so the loss approaches 100% of unfunded claims.

### Likelihood Explanation
Requires only ordinary user behavior in a running epoch with `allowInstantWithdraw` enabled: one or more users claim instant withdrawals (routine), at least one instant or normal withdraw request remains unfunded, and the borrower subsequently defaults and finalization is triggered by the honest owner/borrower. No privileged misbehavior, no exotic sequencing — the trigger condition (`pendingInstantWithdraws != 0` at finalization) is precisely the case the default-instant path was built for. The stale-basis window exists for every epoch that sees any claimed instant withdrawal.

### Recommendation
In `claimInstantWithdrawRequest`, clean up the per-epoch records alongside the aggregate: zero `instantWithdrawsRequestsByEpoch[_user][epochNumber]` and decrement `instantWithdrawClaimsByEpoch[epochNumber]` by the claimed amount (with the same request-epoch tracking used elsewhere, since the claim epoch is the request epoch for instant flows). This keeps `defaultPendingClaimBasis` limited to genuinely unfunded receipts. Alternatively, clear `instantWithdrawsRequestsByEpoch` at claim time and derive the funded/unfunded split from `pendingInstantWithdraws` alone.

### Proof of Concept
Foundry fork PoC sketch (mainnet fork, existing epoch vault with instant withdraws enabled):

```solidity
function testStaleInstantBasisDilutesRecovery() public {
    // epoch running, instantDelay elapsed, strategy holds collected instant funds
    uint256 A = 9_000_000e6;
    uint256 V = 100_000e6;

    // attacker: request + claim instant withdraw in epoch N
    vm.prank(attacker);
    cdoEpoch.requestInstantWithdraw(sharesAttacker, address(aaTranche));
    skip(instantDelay + 1);
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest();          // paid at par
    assertEq(strategy.instantWithdrawsRequests(attacker), 0);
    // stale basis remains:
    assertEq(strategy.instantWithdrawClaimsByEpoch(strategy.epochNumber()), A + V_pending);

    // victim requests V instant, stays unfunded
    vm.prank(victim);
    cdoEpoch.requestInstantWithdraw(sharesVictim, address(aaTranche));
    assertGt(strategy.pendingInstantWithdraws(), 0);

    // borrower defaults; owner finalizes recovery with "full" funding
    _defaultAndFinalize(recoveryForUnfunded);

    uint256 price = strategy.defaultRecoveryPrice();
    // price << RECOVERY_FULL because basis included attacker's already-paid A
    uint256 paid = _claimInstant(victim);
    assertLt(paid, V / 10);                        // victim loses >90%
    // stranded reserve: reserve - actually-paid can never leave the strategy
    assertGt(underlying.balanceOf(address(strategy)) - expectedResidual, 0);
}
```