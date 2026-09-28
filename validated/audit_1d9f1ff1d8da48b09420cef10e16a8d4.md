The bug class here is "a value that should be cleared/zeroed is missing from the reset path," leaving stale state. I need to check the epoch/claim bookkeeping in `IdleCreditVault` and the stopEpoch ordering in `IdleCDOEpochVariant`.### Title
Missing decrement of `instantWithdrawClaimsByEpoch` on funded instant-withdraw claims inflates default recovery price and drains the recovery reserve - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
`instantWithdrawClaimsByEpoch[epoch]` is the per-epoch aggregate of outstanding instant-withdraw receipt basis, incremented in `requestInstantWithdraw` (line 372) and correctly decremented on the defaulted path in `_claimDefaultedInstantWithdrawRequest` (line 853). However, the normal funded-claim path `claimInstantWithdrawRequest` (lines 387-392) clears `instantWithdrawsRequests[_user]` but never decrements `instantWithdrawClaimsByEpoch[epochNumber]` — the exact analog of CVE-2023-52874: a value listed in the "must be cleared" set (`lastWithdrawRequest`, `withdrawsRequests`, `instantWithdrawsRequests`, `apr0Users`) is left stale. The stale aggregate then feeds `defaultPendingClaimBasis()` (line 647) and `_defaultPrefundedInstantReserve()` (line 719) during `finalizeDefaultRecovery`, inflating both the claim basis and the phantom "already-held" reserve, which overstates `defaultRecoveryPrice` / `defaultRecoveryReserve` relative to tokens actually held.

### Finding Description
Sequence in a running epoch (`epochNumber == E`) of a standard `IdleCDOEpochVariant` credit vault:

1. Users A and B call `requestInstantWithdraw` → `instantWithdrawClaimsByEpoch[E]` = amountA + amountB, `pendingInstantWithdraws` = same.
2. The CDO partially funds the queue via `collectInstantWithdrawFunds(amountA)` → `pendingInstantWithdraws` drops to amountB; the strategy now holds amountA of underlying.
3. User A calls `claimInstantWithdrawRequest` → receives amountA, `instantWithdrawsRequests[A]` zeroed, strategy balance back to ~0 — **but `instantWithdrawClaimsByEpoch[E]` still equals amountA + amountB** (stale, the "missing zeroed register").
4. Borrower defaults before `stopEpoch`; manager finalizes via `finalizeDefaultRecovery(_recoveredAmount, source)`:
   - `defaultPendingClaimBasis()` adds `instantWithdrawClaimsByEpoch[E]` = amountA + amountB, double-counting the already-paid A.
   - `_defaultPrefundedInstantReserve()` computes `instantBasis - pendingInstant` = (amountA + amountB) - amountB = amountA, treating already-paid-out tokens as held reserve.
   - `reserveAmount = _recoveredAmount + amountA(phantom) + defaultRecoveryReserve` and `recoveryPrice = reserveAmount / totalBasis` — the reserve ledger is credited amountA that does not exist.
5. `defaultInstantWithdrawsFinalized = pendingInstantWithdraws != 0` is true, so B claims via `_claimDefaultedInstantWithdrawRequest`, paying `amountB * defaultRecoveryPrice / 1e18` out of a reserve whose accounting is inflated by amountA.

The codebase's own invariant — every claim decrements the epoch claim aggregate, proven by line 853 — is violated on the funded path. The `requestWithdraw` loss-guard, `defaultRecoveryFinalized` checks, and `_transferFundedClaim` reserve guard do not cover this, because the funded claim legitimately runs *before* finalization.

### Impact Explanation
The phantom `prefundedReserve` is added to `defaultRecoveryReserve` (line 691) without any backing tokens. Because `_transferDefaultRecovery` blindly decrements that ledger and calls `safeTransfer`, the first claimants (an unprivileged tranche-token holder with a small instant receipt can claim first) withdraw genuine recovered funds at an inflated rate until the real balance is exhausted; all subsequent defaulted-epoch and post-default claims — including `postDefaultRequests` and active-LP recovery priced off the same basis — revert on transfer or receive nothing. This is direct theft of the recovery pool plus permanent freezing of honest users' unclaimed recovery. Quantified loss: up to the total already-claimed instant-withdraw amount of the defaulted epoch (amountA in the scenario), which is stolen from / frozen against the recovery reserve.

### Likelihood Explanation
Likelihood is moderate-to-high: it requires only (a) a partially funded instant-withdraw queue — an explicitly supported state per the comments at lines 636-640 and the `defaultInstantWithdrawsFinalized` flag at line 696 — (b) at least one funded user claiming in the same epoch before default (claims are ungated once funded), and (c) a borrower default, which is a designed-for flow (`finalizeDefaultRecovery` exists precisely for this). No privileged-role misbehavior is needed; the trigger is ordinary user activity around honest borrower/manager calls.

### Recommendation
In `claimInstantWithdrawRequest` (and the post-default funded tail of that function), decrement `instantWithdrawClaimsByEpoch` for the epoch(s) being cleared, mirroring line 853. Because the aggregate does not track per-epoch breakdown beyond `instantWithdrawsRequestsByEpoch`, the funded path should reduce `instantWithdrawClaimsByEpoch[requestEpoch]` for each per-epoch entry cleared (iterate `instantWithdrawsRequestsByEpoch[_user]` epochs or track the current request epoch), so `defaultPendingClaimBasis()` and `_defaultPrefundedInstantReserve()` only count still-outstanding receipts.

### Proof of Concept
A Foundry fork test should:

```solidity
// 1. Warp into a running epoch on an existing IdleCDOEpochVariant + IdleCreditVault pool.
// 2. As two KYC'd LPs:
cdoEpoch.requestInstantWithdraw(amountA, AAtranche); // user A
cdoEpoch.requestInstantWithdraw(amountB, AAtranche); // user B
uint256 epoch = strategy.epochNumber();
assertEq(strategy.instantWithdrawClaimsByEpoch(epoch), amountA + amountB);

// 3. Honest CDO funds only A's request:
vm.prank(address(cdoEpoch_helpers)); // via the CDO's funding path
strategy.collectInstantWithdrawFunds(amountA);

// 4. A claims — funded path:
cdoEpoch.claimInstantWithdrawRequest(); // as A
// BUG: epoch aggregate still counts A's paid receipt
assertEq(strategy.instantWithdrawClaimsByEpoch(epoch), amountA + amountB); // stale

// 5. Borrower defaults; manager finalizes recovery with recoveredAmount R:
cdoEpoch.finalizeDefault(...);
strategy.finalizeDefaultRecovery(R, recoverySource);

// 6. B claims defaulted instant receipt and receives amountB * defaultRecoveryPrice,
//    where the reserve ledger was inflated by the phantom amountA:
uint256 balBefore = underlying.balanceOf(B);
cdoEpoch.claimInstantWithdrawRequest(); // as B
uint256 paid = underlying.balanceOf(B) - balBefore;
// paid is drawn against a reserve that overstates held tokens by amountA;
// subsequent postDefault / defaulted withdraw claims revert on safeTransfer
// once the real balance is drained — demonstrate with a second claimant tx
// expected to revert or receive less than claimBasis * recoveryPrice.
```

I was unable to confirm the exact CDO entry-point names for `collectInstantWithdrawFunds` funding and `finalizeDefault` ordering (the final grep only returned match counts, not lines), but the stale-counter bug in `IdleCreditVault.sol` is self-contained: line 853 establishes that claims must decrement `instantWithdrawClaimsByEpoch`, and the funded claim path at lines 387-392 omits it while the finalization math at lines 644-649 and 716-723 directly consumes the stale value.