### Title
Already-claimed instant-withdraw receipts stay in `instantWithdrawsRequestsByEpoch`/`instantWithdrawClaimsByEpoch` and are paid a second time (and corrupt the unfunded-remainder counter) on default finalization — ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary

The Vim double-free pattern — data freed by one path, then freed again by another that still references it — maps directly to `IdleCreditVault`'s instant-withdraw accounting. `claimInstantWithdrawRequest` pays a user and zeroes `instantWithdrawsRequests[_user]`, but never clears `instantWithdrawsRequestsByEpoch[_user][epoch]` and never decrements `instantWithdrawClaimsByEpoch[epoch]`. If the borrower defaults in the same `epochNumber`, `finalizeDefaultRecovery` treats those stale, already-paid receipts as live defaulted claims: `_defaultPrefundedInstantReserve` counts already-paid underlying as held reserve, `defaultPendingClaimBasis` inflates the recovery basis, and `_claimDefaultedInstantWithdrawRequest` re-reads the stale per-epoch entry and pays the same basis a second time from `defaultRecoveryReserve`.

### Finding Description

Relevant code:

- `claimInstantWithdrawRequest` burns the user receipt tokens and zeroes only the aggregate `instantWithdrawsRequests[_user]`; the per-epoch basis `instantWithdrawsRequestsByEpoch[_user][currentEpoch]` and the per-epoch total `instantWithdrawClaimsByEpoch[currentEpoch]` written in `requestInstantWithdraw` are left intact forever. `pendingInstantWithdraws` is also not reduced on claim — only on `collectInstantWithdrawFunds`.
- `defaultPendingClaimBasis()` adds `instantWithdrawClaimsByEpoch[epochNumber]` to the defaulted claim basis whenever `pendingInstantWithdraws != 0`, including basis that was already paid out.
- `_defaultPrefundedInstantReserve()` computes `instantWithdrawClaimsByEpoch[epochNumber] - pendingInstantWithdraws` and adds it to `defaultRecoveryReserve`, but that "prefunded" underlying may have already been transferred to claimants and is no longer in the strategy.
- `_claimDefaultedInstantWithdrawRequest` reads `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` with no check that the receipt is still outstanding, decrements `instantWithdrawsRequests[_user]` (underflow → revert for a fully-claimed user), zeroes `pendingInstantWithdraws` when `claimBasis >= pending`, burns `claimBasis` receipt tokens, and pays `claimBasis * defaultRecoveryPrice` from the reserve.

Concrete sequence (epoch N, instant-withdraw mode enabled):

1. Users A and B each `requestInstantWithdraw` 100 in epoch N: `instantWithdrawClaimsByEpoch[N] = 200`, `pendingInstantWithdraws = 200`.
2. The honest CDO/manager collects only 100 (`collectInstantWithdrawFunds(100)` → `pendingInstantWithdraws = 100`, strategy balance 100) and A claims 100 at par. Stale state: `instantWithdrawsRequestsByEpoch[A][N] = 100`, `instantWithdrawClaimsByEpoch[N] = 200`.
3. Borrower defaults; honest CDO calls `finalizeDefaultRecovery` while `epochNumber == N`:
   - `pendingBasis` includes the phantom 100 (basis 200 instead of the real unpaid 100), so `defaultRecoveryPrice` is diluted for every claimant including active LPs.
   - `_defaultPrefundedInstantReserve` adds `200 - 100 = 100` to `defaultRecoveryReserve`, but that 100 was already sent to A and is not in the strategy balance — the reserve is overstated by exactly the paid amount.
4. Claims then misbehave:
   - A calls `claimInstantWithdrawRequest` with no new request → `instantWithdrawsRequests[A] -= 100` underflows and reverts, permanently blocking A's claim path (DoS).
   - If A posts a new instant request ≥ 100 post-default (nothing in `requestInstantWithdraw` blocks post-default requests), `_claimDefaultedInstantWithdrawRequest` pays the already-paid 100 basis again from `defaultRecoveryReserve` — a literal double payment of the same receipt.
   - Either way, `pendingInstantWithdraws` is zeroed/corrupted by subtracting already-claimed basis, so the accounting no longer reflects the true unfunded remainder owed to B.

### Impact Explanation

Direct theft and permanent freezing of recovery funds, quantified by the stale claimed amount `S`:

- `defaultRecoveryReserve` is credited `S` underlying that no longer exists in the contract. Total entitlement (`totalBasis * defaultRecoveryPrice = reserveAmount`) therefore exceeds real holdings by `S`; early claimants (including an attacker cycling a new instant request through `_claimDefaultedInstantWithdrawRequest`) drain real recovery funds and later claimants' `safeTransfer` reverts — permanent freezing of up to `S` of recovery payouts.
- `defaultPendingClaimBasis` inflation by `S` lowers `defaultRecoveryPrice` for all defaulted receipt holders and lowers `defaultBBNav`/the crystallized NAV for active AA/BB holders, transferring value to whoever claims first or twice.
- A fully-claimed user is permanently bricked on the instant-claim path (underflow revert) and `pendingInstantWithdraws` no longer measures the unfunded remainder.

