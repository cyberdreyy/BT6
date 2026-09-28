### Title
Instant-withdraw receipts keyed to a stale epoch index are excluded from default-recovery basis and later paid at par, draining the recovery reserve - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The external bug is a null-pointer dereference caused by trusting an attacker-controlled index that doesn't resolve to a valid entry. The analog in `IdleCreditVault` is an epoch-keyed index mismatch: instant-withdraw receipts are recorded under the epoch in which `requestInstantWithdraw` runs (`instantWithdrawsRequestsByEpoch[user][epochNumber]`), but default finalization and defaulted-claim logic always dereference `epochNumber`/`defaultRecoveryEpoch` — a pointer that has moved past the epoch where the receipt was actually recorded. The lookup returns zero (the "null" result), the receipt is silently dropped from the recovery basis, and the same receipt is still payable at par through the funded-claim path.

### Finding Description
`requestInstantWithdraw` stores the claim basis under the *current* `epochNumber` and bumps `instantWithdrawClaimsByEpoch[currentEpoch]` (lines 367-372). Two index dereferences assume the receipt lives under the *latest* epoch:

1. `defaultPendingClaimBasis()` (lines 644-649): when `pendingInstantWithdraws != 0`, it adds `instantWithdrawClaimsByEpoch[epochNumber]`. If `epochNumber` advanced via `deposit()` (line 610) after the request was made but before default finalization, this lookup returns 0, so the outstanding instant claims are excluded from `totalBasis` in `finalizeDefaultRecovery` (line 679).
2. `_claimDefaultedInstantWithdrawRequest` (lines 842-844): reads `instantWithdrawsRequestsByEpoch[user][defaultRecoveryEpoch]` — again 0 for a stale-epoch receipt — so the defaulted-claim path returns early without clearing or paying the claim.

The prefunded-reserve helper `_defaultPrefundedInstantReserve` (lines 716-723) has the same defect: with `instantBasis` read at the new epoch it returns 0, so already-held prefunded instant liquidity is not added to `defaultRecoveryReserve`.

Because `claimInstantWithdrawRequest` (lines 380-393) clears the default-epoch receipt first and then pays `instantWithdrawsRequests[user]` in full via `_transferFundedClaim`, a stale-epoch receipt holder claims the full par amount — no recovery haircut — and spends underlyings that bypass the reserve entirely (the `_transferFundedClaim` guard only requires `balance - reserve >= amount`, and the incorrectly small reserve under-protects it). Meanwhile `pendingInstantWithdraws` is never decremented for these claims, so it stays permanently non-zero.

### Impact Explanation
Two coupled effects:

- **Recovery over-distribution / theft of unclaimed yield:** `totalBasis` in `finalizeDefaultRecovery` excludes stale-epoch instant claims, so `recoveryPrice = reserve * 1e18 / totalBasis` is inflated. Every defaulted normal withdraw receipt claimed via `_claimDefaultedWithdrawRequest` receives `claimBasis * inflatedPrice`, overpaying early claimants and draining `defaultRecoveryReserve` before later claimants can claim — a direct wealth transfer between unprivileged users.
- **Par payout bypassing the haircut:** the stale-epoch instant claimant is then paid 100% via `_transferFundedClaim` while every other receipt holder took a haircut, further depleting strategy-held underlyings that back active tranche NAV.

Broken invariants: one-receipt-one-haircut-payout and isolation of the recovery reserve. Loss equals roughly `(excluded instant basis / totalBasis)` share of the recovery fund, plus the uncut par payout.

### Likelihood Explanation
Requires: an instant-withdraw request in epoch N that remains unfunded (`pendingInstantWithdraws > 0`, i.e., CDO lacked liquidity at request time), `stopEpoch`/`deposit` advancing `epochNumber` to N+1 without the receipt being claimed, then borrower default and `finalizeDefaultRecovery` in epoch N+1. All steps are reachable with unprivileged sequencing (attacker is the instant-withdraw requester and a claimant) around honest manager/borrower epoch calls. Existing guards don't catch it: no code validates that `instantWithdrawClaimsByEpoch` entries exist under older epochs, and `defaultInstantWithdrawsFinalized` is set purely from `pendingInstantWithdraws != 0`.

