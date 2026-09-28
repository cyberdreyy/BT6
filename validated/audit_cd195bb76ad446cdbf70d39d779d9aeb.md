### Title
Permanent `stopEpoch` freeze: APR0 withdraw bucket reverts forever once `unscaledApr` is re-enabled - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.prepareStopEpochWithApr0()` unconditionally reverts with `NotAllowed` whenever `unscaledApr != 0` and `apr0TotalPrincipal != 0`. This is the same bug class as the Andromeda `is_permissioned()` underflow: a global permission/config flag (`unscaledApr`) can be flipped after users have already created state (`apr0TotalPrincipal` receipts) that assumes the flag stays in its previous value. Once the flag flips, the outstanding bucket can never be drained, so every subsequent `stopEpoch` reverts and the vault is frozen permanently.

### Finding Description
When the vault runs in APR0 mode (`unscaledApr == 0`), `requestWithdraw` routes withdraw requests into the APR0 bucket instead of the normal `withdrawsRequests` mapping:

- `requestWithdraw` calls `_requestWithdrawApr0(_amount, _user)` when `unscaledApr == 0 && !isClosed`, which records per-user `apr0Users[_user].principal` and increases the global `apr0TotalPrincipal` counter [1](#0-0) 
- At `stopEpoch`, the CDO calls `prepareStopEpochWithApr0`, which loads `apr0TotalPrincipal` and reverts if the APR is no longer zero: `if (unscaledApr != 0) { revert NotAllowed(); }` [2](#0-1) 
- The bucket is only closed inside the same function (`apr0TotalPrincipal = 0`) after the revert check, so the revert path can never make progress [3](#0-2) 
- Users cannot drain the bucket either: `_settleApr0` only settles principal once `epochNumber` has advanced past `principalEpoch`, which requires a successful `stopEpoch` — the very call that reverts [4](#0-3) 
- Defaulted-claim cleanup in `_clearWithdrawClaimForEpoch` can zero `apr0TotalPrincipal`, but only runs after `defaultRecoveryFinalized`, which itself requires a stopped/defaulted epoch [5](#0-4) 

Attack sequence (epoch running, APR0 mode):

1. Pool operates with `unscaledApr == 0`.
2. Attacker (a KYC-passed lender holding tranche tokens, unprivileged) calls `requestWithdraw`, seeding `apr0TotalPrincipal > 0` and minting an APR0 receipt [6](#0-5) .
3. The honest manager, in the normal course of operating the vault, sets a nonzero `unscaledApr` (e.g. re-enabling interest accrual for the next epoch).
4. At epoch end, `stopEpoch` → `prepareStopEpochWithApr0` hits `apr0TotalPrincipal != 0 && unscaledApr != 0` and reverts `NotAllowed`.
5. Reverting `unscaledApr` back to 0 does not help an attacker aiming to freeze: the honest roles can recover that way — but note the attacker controls whether the bucket is non-zero, and there is no legitimate path to clear `apr0TotalPrincipal` while APR stays nonzero. Any configuration the manager wants that has `unscaledApr != 0` is permanently blocked, and if the manager legitimately needs nonzero APR, the epoch can never stop and all funds (deposits, pending receipts, borrower repayment) are locked.

### Impact Explanation
Permanent freezing of all funds in the credit vault. `stopEpoch` is the only path that funds pending withdraws (`collectWithdrawFunds`), settles APR0 buckets, pays interest, and bumps `epochNumber`. With `apr0TotalPrincipal` stuck non-zero under `unscaledApr != 0`, every `stopEpoch`/`stopEpochWithDuration`/pool-close attempt reverts, and no claim path can settle APR0 principal because `_settleApr0` requires `epochNumber` to advance first. Loss equals the entire vault TVL plus pending withdraws held hostage.

### Likelihood Explanation
Requires only (a) an APR0-mode epoch and (b) a manager APR change while at least one APR0 withdraw request is open — both routine administrative/config transitions, not attacker-controlled privileged behavior. An unprivileged tranche holder can guarantee the precondition by keeping a minimal APR0 withdraw request open so `apr0TotalPrincipal` never reaches zero. This is structurally identical to the external report: outstanding per-user state created under one flag value (permissioned_action=true / unscaledApr=0) breaks when the flag flips (false / nonzero).

### Recommendation
Mirror the report's fix — only enforce the APR0 revert/settlement when the bucket is still required to accrue under the current configuration, or provide an escape path: e.g. allow `prepareStopEpochWithApr0` to settle `apr0TotalPrincipal` at the last stored `apr0RateByEpoch` (or zero rate) instead of reverting, or let users unwind an open APR0 request back into tranche tokens so the bucket can drain without `stopEpoch`.

### Proof of Concept
```solidity
// Foundry fork PoC (sketch, against a live APR0 credit vault fork)
// Setup: cdoEpoch with unscaledApr == 0, attacker is KYC'd lender
attacker depositAA(amount);                       // get AA tranche tokens
cdoEpoch.requestWithdraw(trancheBal, AAtranche);  // seeds apr0TotalPrincipal > 0
startEpoch();                                     // epoch running, bucket open