### Likelihood Explanation

Medium: it needs (a) instant-withdraw mode enabled, (b) an epoch where instant requests are only partially collected before a borrower default, and (c) at least one instant claim executed in the same `epochNumber` as `finalizeDefaultRecovery` — i.e., before a deposit during a running epoch bumps `epochNumber`. None of these require privileged misbehavior; partial prefunding of the instant queue followed by default is an ordinary adverse scenario the protocol explicitly supports (`defaultInstantWithdrawsFinalized` exists precisely for the unfunded remainder). No existing guard clears or validates the stale per-epoch entries: `_ensureDefaultRecoveryInitialized`, `defaulted()` checks, and the `_transferFundedClaim` reserve guard do not touch this path.

### Recommendation

- In `claimInstantWithdrawRequest`, clear the per-epoch basis when paying: `instantWithdrawClaimsByEpoch[epochOfReceipt] -= amount` and `instantWithdrawsRequestsByEpoch[_user][epochOfReceipt] = 0` (store the receipt epoch, or iterate/clear all non-zero per-epoch entries for the user), and decrement `pendingInstantWithdraws` by the claimed amount or reconcile it against collected funds.
- Alternatively, make `_claimDefaultedInstantWithdrawRequest` skip basis whose receipt is already claimed (only claim `min(instantWithdrawsRequestsByEpoch[..], instantWithdrawsRequests[_user])` semantics are insufficient — the per-epoch entry must track *outstanding* basis), and make `_defaultPrefundedInstantReserve` derive prefunded reserve from the strategy's actual collectible balance rather than `instantBasis - pendingInstant`.
- Add a test: partially fund instant queue, claim one user fully, default and finalize in the same epoch, assert the reserve equals the strategy balance and remaining claimants are paid in full.

### Proof of Concept

Foundry fork PoC (schematic, in the style of `test/foundry/IdleCDOEpochQueue.t.sol`):

```solidity
// Setup: epoch variant CDO + IdleCreditVault strategy, instant withdraws enabled
// via cdoEpoch.setInstantWithdrawParams(delay, apr, true). Users A and B are KYC'd.

uint256 E = strategy.epochNumber();           // epoch N, running

// 1) A and B request 100 instant withdrawals each
_requestInstantWithUser(A, 100e6);
_requestInstantWithUser(B, 100e6);
assertEq(strategy.instantWithdrawClaimsByEpoch(E), 200e6);
assertEq(strategy.pendingInstantWithdraws(), 200e6);

// 2) Manager collects only enough for A (partial prefunding)
vm.prank(address(cdoEpoch));
strategy.collectInstantWithdrawFunds(100e6);  // pendingInstantWithdraws = 100

// 3) A claims fully; per-epoch data stays stale
vm.prank(address(cdoEpoch));
strategy.claimInstantWithdrawRequest(A);
assertEq(strategy.instantWithdrawsRequests(A), 0);
assertEq(strategy.instantWithdrawsRequestsByEpoch(A, E), 100e6); // STALE

// 4) Borrower defaults; CDO finalizes recovery while epochNumber == E
_finalizeDefault(/* recovered */ 50e6, recoverySource);

// BUG 1: basis inflated by A's already-paid 100
// defaultPendingClaimBasis() == pendingWithdraws + 200e6 (real unpaid: 100e6)

// BUG 2: reserve credited for underlying already sent to A
assertGt(strategy.defaultRecoveryReserve(),
         underlying.balanceOf(address(strategy)) /* + legitimately recovered amount */ - 50e6);

// BUG 3 (double free): A posts a new 100 instant request post-default, then
// _claimDefaultedInstantWithdrawRequest pays the stale 100 basis a second time
_requestInstantWithUser(A, 100e6);
uint256 balPre = underlying.balanceOf(A);
vm.prank(address(cdoEpoch));
strategy.claimInstantWithdrawRequest(A); // pays 100e6 * defaultRecoveryPrice for already-paid basis
assertGt(underlying.balanceOf(A), balPre);

// BUG 4: last claimant is frozen — reserve overstated by 100e6
vm.prank(address(cdoEpoch));
vm.expectRevert(); // safeTransfer fails: balance exhausted by phantom reserve
strategy.claimInstantWithdrawRequest(B);
```

Key assertion without any new request by A: `strategy.claimInstantWithdrawRequest(A)` reverts on the `instantWithdrawsRequests[A] -= 100e6` underflow at `contracts/strategies/idle/IdleCreditVault.sol:848`, permanently blocking that call path; and `defaultRecoveryReserve` exceeds real strategy holdings by exactly the previously claimed amount, so the final recovery claimant cannot be paid.