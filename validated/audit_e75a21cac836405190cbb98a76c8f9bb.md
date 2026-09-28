### Title
Instant-withdraw claims pay the full requested amount regardless of how much was actually funded — missing funded-amount bound in `claimInstantWithdrawRequest` - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The OIIO bug is a bounds check (`OIIO_DASSERT`) that compiles to a no-op in production, letting an RLE count exceed the scanline buffer. The closest analog in idle-tranches is the same bug class — a missing bound between a *requested* count and a *funded* buffer: `IdleCreditVault.claimInstantWithdrawRequest` pays out `instantWithdrawsRequests[_user]` in full, while funding is tracked separately in `pendingInstantWithdraws`/`collectInstantWithdrawFunds`. No check enforces `claimed ≤ funded`, so an underfunded instant receipt is paid out of underlying belonging to active LPs and other claimants.

### Finding Description
`requestInstantWithdraw` records the receipt under three ledgers: `instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch[_user][epoch]`/`instantWithdrawClaimsByEpoch[epoch]`, and `pendingInstantWithdraws` (IdleCreditVault.sol:356-375). Funding arrives later via `collectInstantWithdrawFunds`, which accepts any `_amount` and only decrements `pendingInstantWithdraws` (IdleCreditVault.sol:398-403). The consumer `claimInstantWithdrawRequest` reads `instantWithdrawsRequests[_user]` — the *requested* count — and calls `_transferFundedClaim(_user, amount)` without ever checking how much of that user's receipt was actually collected (IdleCreditVault.sol:380-393). `pendingInstantWithdraws` is only consulted at default finalization (`defaultPendingClaimBasis`, `_defaultPrefundedInstantReserve`, IdleCreditVault.sol:644-723), never in the non-default claim path. This mirrors the SGI decoder: the write bound (scanline width / funded reserve) is implicit and never enforced, while the loop bound (RLE count / requested amount) is attacker-controlled.

### Impact Explanation
If `startEpoch`/`getInstantWithdrawFunds` collects less underlying than the aggregate instant requests (partial prefunding by the borrower, or the CDO having less available balance than `instantWithdrawClaimsByEpoch`), the first user(s) to have the CDO call `claimInstantWithdrawRequest` receive their full request paid from the strategy's underlying balance — which also holds deposits awaiting `sendInterestAndDeposits` and funds reserved for other funded claims. This is direct theft of LP/claimant funds and breaks the one-receipt-one-funded-payout invariant; later claimants or the pool absorb the shortfall. Loss is bounded by `instantWithdrawsRequests[user] - fundedShare`, up to the strategy's underlying balance.

### Likelihood Explanation
The gap materializes whenever the CDO collects a partial instant-withdraw amount while leaving `pendingInstantWithdraws > 0` — e.g., prefunded-programmable mode where borrower funding at `onStartEpoch` is short, or thin CDO liquidity. `allowInstantWithdraw` only gates the feature flag (IdleCDOEpochVariant.sol:975-979); it does not gate claims on full funding. I could not fully trace `getInstantWithdrawFunds`/`collectInstantWithdrawFunds` call sites in `IdleCDOEpochVariant` within this pass, so whether the CDO can legitimately under-collect and still let claims proceed should be confirmed; if the CDO always collects the exact aggregate or reverts, the exploit path closes and this reduces to defense-in-depth.

### Recommendation
Track funded instant claims explicitly (e.g., a `fundedInstantWithdraws` counter incremented by `collectInstantWithdrawFunds`), and in `claimInstantWithdrawRequest` cap or clear per-user claims so `claimed ≤ funded` — or revert while `pendingInstantWithdraws != 0` for unfunded epochs. Alternatively, reduce `instantWithdrawsRequests[_user]` pro-rata when only part of the epoch's instant claims are collected, as the default path already does via `_defaultPrefundedInstantReserve`.

### Proof of Concept
```solidity
// Foundry fork PoC sketch — requires confirming that startEpoch collects
// less than instantWithdrawClaimsByEpoch while leaving pendingInstantWithdraws > 0.
function testInstantClaimOverPayout() external {
    // 1. KYC'd users deposit; manager starts an epoch with allowInstantWithdraw.
    // 2. User A calls cdoEpoch.requestInstantWithdraw(X) -> strategy mints X receipt tokens,
    //    instantWithdrawsRequests[A] = X, pendingInstantWithdraws = X.
    // 3. Epoch stops/starts; CDO calls collectInstantWithdrawFunds(Y) with Y < X
    //    (borrower prefunds only Y, or CDO balance < X). pendingInstantWithdraws = X - Y.
    // 4. CDO calls strategy.claimInstantWithdrawRequest(A):
    //    amount = instantWithdrawsRequests[A] = X (not Y), _burn(A, X),
    //    _transferFundedClaim(A, X) pulls X from the strategy's underlying balance.
    // 5. Assert: A received X underlying while only Y was funded ->
    //    X - Y came from deposits/funds backing other LPs' claims.
}
```