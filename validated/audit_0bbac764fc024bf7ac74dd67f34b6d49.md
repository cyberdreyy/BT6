### Title
Claimed instant-withdraw receipts are never removed from `instantWithdrawClaimsByEpoch`, inflating default-recovery basis and reserve — ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
Analogous to the Curve `withdraw` report, where the global `liquidity.total` was reduced but the tracked subset `liquidity.staked` was left stale, `IdleCreditVault.claimInstantWithdrawRequest` pays out an instant receipt but never clears `instantWithdrawsRequestsByEpoch[_user][epoch]` nor decrements `instantWithdrawClaimsByEpoch[epoch]`. Those per-epoch counters are read later by `defaultPendingClaimBasis` and `_defaultPrefundedInstantReserve` during `finalizeDefaultRecovery`, so already-paid claims are double-counted as outstanding basis *and* as phantom prefunded reserve, producing an inflated `defaultRecoveryPrice`/`defaultRecoveryReserve` that the strategy does not actually hold.

### Finding Description
`requestInstantWithdraw` records both the per-user aggregate and the per-epoch totals:

```solidity
instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
instantWithdrawClaimsByEpoch[currentEpoch] += _amount;
pendingInstantWithdraws += _amount;
``` [1](#0-0) 

When the claim is later paid, only the aggregate is cleared:

```solidity
uint256 amount = instantWithdrawsRequests[_user];
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);
``` [2](#0-1) 

There is no symmetric cleanup of `instantWithdrawsRequestsByEpoch[_user]` or `instantWithdrawClaimsByEpoch[epochNumber]` (compare `_claimDefaultedInstantWithdrawRequest`, which does clear both at lines 847–853). The stale counters feed default finalization:

- `defaultPendingClaimBasis` adds `instantWithdrawClaimsByEpoch[epochNumber]` to the claim basis whenever `pendingInstantWithdraws != 0` [3](#0-2) 
- `_defaultPrefundedInstantReserve` computes `instantBasis - pendingInstant` as "already-held" strategy funds [4](#0-3) 
- `finalizeDefaultRecovery` adds that prefunded amount to `reserveAmount` without pulling any tokens [5](#0-4) 

Scenario (running epoch, prefunded/partial instant mode): borrower or CDO partially funds the instant queue via `collectInstantWithdrawFunds`, so `pendingInstantWithdraws < instantWithdrawClaimsByEpoch[epoch]`; the code comments explicitly allow this state (lines 637–640). Attacker (unprivileged lender) claims their funded instant receipt `X` — tokens leave the strategy, but `instantWithdrawClaimsByEpoch` still includes `X`. The borrower then defaults in the same epoch and `finalizeDefaultRecovery` runs: `totalBasis` is inflated by `X`, and `prefundedReserve`/`defaultRecoveryReserve` are inflated by `X` with no backing tokens. `recoveryPrice` is therefore overstated while `defaultRecoveryReserve` records `actual + X`.

### Impact Explanation
The reserve is insolvent by exactly the claimed amount `X`. Other users' defaulted-epoch receipts and active-LP recovery claims (`_claimDefaultedWithdrawRequest`, `_claimDefaultedInstantWithdrawRequest`, tranche redemptions via `_transferDefaultRecovery`) are priced at the inflated `defaultRecoveryPrice` but backed by fewer tokens. The last `X` worth of claimants either get `_transferDefaultRecovery` underpayments or revert on insufficient balance — permanent freezing/theft of up to `X` of unclaimed recovery funds, attributable to a stale-subset accounting bug identical in class to the report.

### Likelihood Explanation
Requires: (a) an epoch where instant withdrawals are only partially funded (`pendingInstantWithdraws` non-zero but below the epoch's instant basis — a state the code explicitly supports), (b) a user claims the funded portion before epoch end, and (c) the borrower defaults in that same epoch before the residual instant queue is funded. Partial instant funding plus a mid-epoch default is an edge sequence, hence low likelihood, but any unprivileged instant-withdraw requester can trigger the stale state by simply claiming.

### Recommendation
In `claimInstantWithdrawRequest`, mirror the cleanup done in `_claimDefaultedInstantWithdrawRequest`: after computing `amount`, decrement `instantWithdrawsRequestsByEpoch[_user][epochNumber]` (or track the receipt's request epoch) and reduce `instantWithdrawClaimsByEpoch[requestEpoch]` by the claimed amount, so post-claim per-epoch totals reflect only outstanding receipts. Alternatively, stop relying on `instantWithdrawClaimsByEpoch` in default finalization and derive the unfunded instant basis directly from `pendingInstantWithdraws`.

### Proof of Concept
Foundry fork PoC sketch on `test/foundry/IdleCreditVault.t.sol` harness:

1. Deposit into AA tranche so the strategy holds underlying; `startEpoch`.
2. Attacker calls `requestInstantWithdraw(X)` — sets `instantWithdrawClaimsByEpoch[e] = X`.
3. CDO calls `collectInstantWithdrawFunds(X - r)` for small residual `r` — `pendingInstantWithdraws = r`, strategy now holds `X - r` for instant claims.
4. Attacker calls `claimInstantWithdrawRequest` — receives `X`; assert `instantWithdrawClaimsByEpoch[e]` still equals `X` (bug).
5. Borrower defaults; call `finalizeDefaultRecovery` with recovery funds.
6. Assert `defaultRecoveryReserve` exceeds `underlying.balanceOf(strategy)` by ≈ `X`, and `defaultRecoveryPrice * defaultPendingClaimBasis / 1e18 > reserve` — subsequent `_claimDefaultedInstantWithdrawRequest`/recovery claims for other users revert or underpay, demonstrating insolvency of the recorded reserve.

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L387-392)
```text
    uint256 amount = instantWithdrawsRequests[_user];
    // burn strategy tokens from user
    _burn(_user, amount);

    instantWithdrawsRequests[_user] = 0;
    _transferFundedClaim(_user, amount);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L644-649)
```text
  function defaultPendingClaimBasis() public view returns (uint256 basis) {
    basis = pendingWithdraws;
    if (pendingInstantWithdraws != 0) {
      basis += instantWithdrawClaimsByEpoch[epochNumber];
    }
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L685-692)
```text
    uint256 prefundedReserve = _defaultPrefundedInstantReserve();
    uint256 reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve;
    // Recovery can be above par if the recovered funds exceed the computed basis.
    uint256 recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;

    defaultRecoveryFinalized = true;
    defaultRecoveryReserve = reserveAmount;
    defaultRecoveryPrice = recoveryPrice;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L716-723)
```text
  function _defaultPrefundedInstantReserve() internal view returns (uint256 prefundedReserve) {
    uint256 pendingInstant = pendingInstantWithdraws;
    if (pendingInstant == 0) return prefundedReserve;
    uint256 instantBasis = instantWithdrawClaimsByEpoch[epochNumber];
    if (instantBasis > pendingInstant) {
      prefundedReserve = instantBasis - pendingInstant;
    }
  }
```
