### Title
Stale per-epoch instant-withdraw receipts are reused after the funded claim, double-counting claim basis and phantom reserve at default finalization - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`claimInstantWithdrawRequest` pays the funded instant-withdraw receipt but only clears the aggregate `instantWithdrawsRequests[_user]`; it never clears `instantWithdrawsRequestsByEpoch[_user][epoch]` or `instantWithdrawClaimsByEpoch[epoch]`. If a default is later finalized while `epochNumber` is still that same epoch, `defaultPendingClaimBasis`/`_defaultPrefundedInstantReserve` treat the already-paid ("freed") receipt as live claim basis and count the already-transferred tokens as prefunded reserve — a use-after-free-style reuse of a stale receipt record that corrupts `defaultRecoveryPrice` and makes the reserve insolvent.

### Finding Description
In `IdleCreditVault.requestInstantWithdraw`, the vault records both the aggregate receipt and per-epoch records:

- `instantWithdrawsRequests[_user] += _amount` (line 366)
- `instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount` and `instantWithdrawClaimsByEpoch[currentEpoch] += _amount` (lines 371-372)

In the normal (funded) claim path, only the aggregate is freed:

```
uint256 amount = instantWithdrawsRequests[_user];
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);
```

`instantWithdrawsRequestsByEpoch[_user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]` are left intact (lines 387-392). They are only cleared inside `_claimDefaultedInstantWithdrawRequest` (lines 844-853), which runs exclusively during the default-recovery path.

At default finalization these stale records are consumed as if still live:

- `defaultPendingClaimBasis` adds `instantWithdrawClaimsByEpoch[epochNumber]` whenever `pendingInstantWithdraws != 0` (lines 645-648) — including basis already paid out.
- `_defaultPrefundedInstantReserve` computes `instantBasis - pendingInstant` (lines 716-723) and `finalizeDefaultRecovery` adds it to `reserveAmount` (line 686) as tokens "already held" by the strategy — but those tokens were already transferred to the claimant in step `_transferFundedClaim`.

The sequence requires only unprivileged actions plus an ordinary borrower default:

1. During epoch N (running/buffer phase, `allowInstantWithdraw` enabled), user A calls `requestInstantWithdraw(X)` and user B calls `requestInstantWithdraw(Y)`. `instantWithdrawClaimsByEpoch[N] = X+Y`, `pendingInstantWithdraws = X+Y`.
2. At `startEpoch`, the CDO has cash for only X, so `collectInstantWithdrawFunds(X)` pulls X into the strategy and `pendingInstantWithdraws = Y` (partial prefunding is explicitly contemplated by the code comments at lines 636-640).
3. A calls `claimInstantWithdrawRequest`, receives X at par. Aggregate cleared; per-epoch records for A and the epoch totals remain stale.
4. The borrower fails to repay at `stopEpoch` and `_handleBorrowerDefault`/`finalizeDefaultRecovery` runs while `epochNumber == N`.

Finalization then computes `basis = pendingWithdraws + (X + Y)` and `reserveAmount = recovered + X(phantom) + defaultRecoveryReserve`. `defaultRecoveryPrice = reserve * 1e18 / basis` is priced against X tokens that no longer exist in the contract.

### Impact Explanation
The reserve is overstated by exactly X — the already-claimed amount. Consequences:

- B's defaulted claim pays `Y * price / 1e18` from a pool that is short X real tokens. Whether B's own transfer succeeds, the residual `defaultRecoveryReserve` available to post-default requesters (`_claimPostDefaultWithdrawRequest`, `_claimDefaultedWithdrawRequest`, `DefaultDistributor.claim`) is X less than accounted. The last claimants' transfers revert on insufficient balance — permanent freezing/insolvency of up to X underlying, directly quantifiable as the stale prefunded amount.
- All claimants' recovery price is computed on a phantom reserve, so the haircut distribution is corrupt: real recovery is diluted by double-counted basis while the nominal price assumes tokens that were already paid out — an effective overpayment/dilution of the loss waterfall.
- Additionally, if A ever calls `claimInstantWithdrawRequest` again after finalization, `_claimDefaultedInstantWithdrawRequest` hits `instantWithdrawsRequests[A] -= X` on a zeroed aggregate (line 848) and underflow-reverts, permanently bricking that function for A.

