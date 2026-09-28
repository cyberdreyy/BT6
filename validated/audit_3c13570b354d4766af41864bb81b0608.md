### Title
Post-default instant-withdraw requests re-enter the finalized default-epoch bucket and drain `defaultRecoveryReserve` - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
CVE-2026-64095 is a TOCTOU bug: a state flag (`request_sent`) and a counter (`bla.num_requests`) are mutated non-atomically by concurrent contexts, so a second context can decrement the counter after the state was already transitioned — the fix adds a third "stopped" state. The analog in `IdleCreditVault` is the same shape: `defaultRecoveryEpoch` is the "finalized" state, but `instantWithdrawsRequestsByEpoch[_user][epochNumber]` keeps accumulating new requests into that same bucket because a borrower default does not bump `epochNumber`. `_claimDefaultedInstantWithdrawRequest` then treats the *entire* bucket — including requests created after finalization — as a defaulted receipt and pays it out of the fixed `defaultRecoveryReserve`.

### Finding Description
The normal (non-instant) withdraw path already separates post-default requests into `postDefaultRequests[_user]`, claimed via `_claimPostDefaultWithdrawRequest` [1](#0-0) . The instant path has no such separation. `requestInstantWithdraw` unconditionally writes into the current epoch bucket:

```
instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
instantWithdrawClaimsByEpoch[currentEpoch] += _amount;
pendingInstantWithdraws += _amount;
``` [2](#0-1) 

`finalizeDefaultRecovery` snapshots `defaultRecoveryEpoch = epochNumber` and fixes `defaultRecoveryPrice`/`defaultRecoveryReserve` against the claim basis computed at that moment [3](#0-2) . Because `_handleBorrowerDefault` does not increment `epochNumber`, `currentEpoch` in a later `requestInstantWithdraw` is still `defaultRecoveryEpoch`. On claim, `_claimDefaultedInstantWithdrawRequest` reads the whole epoch bucket, pays `(claimBasis * defaultRecoveryPrice) / RECOVERY_FULL` from the reserve, and only saturates `pendingInstantWithdraws` against the unfunded remainder [4](#0-3) .

This is the same missing-atomicity defect: the "receipt basis counted at finalization" state and the "epoch bucket" counter are not transitioned atomically, and there is no third state marking the bucket closed to new writes.

### Impact Explanation
An attacker who holds tranche tokens (or already has an instant receipt) calls the CDO's instant-withdraw request after `finalizeDefaultRecovery`. The new request inflates `instantWithdrawsRequestsByEpoch[attacker][defaultRecoveryEpoch]` beyond the basis the reserve was priced against. `claimInstantWithdrawRequest` then clears the merged bucket and `_transferDefaultRecovery` pays haircut-priced underlying out of `defaultRecoveryReserve` — funds that were never collected from the borrower for this new receipt. Each unit requested post-default steals `defaultRecoveryPrice` units of recovery that belong to legitimate defaulted-epoch claimants, leaving later honest claims underfunded (insolvency of the recovery reserve; direct theft of unclaimed recovery).

### Likelihood Explanation
Requires: a pool in the defaulted+finalized phase, and the instant-withdraw request path remaining callable post-default (the request path only calls `_ensureDefaultRecoveryInitialized` and `_onlyIdleCDO`; whether `IdleCDOEpochVariant` reverts instant requests when `defaulted()` was not fully verified — if it does not gate, the attack is a two-transaction sequence any tranche holder can run). Loss is bounded by the attacker's tranche balance but is 1:1 dilution of the reserve per unit requested.

### Recommendation
Mirror the "stopped" state from the kernel fix: either (a) revert `requestInstantWithdraw` when `defaulted()`, or (b) route post-default instant requests to a separate `postDefaultInstantRequests` bucket priced at `defaultRecoveryPrice` at request time (like `postDefaultRequests`), and have `_claimDefaultedInstantWithdrawRequest` snapshot the pre-claim basis rather than re-reading the mutable epoch bucket. At minimum, gate `_claimDefaultedInstantWithdrawRequest` on a basis captured at finalization (`instantWithdrawClaimsByEpoch` is already a global per-epoch aggregate but is also mutated post-default, so it cannot serve as the frozen basis as-is).

### Proof of Concept
Foundry fork outline (pool with instant withdraws enabled):
1. Epoch N running. Honest users A, B hold instant withdraw receipts (basis 100). Attacker C holds tranche tokens, no receipt.
2. Borrower defaults; `finalizeDefaultRecovery` sets `defaultRecoveryEpoch = N`, `defaultRecoveryPrice = p < 1`, `defaultRecoveryReserve` sized for basis ~100.
3. C calls CDO `requestInstantWithdraw(x)` → strategy mints receipt and adds `x` to `instantWithdrawsRequestsByEpoch[C][N]` and `pendingInstantWithdraws`.
4. C calls `claimInstantWithdrawRequest` → `_claimDefaultedInstantWithdrawRequest` clears `x` and `_transferDefaultRecovery` pays `x*p` from the reserve.
5. Assert: reserve decreased by `x*p` while total funded basis never included `x`; honest A's subsequent claim reverts on insufficient reserve (or pays less than `claimBasis*p`).

Caveat: step 3 assumes `IdleCDOEpochVariant` does not gate instant-withdraw requests on `defaulted()`; if a guardian-level gate exists there, the primitive degrades to an accounting-desync rather than a direct theft, and the finding would need to be downgraded accordingly.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L356-375)
```text
  function requestInstantWithdraw(uint256 _amount, address _user) external {
    _onlyIdleCDO();
    _ensureDefaultRecoveryInitialized();
    // burn strategy tokens from cdo
    _burn(msg.sender, _amount);
  
    // mint equal amount of strategy tokens to the user as receipt, useful in case of default
    _mint(_user, _amount);

    // increase the instant withdraw requests for the user
    instantWithdrawsRequests[_user] += _amount;
    uint256 currentEpoch = epochNumber;
    // we record both per-user (old, kept for compatibility) and per-epoch so on
    // finalization we can distinguish "default-epoch pending instant receipts"
    // from old funded instant receipts.
    instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
    instantWithdrawClaimsByEpoch[currentEpoch] += _amount;
    // increase the total instant withdraw requests
    pendingInstantWithdraws += _amount;
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L679-700)
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
    // Bring active CDO NAV to the same recovery ratio. IdleCDOEpochVariant then calls
    // _forceUpdateAccounting so tranche prices/virtualPrice expose the crystallized loss.
    uint256 activeFinalNAV = (activeBasis * recoveryPrice) / RECOVERY_FULL;
    defaultBBNav = defaultBBNav * recoveryPrice / RECOVERY_FULL;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L760-767)
```text
  function _claimPostDefaultWithdrawRequest(address _user) internal returns (uint256 amount) {
    amount = postDefaultRequests[_user];
    if (amount == 0) return amount;
    postDefaultRequests[_user] = 0;
    // Post-default receipts are paid 1:1 because the haircut was applied when the request was made.
    _burn(_user, amount);
    _transferDefaultRecovery(_user, amount);
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L842-856)
```text
  function _claimDefaultedInstantWithdrawRequest(address _user) internal returns (uint256 claimBasis) {
    uint256 defaultEpoch = defaultRecoveryEpoch;
    claimBasis = instantWithdrawsRequestsByEpoch[_user][defaultEpoch];
    if (claimBasis == 0) return claimBasis;

    instantWithdrawsRequestsByEpoch[_user][defaultEpoch] = 0;
    instantWithdrawsRequests[_user] -= claimBasis;
    uint256 pending = pendingInstantWithdraws;
    // `pendingInstantWithdraws` is only the unfunded remainder. If this user's claim is larger,
    // the extra amount was already counted as prefunded reserve during default finalization.
    pendingInstantWithdraws = claimBasis >= pending ? 0 : pending - claimBasis;
    instantWithdrawClaimsByEpoch[defaultEpoch] -= claimBasis;
    _burn(_user, claimBasis);
    _transferDefaultRecovery(_user, (claimBasis * defaultRecoveryPrice) / RECOVERY_FULL);
  }
```