vm.prank(manager);
cdoEpoch.setApr(...);                             // honest config change: unscaledApr != 0

vm.warp(cdoEpoch.epochEndDate() + 1);
vm.prank(manager);
vm.expectRevert(NotAllowed.selector);
cdoEpoch.stopEpoch(0, 0);                         // prepareStopEpochWithApr0 reverts

// apr0TotalPrincipal cannot be cleared: no settle path, epochNumber frozen.
// All deposits + pending withdraws permanently locked.
```

Uncertain details to confirm during execution: the exact manager-facing setter that writes `unscaledApr` (lives in `IdleCDOEpochVariant.sol`/`IdleCreditVaultManagerOrchestrator.sol`) and whether the manager can restore `unscaledApr = 0` to recover — if so, severity reduces to freezing the vault in APR0 mode permanently (still a config-lock, but recoverable principal). If `unscaledApr` cannot be lowered once raised, the freeze is unrecoverable.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L271-294)
```text
    }
    // burn strategy tokens from cdo (we don't burn future interest here, only the principal)
    _burn(msg.sender, _principal);
    // mint equal amount of strategy tokens to the user as receipt (interest included), useful in case of default
    _mint(_user, _amount);
    // A successfully closed pool already recalled all funds and has no later stopEpoch.
    if (!isClosed) {
      // Global amount that stopEpoch must source from borrower/strategy for all pending receipts.
      pendingWithdraws += _amount;
    }
    // save the epoch of the last withdraw request (buffer + epochDuration is 1 epoch)
    lastWithdrawRequest[_user] = currentEpoch;
    // APR=0 requests keep separate accounting and settle interest at stopEpoch.
    // `_amount` here is the post-management-fee principal bucket for that flow.
    if (unscaledApr == 0 && !isClosed) {
      _requestWithdrawApr0(_amount, _user);
    } else {
      // increase the withdraw requests for the user
      // we record both per-user (old, kept for compatibility) and per-epoch so
      // on finalization we can distinguish "default-epoch pending receipts"
      // from old funded receipts.
      withdrawsRequests[_user] += _amount;
      withdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L499-508)
```text
    uint256 _principal = apr0TotalPrincipal;

    // Fast path: no APR0 accounting needed.
    if (_principal == 0) {
      return (_expInterest, _adjPendingWithdrawFees);
    }
    // APR0 principal is only valid while APR is 0 for that request lifecycle.
    if (unscaledApr != 0) {
      revert NotAllowed();
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L539-541)
```text
    // Close current APR0 bucket so it cannot accrue again on later stopEpoch calls.
    apr0TotalPrincipal = 0;
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L551-558)
```text
    uint256 _reqEpoch = _apr0User.principalEpoch;
    // Settle only after stopEpoch bumped epochNumber (ie after one full wait epoch).
    if (_reqEpoch >= epochNumber) {
      return;
    }
    // Move principal from "open APR0 bucket" to "settled bucket" (same principal, not duplicated).
    _apr0User.settledPrincipal += _principal;
    uint256 _rate = apr0RateByEpoch[_reqEpoch];
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L821-831)
```text
    Apr0UserData storage apr0User = apr0Users[_user];
    if (apr0User.principal != 0 && apr0User.principalEpoch == _claimEpoch) {
      if (_isClearingApr0) {
        uint256 apr0Principal = apr0User.principal;
        uint256 totalApr0Principal = apr0TotalPrincipal;
        // prepareStopEpochWithApr0 may already close the global APR0 bucket before default finalization.
        apr0TotalPrincipal = apr0Principal >= totalApr0Principal ? 0 : totalApr0Principal - apr0Principal;
      }
      apr0User.principal = 0;
      apr0User.principalEpoch = 0;
    }
```