Loss scales with the instant-withdraw liquidity at the pool: any partially prefunded instant queue followed by a same-epoch default leaks/denies up to the full prefunded amount. This is a solvency invariant break ("one receipt one payout") caused by reusing a receipt record after it was freed — the direct analog of the use-after-free bug class in CVE-2021-4102.

### Likelihood Explanation
Likelihood is moderate. It requires (a) `allowInstantWithdraw` enabled, (b) an epoch where instant requests exceed the CDO's liquid cash at `startEpoch` so only a partial prefund occurs, and (c) a borrower default finalized in the same epoch — a realistic scenario exactly when instant liquidity is scarce (borrower stress). No privileged misbehavior is needed: the attacker is an ordinary KYC'd lender who simply claims a funded instant withdrawal. No existing guard stops it: `_ensureDefaultRecoveryInitialized`, `_onlyIdleCDO`, epoch gating, and the `pendingInstantWithdraws != 0` checks all pass; the funded-claim path simply never frees the per-epoch records.

### Recommendation
In the funded path of `claimInstantWithdrawRequest`, clear the per-epoch records the same way `_claimDefaultedInstantWithdrawRequest` does:

```
instantWithdrawsRequestsByEpoch[_user][epoch] = 0; // per request epoch
instantWithdrawClaimsByEpoch[epoch] -= amount;
instantWithdrawsRequests[_user] = 0;
```

Since a user may have receipts in multiple epochs, iterate or track the request epoch(s) so each per-epoch entry is decremented by the amount actually paid, keeping `instantWithdrawClaimsByEpoch` equal to the sum of outstanding (unclaimed) per-epoch basis. Alternatively, recompute prefunded reserve at finalization from actual live receipts rather than cumulative epoch totals.

### Proof of Concept
Foundry fork PoC (against the existing `IdleCreditVault.t.sol` harness, using `deal` for underlying):

```solidity
function testStaleInstantReceiptInflatesDefaultReserve() external {
    _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, cdoEpoch.managementFee());
    // enable instant withdraws, deposit AA so pool has liquidity, start epoch N
    uint256 amount = 10_000 * ONE_SCALE;
    idleCDO.depositAA(amount);
    _transferBurnedTrancheTokens(address(this), true); // lender user1 gets tranches

    _startEpochAndCheckPrices(0); // epoch N running, funds sent to borrower

    address user2 = makeAddr("user2");
    // give user2 a position similarly (deposit + requestInstantWithdraw)

    // 1) user1 requests instant withdraw X, user2 requests Y (X+Y > CDO cash)
    //    so startEpoch/collectInstantWithdrawFunds only prefunds X
    // 2) user1 claims funded X: instantWithdrawsRequests[user1] == 0
    //    but instantWithdrawsRequestsByEpoch[user1][N] == X  (STALE)
    //    and instantWithdrawClaimsByEpoch[N] == X + Y        (STALE)

    assertEq(creditVault.instantWithdrawsRequestsByEpoch(user1, N), X); // freed record still live

    // 3) borrower defaults in same epoch N; finalize
    //    defaultPendingClaimBasis() == pendingWithdraws + X + Y  (X double counted)
    //    _defaultPrefundedInstantReserve() == X (phantom, tokens already paid to user1)
    //    defaultRecoveryPrice priced against X non-existent tokens

    // 4) user2 claims haircut; post-default/other claimants then find the
    //    reserve short by X -> their claims revert (insolvency / permanent freeze)
}
```

Key invariant assertion: after step 2, `instantWithdrawClaimsByEpoch[N]` should equal Y (only outstanding basis); the bug leaves it at X+Y, directly producing the X-token reserve shortfall measurable via `underlying.balanceOf(address(creditVault))` vs `defaultRecoveryReserve` after finalization.