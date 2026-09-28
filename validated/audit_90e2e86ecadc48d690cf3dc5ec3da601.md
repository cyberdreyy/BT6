### Title
`defaultPendingClaimBasis` over-reads only the current epoch's instant-withdraw table, inflating `defaultRecoveryPrice` and letting early claimants drain the recovery reserve - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
When a borrower default is finalized, `IdleCreditVault.finalizeDefaultRecovery` computes the recovery haircut over `totalBasis = activeBasis + defaultPendingClaimBasis()`. `defaultPendingClaimBasis` adds `instantWithdrawClaimsByEpoch[epochNumber]` — a per-epoch mapping indexed **only** by the current epoch — whenever the **aggregate** counter `pendingInstantWithdraws != 0`. Instant-withdraw receipts requested in a *previous* epoch but still unfunded inflate `pendingInstantWithdraws` while contributing nothing to `instantWithdrawClaimsByEpoch[epochNumber]`. Like the Graphite `CmapSubtable4NextCodepoint` over-read that walked past the subtable boundary, the accounting reads the wrong "row" of the epoch table: it checks the aggregate ("is there anything?") but prices using a different, narrower index ("how much is in *this* epoch?").

### Finding Description
- `pendingInstantWithdraws` is incremented in `requestInstantWithdraw` and decremented only in `collectInstantWithdrawFunds`/`_claimDefaultedInstantWithdrawRequest`; it is a global aggregate, not epoch-scoped [1](#0-0) .
- `instantWithdrawClaimsByEpoch[currentEpoch]` records the same request keyed by the epoch at request time [2](#0-1) .
- If an instant request stays unfunded across a `stopEpoch` (which bumps `epochNumber` in `deposit` [3](#0-2) ), its basis lives under the old epoch index.
- At default finalization, `defaultPendingClaimBasis` only adds `instantWithdrawClaimsByEpoch[epochNumber]` — the stale-index read misses prior-epoch instant claims, so `totalBasis` is understated and `recoveryPrice = reserveAmount * 1e18 / totalBasis` is overstated, possibly above 1e18 ("above par" is explicitly allowed) [4](#0-3) [5](#0-4) .
- Additionally `_defaultPrefundedInstantReserve` compares the current-epoch claim basis against the aggregate `pendingInstantWithdraws`; if old-epoch claims make `instantBasis <= pendingInstant`, legitimately prefunded current-epoch instant funds are not counted as reserve, further skewing the ratio [6](#0-5) .

### Impact Explanation
Every defaulted-epoch receipt holder (normal withdraw receipts via `_claimDefaultedWithdrawRequest`, APR0 receipts, instant receipts, active tranche NAV through `finalizeDefaultRecovery`'s mint/burn) is paid at the inflated `defaultRecoveryPrice`. Early claimants — an attacker who times `claimWithdrawRequest`/`claimInstantWithdrawRequest` immediately after `finalizeDefaultRecovery` — withdraw more than their pro-rata share, draining `defaultRecoveryReserve` so that later claimants' `_transferDefaultRecovery` either pays less than entitled or reverts on insufficient balance. This is direct theft of other users' recovery proceeds plus permanent freezing of the residual claims, quantified by `(inflatedPrice/truePrice - 1) * claimedBasis`.

### Likelihood Explanation
Requires: (1) an instant-withdraw request that remains unfunded through at least one `stopEpoch` (possible whenever `getInstantWithdrawFunds` liquidity is short — an unprivileged holder merely requests and waits), and (2) a subsequent borrower default, which is a normal protocol event driven by the honest borrower's failure to repay, not by any privileged attacker. No privileged misbehavior is needed; the sequencing is achievable by ordinary KYC'd lenders.

### Recommendation
Track an aggregate pending instant-claim basis (or iterate/maintain a separate "unfunded instant basis" counter) and use that — not `instantWithdrawClaimsByEpoch[epochNumber]` — in `defaultPendingClaimBasis` and `_defaultPrefundedInstantReserve`. Equivalently, fold all still-pending instant epochs into `instantWithdrawClaimsByEpoch[epochNumber]` when `stopEpoch` advances `epochNumber` with `pendingInstantWithdraws != 0`, so the single-index read matches the aggregate it gates on.

### Proof of Concept
Foundry fork sketch on an `IdleCDOEpochVariant` + `IdleCreditVault` deployment:

1. Epoch N running: attacker (KYC'd AA holder) calls `requestInstantWithdraw` with `X` underlying; ensure CDO has insufficient instant liquidity so `collectInstantWithdrawFunds` never funds it. `pendingInstantWithdraws = X`, `instantWithdrawClaimsByEpoch[N] = X`.
2. Honest manager calls `stopEpoch` → `epochNumber` becomes N+1; the request stays pending. New epoch starts; victim deposits and requests a normal withdraw `Y` in epoch N+1 (`withdrawsRequestsByEpoch[victim][N+1] = Y`, `pendingWithdraws = Y`).
3. Borrower defaults; owner triggers `finalizeDefaultRecovery(R, source)` with recovered `R < activeBasis + X + Y`.
4. `defaultPendingClaimBasis()` returns `Y + instantWithdrawClaimsByEpoch[N+1]` = `Y` (attacker's `X` is keyed under N and omitted). `defaultRecoveryPrice` is computed against `totalBasis` missing `X` → price inflated by factor `(active + X + Y)/(active + Y)`.
5. Attacker/victim call `claimWithdrawRequest`: victim's defaulted receipt pays `Y * inflatedPrice`, over-drawing `defaultRecoveryReserve`; subsequent claimants revert or are underpaid. Assert final `underlyingToken.balanceOf(strategy) < defaultRecoveryReserve` bookkeeping mismatch / last claimant claim < entitled amount.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L366-374)
```text
    instantWithdrawsRequests[_user] += _amount;
    uint256 currentEpoch = epochNumber;
    // we record both per-user (old, kept for compatibility) and per-epoch so on
    // finalization we can distinguish "default-epoch pending instant receipts"
    // from old funded instant receipts.
    instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
    instantWithdrawClaimsByEpoch[currentEpoch] += _amount;
    // increase the total instant withdraw requests
    pendingInstantWithdraws += _amount;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L607-611)
```text
    if (IIdleCDOEpochVariant(idleCDO).isEpochRunning()) {
      // deposit done on stopEpoch (before setting the var to false) so we reset the counter
      totEpochDeposits = 0;
      epochNumber += 1;
    } else {
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L644-648)
```text
  function defaultPendingClaimBasis() public view returns (uint256 basis) {
    basis = pendingWithdraws;
    if (pendingInstantWithdraws != 0) {
      basis += instantWithdrawClaimsByEpoch[epochNumber];
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L679-692)
```text
    uint256 pendingBasis = defaultPendingClaimBasis();
    uint256 totalBasis = activeBasis + pendingBasis;
    if (totalBasis == 0) revert NotAllowed();

    // Some recovery funds may already be in this strategy: partially prefunded instant requests
    // and borrower-send funds that failed at epoch start. Count both without pulling them again.
    uint256 prefundedReserve = _defaultPrefundedInstantReserve();
    uint256 reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve;
    // Recovery can be above par if the recovered funds exceed the computed basis.
    uint256 recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;

    defaultRecoveryFinalized = true;
    defaultRecoveryReserve = reserveAmount;
    defaultRecoveryPrice = recoveryPrice;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L716-722)
```text
  function _defaultPrefundedInstantReserve() internal view returns (uint256 prefundedReserve) {
    uint256 pendingInstant = pendingInstantWithdraws;
    if (pendingInstant == 0) return prefundedReserve;
    uint256 instantBasis = instantWithdrawClaimsByEpoch[epochNumber];
    if (instantBasis > pendingInstant) {
      prefundedReserve = instantBasis - pendingInstant;
    }
```
