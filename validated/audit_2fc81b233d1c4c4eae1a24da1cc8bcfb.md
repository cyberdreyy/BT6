### Title
Stale `instantWithdrawClaimsByEpoch`/`instantWithdrawsRequestsByEpoch` after `claimInstantWithdrawRequest` inflate `finalizeDefaultRecovery` basis and reserve, overpaying early claimants and permanently freezing recovery funds - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`requestInstantWithdraw` records both a per-user per-epoch receipt (`instantWithdrawsRequestsByEpoch[user][epoch]`) and an aggregate per-epoch counter (`instantWithdrawClaimsByEpoch[epoch]`), but `claimInstantWithdrawRequest` clears only `instantWithdrawsRequests[user]` — neither per-epoch counter is decremented. These stale per-epoch values are later consumed by `defaultPendingClaimBasis()` and `_defaultPrefundedInstantReserve()` during `finalizeDefaultRecovery`, inflating both `totalBasis` and the assumed already-held `reserveAmount` by amounts that were already paid out and left the contract. The result is an inflated `defaultRecoveryPrice` and a `defaultRecoveryReserve` larger than the real balance, so early post-default claimants are overpaid and later claims revert on underflow — a direct analog of the stale `inFlightBridgeAmounts` bug: an in-flight/settled amount is never cleared and is double-counted in a later accounting computation.

### Finding Description
In `contracts/strategies/idle/IdleCreditVault.sol`:

- `requestInstantWithdraw` (lines 356–375) increments `instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch[_user][currentEpoch]`, `instantWithdrawClaimsByEpoch[currentEpoch]`, and `pendingInstantWithdraws`.
- `collectInstantWithdrawFunds` (lines 398–403) decrements `pendingInstantWithdraws` when the CDO funds the instant queue.
- `claimInstantWithdrawRequest` (lines 380–393) burns the user's receipt tokens, zeroes `instantWithdrawsRequests[_user]`, and transfers the funded underlyings — but **never** clears `instantWithdrawsRequestsByEpoch[_user][epoch]` or decrements `instantWithdrawClaimsByEpoch[epoch]`. These entries are stale once the receipt is claimed.

Both stale counters feed default-recovery accounting:

- `defaultPendingClaimBasis` (lines 644–649): `basis += instantWithdrawClaimsByEpoch[epochNumber]` whenever `pendingInstantWithdraws != 0`.
- `_defaultPrefundedInstantReserve` (lines 716–723): `prefundedReserve = instantWithdrawClaimsByEpoch[epochNumber] - pendingInstantWithdraws`, i.e. the "already-funded and still-held" portion.
- `finalizeDefaultRecovery` (lines 661–710) computes `recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis` and stores `defaultRecoveryReserve = reserveAmount`, where `reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve`.

Because the claimed amount `C` remains in `instantWithdrawClaimsByEpoch`, `totalBasis` gains `C` it should not, and `prefundedReserve` — and therefore `reserveAmount` — is inflated by the same `C`, even though those `C` underlyings were already transferred out to the claimant. Since adding `C` to numerator and denominator pushes `recoveryPrice` toward 1, the stored price exceeds the true ratio and `defaultRecoveryReserve` exceeds the contract's actual balance by `C`. Each `_transferDefaultRecovery` (lines 912–917) does `defaultRecoveryReserve -= _amount` against this phantom reserve; once real funds are exhausted, subsequent claims revert on underflow (`_claimDefaultedInstantWithdrawRequest` line 852's `pendingInstantWithdraws` subtraction and `_claimDefaultedWithdrawRequest` line 778's `pendingWithdraws` subtraction also underflow), permanently freezing the remaining claimants' recovery.

The attacker cannot double-claim their own stale per-epoch receipt (`instantWithdrawsRequests[_user] -= claimBasis` at line 848 underflows on a second attempt), so the exploit is the aggregate inflation: the attacker's already-settled receipt silently re-enters the recovery basis.

Sequence (unprivileged, KYC-passing lender; honest privileged roles only sequence the calls):

1. Epoch N running. Attacker calls CDO `requestInstantWithdraw(A)`. `instantWithdrawClaimsByEpoch[N] = A`, `pendingInstantWithdraws = A`.
2. Borrower/manager flow funds the instant queue; CDO calls `collectInstantWithdrawFunds(A)` → `pendingInstantWithdraws = 0`, `A` underlyings held in the strategy.
3. Attacker calls `claimInstantWithdrawRequest` → receives `A` at par. `instantWithdrawClaimsByEpoch[N]` is still `A` (stale).
4. Victim requests instant withdraw `V` in the same epoch N → `pendingInstantWithdraws = V`, `instantWithdrawClaimsByEpoch[N] = A + V`.
5. Borrower defaults; guardian path calls `_handleBorrowerDefault` → `finalizeDefaultRecovery` runs while `epochNumber == N` and `pendingInstantWithdraws = V != 0`.
6. `instantBasis = A + V`, `prefundedReserve = (A + V) - V = A` (phantom — already paid out in step 3). `totalBasis` and `reserveAmount` each inflated by `A` → inflated `defaultRecoveryPrice` and `defaultRecoveryReserve`.
7. Claimants draw at the inflated price until the phantom `A` is exhausted; remaining claims revert permanently.

