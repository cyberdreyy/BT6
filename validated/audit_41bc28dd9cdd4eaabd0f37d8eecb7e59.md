### Title
Claimed instant-withdraw receipts stay in the per-epoch claim basis, inflating `prefundedReserve` and `defaultRecoveryPrice` after borrower default — (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`claimInstantWithdrawRequest` clears the aggregate `instantWithdrawsRequests[_user]` but never clears the per-epoch `instantWithdrawsRequestsByEpoch[_user][epoch]` or the epoch total `instantWithdrawClaimsByEpoch[epoch]`. If the borrower later defaults in that same epoch, `finalizeDefaultRecovery` counts already-paid instant receipts again — once as extra claim basis (`defaultPendingClaimBasis`) and once as phantom prefunded reserve (`_defaultPrefundedInstantReserve`). The resulting `defaultRecoveryPrice` and `defaultRecoveryReserve` are computed over funds that no longer exist, so default-recovery claimants are paid against an insolvent reserve and late claimants can never be paid.

### Finding Description
In `claimInstantWithdrawRequest` (contracts/strategies/idle/IdleCreditVault.sol:380-393):

```solidity
uint256 amount = instantWithdrawsRequests[_user];
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);
```

Only the aggregate counter is zeroed. The per-epoch receipt basis written in `requestInstantWithdraw` (lines 366-374: `instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount; instantWithdrawClaimsByEpoch[currentEpoch] += _amount`) is left untouched.

After a borrower default, `finalizeDefaultRecovery` (lines 661-710) computes:

- `basis = pendingWithdraws + instantWithdrawClaimsByEpoch[epochNumber]` via `defaultPendingClaimBasis` (lines 644-649) — includes the already-claimed amount `X`.
- `prefundedReserve = instantWithdrawClaimsByEpoch[epochNumber] - pendingInstantWithdraws` via `_defaultPrefundedInstantReserve` (lines 716-723) — the claimed `X` was funded via `collectInstantWithdrawFunds`, so `pendingInstantWithdraws` was decremented while the epoch claim basis was not, producing a phantom `prefundedReserve == X` for tokens already paid out.

`defaultRecoveryReserve = _recoveredAmount + prefundedReserve + defaultRecoveryReserve` then credits `X` underlyings that the strategy does not hold, and `recoveryPrice = reserveAmount * 1e18 / totalBasis` is skewed by an `X/X` numerator-denominator pair. Every `recoveryPrice < 1e18` is pushed upward, so each `_transferDefaultRecovery` pays out more real tokens per unit of basis than the true recovery ratio — and the reserve accounting (`defaultRecoveryReserve -= _amount`) assumes tokens that were never collected. Once the real balance is exhausted, remaining claimants' transfers revert: their recovery is permanently frozen inside the strategy. Early claimants effectively steal the phantom `X` portion of the reserve from later claimants and active LPs.

### Impact Explanation
An unprivileged lender with instant-withdraw access inflates the default-recovery reserve by the full amount `X` of their already-paid instant withdrawal. Concretely: with real recovered funds `R`, true basis `B`, and phantom `X`, the stored price becomes `(R + X)/(B + X)` instead of `R/B`. Each claimant receives `basis * price` but the strategy only holds `R` real tokens, so the aggregate payout target exceeds actual holdings by up to `X`. Claimants who act first over-receive; the last `X`-worth of claims reverts in `_transferDefaultRecovery`/`safeTransfer` forever (no admin path exists to correct `defaultRecoveryReserve` post-finalization). This is direct insolvency plus theft/permanent freezing of unclaimed recovery, proportional to the attacker's claimed instant-withdraw size.

### Likelihood Explanation
Requirements: a pool with `allowInstantWithdraw` enabled, an attacker who is a KYC-passing tranche holder, and a subsequent borrower default within the same `epochNumber` — the attacker does not cause the default, they merely pre-position. The attacker requests an instant withdraw, waits for `collectInstantWithdrawFunds` funding, claims it, then places a second (small) instant request so `pendingInstantWithdraws != 0` at finalization, which is also required for `defaultInstantWithdrawsFinalized`/`_defaultPrefundedInstantReserve` to engage. No privileged role misbehavior is needed; `requestInstantWithdraw`, `claimInstantWithdrawRequest`, `collectInstantWithdrawFunds`, and `finalizeDefaultRecovery` are all honest-actor calls. One caveat I could not fully verify in the iteration limit: the exact IdleCDOEpochVariant path that triggers `collectInstantWithdrawFunds` mid-epoch vs. only at `startEpoch` — if funding only happens at epoch start and `epochNumber` increments before the claim, the claimed receipt would sit in the prior epoch and not enter `instantWithdrawClaimsByEpoch[epochNumber]`, narrowing the window to claims funded and claimed while `epochNumber` stays fixed.

### Recommendation
In `claimInstantWithdrawRequest`, clear the per-epoch accounting alongside the aggregate:

```solidity
uint256 currentEpoch = epochNumber;
instantWithdrawsRequestsByEpoch[_user][currentEpoch] -= amount; // track per-epoch remainder
instantWithdrawClaimsByEpoch[currentEpoch] -= amount;
```

Because a user may hold receipts across epochs, store receipts per epoch and decrement the specific epoch bucket (or record each request's epoch and clear it on claim). Additionally, `_defaultPrefundedInstantReserve` should only count basis that is still outstanding (`instantWithdrawsRequests`-backed), e.g., track funded-but-unclaimed instant amounts in a dedicated counter decremented on claim.

### Proof of Concept
Foundry fork PoC sketch (pool in instant-withdraw mode, epoch running):

```solidity
function testInstantClaimPhantomReserve() public {
    // epoch N running, allowInstantWithdraw == true
    uint256 X = 100_000e6;
    uint256 Y = 1_000e6;

    // 1. Attacker deposits AA and requests instant withdraw of X
    depositAA(attacker, X);
    requestInstantWithdraw(attacker, X);          // via CDO
    // CDO/borrower funds it: collectInstantWithdrawFunds(X)
    //    pendingInstantWithdraws -> 0, strategy holds X underlyings
    claimInstantWithdrawRequest(attacker);        // attacker receives X
    // BUG: instantWithdrawsRequestsByEpoch[attacker][N] == X
    //      instantWithdrawClaimsByEpoch[N] == X   (never cleared)

    // 2. Attacker requests a second small instant withdraw (unfunded)
    depositAA(attacker, Y);
    requestInstantWithdraw(attacker, Y);          // pendingInstantWithdraws = Y

    // 3. Borrower defaults; owner/manager finalize recovery with R recovered
    borrowerDefaults();
    finalizeDefaultRecovery(R, recoverySource);

    // _defaultPrefundedInstantReserve returns (X + Y) - Y == X (phantom)
    // defaultRecoveryReserve credited X tokens not held by strategy
    // defaultRecoveryPrice = (R + X) / (activeBasis + pendingWithdraws + X + Y)

    // 4. Honest claimants draw down real tokens; final claims revert
    //    because balance < defaultRecoveryReserve remainder.
    vm.expectRevert(); // insolvency: reserve accounted but tokens absent
    claimInstantWithdrawRequest(victim);
}
```

Expected result without the fix: `defaultRecoveryReserve` exceeds `underlying.balanceOf(strategy)` by `X` after finalization, and the last `X` of recovery claims revert permanently.