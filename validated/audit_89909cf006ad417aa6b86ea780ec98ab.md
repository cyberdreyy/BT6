### Title
Stale instant-withdraw per-epoch receipts are never deregistered on claim, inflating default-recovery basis and freezing recovery claims - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`claimInstantWithdrawRequest` clears only the aggregate `instantWithdrawsRequests[_user]` counter when paying a funded instant withdrawal. It never removes the per-epoch entries `instantWithdrawsRequestsByEpoch[_user][epoch]` or `instantWithdrawClaimsByEpoch[epoch]`. Those stale ledger entries are later read verbatim by `defaultPendingClaimBasis`, `_defaultPrefundedInstantReserve`, and `_claimDefaultedInstantWithdrawRequest` if the same epoch ends in default — the exact "use freed resource via stale pointer" shape of CVE-2021-47525.

### Finding Description
In `contracts/strategies/idle/IdleCreditVault.sol:380-393`, a funded instant-withdraw claim does:

```solidity
uint256 amount = instantWithdrawsRequests[_user];
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);
```

But `requestInstantWithdraw` (lines 366-374) recorded the request in three places: `instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch[_user][currentEpoch]`, and `instantWithdrawClaimsByEpoch[currentEpoch]`. The claim path clears only the first — the per-epoch records survive after the receipt is burned and paid.

Attack sequence, all within one epoch with instant withdrawals enabled:

1. Attacker (any KYC-passing lender) calls `requestInstantWithdraw(A)`. `instantWithdrawsRequestsByEpoch[attacker][N] = A`, `instantWithdrawClaimsByEpoch[N] = A`, `pendingInstantWithdraws = A`.
2. Epoch stop funds the request: `collectInstantWithdrawFunds(A)` sets `pendingInstantWithdraws = 0`. Attacker calls `claimInstantWithdrawRequest` and receives `A`. The per-epoch entries still hold `A`.
3. Same epoch N (or a subsequent request epoch — the vault writes to `epochNumber` with no guard), attacker calls `requestInstantWithdraw(B)`. Now `instantWithdrawsRequestsByEpoch[attacker][N] = A + B`, `instantWithdrawClaimsByEpoch[N] = A + B`, `pendingInstantWithdraws = B`.
4. Borrower defaults. `finalizeDefaultRecovery` computes `defaultPendingClaimBasis() = pendingWithdraws + instantWithdrawClaimsByEpoch[N]` because `pendingInstantWithdraws != 0` — the basis is inflated by the already-claimed `A` (lines 644-649). `_defaultPrefundedInstantReserve()` adds `instantBasis - pendingInstant = A` to `reserveAmount` as if `A` were still sitting in the strategy backing those claims — but it was already paid out (lines 716-723).
5. `defaultRecoveryPrice = reserveAmount / totalBasis` is therefore depressed for every claimant, while `defaultRecoveryReserve` does not actually contain the phantom `A`.
6. Attacker's own claim at `_claimDefaultedInstantWithdrawRequest` (lines 842-856) computes `claimBasis = A + B` and executes `instantWithdrawsRequests[attacker] -= claimBasis`, which underflows (`instantWithdrawsRequests` holds only `B`), permanently reverting. The phantom `A` is never claimable by anyone, yet it remains baked into the divisor — the dilution is locked in and the trailing defaulted/loss-adjusted claimants hit `defaultRecoveryReserve -= _amount` underflows in `_transferDefaultRecovery` (line 915), permanently freezing their recovery.

The same stale-entry leak also poisons `pendingInstantWithdraws = claimBasis >= pending ? 0 : pending - claimBasis` (line 852): had the underflow not reverted first, the bucket accounting would have been driven to zero while real requests remain.

### Impact Explanation
Direct, quantified dilution and permanent freezing of default-recovery funds. Every honest defaulted claimant's `defaultRecoveryPrice` is reduced by the attacker's phantom `A` share; the shortfall materializes as reverts (permanent freeze) in `_transferDefaultRecovery` for the last claimants, and the attacker's own claim is bricked. Loss magnitude ≈ `recovered * A / totalBasis`, bounded only by the attacker's instant-withdraw size before default. Broken invariant: one receipt, one payout — a receipt that was burned and paid still counts as outstanding claim basis.

### Likelihood Explanation
Requires no privileged cooperation: an unprivileged lender makes an instant withdrawal, claims it, re-requests in the same epoch, and the borrower later defaults in that epoch (defaults are a normal protocol outcome). Instant withdrawals are a supported mode (`setInstantWithdrawParams`), and `requestInstantWithdraw` has no same-epoch or prior-receipt guard in the vault. Caveat: I did not fully verify `IdleCDOEpochVariant`'s instant-withdraw timing gates (`instantWithdrawDelay`) — if the CDO forbids re-requesting within the same epoch after a claim, the attacker's second request lands in a later epoch and the inflation does not apply; however, the stale-entry leak itself is unconditional and also corrupts `instantWithdrawClaimsByEpoch` accounting whenever a defaulted epoch coincides with a previously-claimed-then-re-requested user.

### Recommendation
In `claimInstantWithdrawRequest`, mirror the normal-claim cleanup: after computing `amount`, zero `instantWithdrawsRequestsByEpoch[_user][epochNumber]` (or track and clear each user's contributing epochs) and decrement `instantWithdrawClaimsByEpoch[epoch]` by the claimed amount, the same way `_claimDefaultedInstantWithdrawRequest` already decrements it on the default path. Alternatively, settle instant receipts through `_clearWithdrawClaimForEpoch`-style per-epoch clearing so paid receipts cannot be double-counted at finalization.

### Proof of Concept
Foundry fork skeleton (extend `test/foundry/IdleCreditVault.t.sol` harness):

```solidity
function testStaleInstantReceiptInflatesDefaultBasis() external {
    uint256 A = 50_000 * ONE_SCALE;
    uint256 B = 10_000 * ONE_SCALE;
    address attacker = makeAddr('attacker');
    uint256 delay = cdoEpoch.instantWithdrawDelay();

    vm.prank(manager);
    cdoEpoch.setInstantWithdrawParams(delay, 1e18, false);

    _depositWithUser(attacker, A + B, true);

    // epoch N running: request instant withdraw of A, wait delay, CDO funds it
    vm.prank(attacker);
    cdoEpoch.requestInstantWithdraw(A);
    vm.warp(block.timestamp + delay + 1);
    // manager/borrower funds instant queue -> collectInstantWithdrawFunds(A)
    // attacker claims -> byEpoch entries stay stale
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest();

    // same epoch: second request B
    vm.prank(attacker);
    cdoEpoch.requestInstantWithdraw(B);

    // borrower defaults in epoch N; manager finalizes with partial recovery
    _checkDefault();
    uint256 recovered = /* partial */;
    deal(defaultUnderlying, manager, recovered);
    vm.startPrank(manager);
    IERC20Detailed(defaultUnderlying).approve(address(strategy), recovered);
    cdoEpoch.finalizeDefault(recovered, manager);
    vm.stopPrank();

    IdleCreditVault v = IdleCreditVault(address(strategy));
    // recovery price reflects basis including already-paid A
    // attacker claim reverts on underflow; reserve insolvent for tail claimants
    vm.expectRevert();
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest();
}
```

If, after checking `IdleCDOEpochVariant`'s gating, the same-epoch re-request proves impossible, the finding degrades to the dilution-only variant or should be rejected — I was unable to confirm that gate within the available context.