### Impact Explanation
Direct theft from and permanent freezing of default-recovery funds. Early claimants (which can include the attacker via a fresh post-default request or a co-claimant) are paid an inflated recovery price funded by money that no longer exists; the final claimants' recovery claims revert on underflow forever, since `defaultRecoveryReserve`/`defaultRecoveryPrice` are immutable once `defaultRecoveryFinalized` is set. Loss is quantified as `A` (the stale claimed amount) either stolen via the inflated price or permanently locked. This breaks the "one receipt one payout" and reserve-solvency invariants.

### Likelihood Explanation
Medium. Requires only: (a) an attacker with a funded-and-claimed instant withdraw inside an epoch that later defaults with at least one other unfunded instant request outstanding — a normal sequence needing no special permissions beyond being a lender; and (b) a borrower default finalized in the same epoch number, which is exactly the scenario `defaultInstantWithdrawsFinalized = pendingInstantWithdraws != 0` (line 696) is designed for. No privileged misbehavior is required; the stale counter is a deterministic consequence of the missing cleanup in `claimInstantWithdrawRequest`.

### Recommendation
Clear the per-epoch instant-withdraw accounting when the funded receipt is claimed in `claimInstantWithdrawRequest`. Track the request epoch (e.g., a `lastInstantWithdrawRequest` mapping or clearing the per-epoch entry for the epoch recorded at request time) and decrement both `instantWithdrawsRequestsByEpoch[_user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]` by the claimed amount, mirroring how `_clearWithdrawClaimForEpoch` (lines 811–837) cleans per-epoch normal receipts. Alternatively, settle the aggregate in `collectInstantWithdrawFunds`/`claimInstantWithdrawRequest` so that `instantWithdrawClaimsByEpoch` only ever reflects still-outstanding receipt basis.

### Proof of Concept
Foundry fork/test sketch against `IdleCreditVault` + `IdleCDOEpochVariant` (exact helper names may need adjusting to repo test harness):

```solidity
// test/foundry/InstantWithdrawStaleBasis.t.sol
function testStaleInstantClaimInflatesDefaultRecovery() public {
    // --- Epoch N running, attacker is a KYC'd lender ---
    uint256 A = 100e6;  // attacker instant request
    uint256 V = 50e6;   // victim instant request

    // 1) attacker requests instant withdraw in epoch N
    cdo.requestInstantWithdraw(A, attacker);
    assertEq(strategy.instantWithdrawClaimsByEpoch(strategy.epochNumber()), A);

    // 2) honest manager/borrower path funds the instant queue
    //    (IdleCDO calls collectInstantWithdrawFunds -> pendingInstantWithdraws = 0)
    fundInstantQueue(A);
    assertEq(strategy.pendingInstantWithdraws(), 0);

    // 3) attacker claims at par; per-epoch counters stay stale
    cdo.claimInstantWithdrawRequest(attacker);
    assertEq(strategy.instantWithdrawsRequests(attacker), 0);
    assertEq(strategy.instantWithdrawClaimsByEpoch(strategy.epochNumber()), A); // BUG: stale

    // 4) victim requests instant withdraw in same epoch N, left unfunded
    cdo.requestInstantWithdraw(V, victim);
    assertEq(strategy.pendingInstantWithdraws(), V);
    assertEq(strategy.instantWithdrawClaimsByEpoch(strategy.epochNumber()), A + V);

    // 5) borrower defaults; owner/guardian finalizes recovery in same epoch
    borrowerDefault(); // defaulted() = true
    uint256 recovered = realRecovery; // honest recovery amount
    cdo.finalizeDefaultRecovery(recovered, recoverySource);

    // basis counted A+V though only V is outstanding;
    // prefundedReserve counted A that was already paid out
    uint256 price = strategy.defaultRecoveryPrice();
    uint256 reserve = strategy.defaultRecoveryReserve();
    assertGt(reserve, strategy.underlyingToken().balanceOf(address(strategy)) - otherReserves);

    // 6) last claimant reverts: defaultRecoveryReserve underflows / balance insufficient
    claimAllButLast();               // earlier claimants paid at inflated price
    vm.expectRevert();               // phantom A exhausted
    cdo.claimInstantWithdrawRequest(lastClaimant);
}
```

The key assertions are steps 3 and 5: `instantWithdrawClaimsByEpoch[N]` remains `A` after the receipt is fully claimed, and `defaultRecoveryReserve` exceeds the strategy's actual claimable balance by exactly `A`.