### Recommendation
Track instant claims by request epoch and aggregate all outstanding epochs at finalization — e.g., maintain a `totalInstantClaimsBasis` counter incremented in `requestInstantWithdraw` and decremented on claim, and use it in `defaultPendingClaimBasis`/`_defaultPrefundedInstantReserve` instead of `instantWithdrawClaimsByEpoch[epochNumber]`. In `_claimDefaultedInstantWithdrawRequest`, clear all per-epoch entries for the user (or iterate epochs with nonzero entries) rather than only `defaultRecoveryEpoch`. Alternatively, record each user's request epoch (like `lastWithdrawRequest`) so the correct index is dereferenced.

### Proof of Concept
Foundry fork test sketch (vault in non-APR0 mode):

```solidity
// 1. Epoch N running. Alice (KYC'd LP) calls requestInstantWithdraw(A) via CDO.
//    CDO has insufficient liquidity -> pendingInstantWithdraws = A,
//    instantWithdrawsRequestsByEpoch[alice][N] = A.
// 2. Epoch ends: manager stopEpoch -> CDO deposit() -> epochNumber = N+1.
//    Alice does not claim (still unfunded or griefed liquidity).
// 3. Bob has a pending normal withdraw request (basis B) recorded in epoch N+1.
// 4. Borrower defaults; owner finalizeDefaultRecovery(recovered R).
//    totalBasis = activeBasis + B  (A missing because
//    instantWithdrawClaimsByEpoch[N+1] == 0).
//    recoveryPrice is inflated by factor (activeBasis + B + A) / (activeBasis + B).
// 5. Bob calls claimWithdrawRequest -> gets B * inflatedPrice > fair share.
// 6. Alice calls claimInstantWithdrawRequest ->
//    _claimDefaultedInstantWithdrawRequest returns 0 (stale index),
//    then instantWithdrawsRequests[alice] = A is paid at PAR via
//    _transferFundedClaim, spending non-reserve underlyings.
// Assert: reserve depleted below what remaining defaulted claimants need;
//         Alice received 100% while others took the haircut.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L367-372)
```text
    uint256 currentEpoch = epochNumber;
    // we record both per-user (old, kept for compatibility) and per-epoch so on
    // finalization we can distinguish "default-epoch pending instant receipts"
    // from old funded instant receipts.
    instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
    instantWithdrawClaimsByEpoch[currentEpoch] += _amount;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L380-392)
```text
  function claimInstantWithdrawRequest(address _user) external {
    _onlyIdleCDO();
    if (defaultRecoveryFinalized && defaultInstantWithdrawsFinalized) {
      // Clear the defaulted-epoch instant receipt first, then continue so the same call can
      // also pay any older instant receipt that was already funded before default finalization.
      _claimDefaultedInstantWithdrawRequest(_user);
    }
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L679-696)
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
    defaultRecoveryEpoch = epochNumber;
    // A non-zero pending instant bucket means current-epoch instant receipts were not fully funded
    // and must be paid through the same recovery ratio as normal pending receipts.
    defaultInstantWithdrawsFinalized = pendingInstantWithdraws != 0;
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L842-848)
```text
  function _claimDefaultedInstantWithdrawRequest(address _user) internal returns (uint256 claimBasis) {
    uint256 defaultEpoch = defaultRecoveryEpoch;
    claimBasis = instantWithdrawsRequestsByEpoch[_user][defaultEpoch];
    if (claimBasis == 0) return claimBasis;

    instantWithdrawsRequestsByEpoch[_user][defaultEpoch] = 0;
    instantWithdrawsRequests[_user] -= claimBasis;
```
