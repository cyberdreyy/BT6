### Title
Post-default deposit/withdraw cycles drain `defaultRecoveryReserve` and permanently freeze defaulted-epoch recovery claims - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
Analogous to the tiffcp.c stack buffer overflow — where attacker-controlled input writes past a fixed bound and corrupts adjacent state — post-default withdraw requests write claims against the fixed-size `defaultRecoveryReserve` that was sized only for default-epoch claimants. Each post-default deposit→request→claim round-trip is economically neutral in underlying balance (deposits bring in exactly what claims pay out) but decrements `defaultRecoveryReserve` by the full payout, shrinking it below the aggregate owed to defaulted-epoch receipt holders. Once the reserve counter is exhausted, `_transferDefaultRecovery` underflows and reverts, permanently freezing the defaulted claimants' recovery even though the underlying tokens remain in the contract.

### Finding Description
After `finalizeDefaultRecovery` sets `defaultRecoveryReserve`, `defaultRecoveryPrice`, and `defaultRecoveryFinalized`, the vault intentionally keeps a post-default deposit/withdraw UX:

- `requestWithdraw` post-default path (line 247-258): burns `_amount` of CDO-held strategy tokens, mints a receipt to the user, and sets `postDefaultRequests[_user] = _amount`. The amount is haircut at the CDO layer (virtualPrice), so it is "correctly" paid 1:1.
- `_claimPostDefaultWithdrawRequest` (line 760-767) pays that amount via `_transferDefaultRecovery` (line 912-917), which does `defaultRecoveryReserve -= _amount; safeTransfer(_user, _amount)`.

The mismatch: the underlying backing the post-default claim comes from the user's fresh deposit into the strategy (`deposit()` transfers `_amount` underlying in and mints 1:1 strategy tokens to the CDO). That deposited underlying is **not** added to `defaultRecoveryReserve`, yet the claim consumes the reserve counter. Net effect per round-trip of size `X`:

- `underlyingToken.balanceOf(strategy)`: +X on deposit, −X on claim → unchanged
- `defaultRecoveryReserve`: −X → strictly decreases

Meanwhile defaulted-epoch claims (`_claimDefaultedWithdrawRequest`, `_claimDefaultedInstantWithdrawRequest`) are priced as `claimBasis * defaultRecoveryPrice / RECOVERY_FULL` and together are entitled to consume up to the original `reserveAmount`. After enough post-default churn, `defaultRecoveryReserve` reaches ~0 while defaulted claimants still hold unclaimed receipts. Their `_transferDefaultRecovery` then underflows and reverts forever — the recovery tokens are still in the contract (balance unchanged) but unreachable because the reserve counter was overrun by out-of-bounds post-default claims.

There is also no catch-up path: `reserveDefaultRecovery` reverts once `defaultRecoveryFinalized` is true (line 631), and `transferToken` is owner-only and cannot restore the accounting variable anyway.

### Impact Explanation
Permanent freezing of defaulted-epoch recovery funds. Any tranche holder (AA or BB receipt claimant) whose defaulted-epoch claim has not yet been executed loses the ability to claim their haircut recovery share — up to the entire `defaultRecoveryReserve`. The stolen/frozen value equals `min(postDefaultChurn, reserveAmount)`; an attacker can push the reserve to zero with repeated minimal-cost cycles (each cycle is balance-neutral for the attacker, costing only gas and the haircut applied at deposit time via the post-default virtualPrice — which is recycled back to them at claim since post-default receipts pay 1:1 on the already-haircut amount).

### Likelihood Explanation
- Attacker requirements: unprivileged EOA; only needs to deposit into the CDO after default finalization and cycle request/claim withdraws. The code explicitly preserves post-default request/claim UX (comment at line 252), so this is an intended-reachable state, gated only on whether `depositAA`/`depositBB` remain unpaused post-default in the epoch variant (`_emergencyShutdown` is a virtual hook; if it pauses the CDO, deposits revert and the attack window closes — this is the main reachability dependency).
- No privileged cooperation needed; owner/manager/borrower actions are not required after finalization.
- Loss is bounded by `defaultRecoveryReserve`, i.e., the total unclaimed recovery of defaulted-epoch claimants.

### Recommendation
Do not pay post-default claims out of the default recovery reserve counter. Either:
- Route `_claimPostDefaultWithdrawRequest` through `_transferFundedClaim`-style accounting (pay from non-reserved balance), since post-default deposits bring their own backing; or
- Increase `defaultRecoveryReserve` by the post-default request amount when the request is created, so the reserve counter tracks the fresh backing.

### Proof of Concept
Foundry fork sketch (against an epoch CDO + `IdleCreditVault` pair):

```solidity
// 1. Epoch N: lender L deposits, requests withdraw; borrower defaults;
//    owner calls _handleBorrowerDefault -> finalizeDefaultRecovery.
//    State: defaultRecoveryFinalized = true,
//           defaultRecoveryReserve = R (covers L's claimBasis * price).
// 2. Attacker A (any EOA):
//    for (uint i; i < N_TIMES; i++) {
//        cdo.depositAA(x);                 // strategy.deposit pulls x underlying, mints x stTokens to CDO
//        cdo.withdrawAA(xShares);          // -> strategy.requestWithdraw post-default path
//        cdo.claimWithdrawRequest();       // _claimPostDefaultWithdrawRequest -> _transferDefaultRecovery
//    }                                   // each iter: defaultRecoveryReserve -= x, balance net 0
// 3. After sum(x_i) >= R:
//    vm.prank(L); cdo.claimWithdrawRequest();
//    // _claimDefaultedWithdrawRequest -> _transferDefaultRecovery underflows -> revert
//    // L's recovery tokens remain in the strategy forever.
// Assert: underlyingToken.balanceOf(strategy) >= L_claimAmount (funds present, claim unreachable).
```

Preconditions to confirm on the fork: the chosen vault's `depositAA`/`depositBB` are not paused after `finalizeDefaultRecovery` (i.e., `_emergencyShutdown`/`paused()` state of `IdleCDOEpochVariant` post-default), and `defaultRecoveryPrice > 0` so L's claim is non-zero.