### Title
Stale instant-withdraw epoch ledgers inflate default recovery basis and phantom prefunded reserve, overpaying early claimants and draining the recovery reserve - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The kernel bug frees only the surviving `nr_hw_queues` tags after a shrink, leaking the removed ones. The analog in `IdleCreditVault` is that the funded/claimed instant-withdraw path shrinks the "live" counters (`instantWithdrawsRequests[_user]`, `pendingInstantWithdraws` via `collectInstantWithdrawFunds`) but never frees the per-epoch ledgers `instantWithdrawsRequestsByEpoch` / `instantWithdrawClaimsByEpoch`. Those stale entries are later counted a second time in `defaultPendingClaimBasis` and `_defaultPrefundedInstantReserve` during `finalizeDefaultRecovery`, inflating both the claim basis and the presumed held reserve.

### Finding Description
In `requestInstantWithdraw` the vault records three pieces of state per current epoch: `instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch[_user][epoch]`, and `instantWithdrawClaimsByEpoch[epoch]` (all incremented), plus the global `pendingInstantWithdraws` [1](#0-0) . When the CDO funds instant requests, `collectInstantWithdrawFunds` decrements only `pendingInstantWithdraws` [2](#0-1) . When the user then claims, `claimInstantWithdrawRequest` burns `instantWithdrawsRequests[_user]`, pays funded underlying, and zeroes the aggregate counter — but leaves `instantWithdrawsRequestsByEpoch` and `instantWithdrawClaimsByEpoch` untouched [3](#0-2) . Only the defaulted-epoch claim path `_claimDefaultedInstantWithdrawRequest` clears them [4](#0-3) .

At default finalization, `defaultPendingClaimBasis` adds `instantWithdrawClaimsByEpoch[epochNumber]` to the basis whenever `pendingInstantWithdraws != 0` [5](#0-4) , and `_defaultPrefundedInstantReserve` treats `instantBasis - pendingInstant` as cash already held by the strategy [6](#0-5) . The already-claimed amount therefore counts as (a) extra claim basis and (b) phantom "prefunded" reserve that was actually paid out to the claimant and is no longer held.

### Impact Explanation
Concrete sequence (running epoch, borrower honest until default): users A and B each request 100 instant withdrawals in epoch N (`instantWithdrawClaimsByEpoch[N] = 200`, `pendingInstantWithdraws = 200`). The honest CDO/manager funds only 100 (`collectInstantWithdrawFunds(100)` → `pendingInstantWithdraws = 100`) and A claims 100 in the same epoch via `claimInstantWithdrawRequest` — the by-epoch ledgers still say 200. The borrower then defaults and `finalizeDefaultRecovery` runs in the same epoch: `defaultPendingClaimBasis` = `pendingWithdraws + 200` instead of 100, and `_defaultPrefundedInstantReserve` credits a phantom 100 of held cash. `reserveAmount` and `totalBasis` are both inflated by 100, so `defaultRecoveryPrice = reserveAmount * 1e18 / totalBasis` is pushed upward toward 1 while the real reserve lacks the phantom 100 [7](#0-6) . Every default-epoch receipt holder (including a second instant requester or normal withdrawer, all unprivileged users) claims `claimBasis * recoveryPrice`, so early claimants are systematically overpaid and the real `defaultRecoveryReserve` is exhausted before later claims, which revert in `_transferDefaultRecovery`. The broken invariant is donation/reserve isolation and "one receipt, one payout": claimed-and-paid receipts are double-counted, causing theft of other users' unclaimed recovery yield plus permanent freezing of the tail claims. No guard catches it — `defaultInstantWithdrawsFinalized` only checks `pendingInstantWithdraws != 0`, and KYC/`_onlyIdleCDO` gating is irrelevant since all callers are legitimate.

### Likelihood Explanation
Requires a partial instant-withdraw funding followed by an in-epoch claim and a same-epoch borrower default with another instant receipt still pending. Borrower default is an honest-manager event, not attacker-controlled, so likelihood is moderate-low; but no privileged misbehavior is needed and the code path is fully deterministic once the sequencing occurs. The attacker is simply an unprivileged lender who requests an instant withdrawal and claims first after finalization — ordinary usage.

### Recommendation
Shrink all correlated ledgers together when an instant receipt is paid or claimed: in `claimInstantWithdrawRequest`, decrement `instantWithdrawsRequestsByEpoch[_user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]` for the request's epoch (tracking the epoch per user, e.g. a `lastInstantRequestEpoch` mapping, since receipts may span epochs), mirroring how `_clearWithdrawClaimForEpoch` keeps `withdrawsRequests`/`withdrawsRequestsByEpoch` consistent. Alternatively, in `collectInstantWithdrawFunds`, reduce `instantWithdrawClaimsByEpoch[epochNumber]` by the funded amount so `defaultPendingClaimBasis` and `_defaultPrefundedInstantReserve` see only still-owed basis. Add a fork test: two instant requests in one epoch, partial collect, one claim, then `stopEpochWithDuration`-style default + `finalizeDefaultRecovery`, asserting reserve ≥ sum of payouts.

### Proof of Concept
```solidity
// Foundry fork test sketch against contracts/strategies/idle/IdleCreditVault.sol
// Setup: epoch CDO + credit vault, KYC'd users A and B, running epoch N.
function testStaleInstantLedgerInflatesRecovery() public {
    // epoch N running; A and B requestInstantWithdraw(100e6) via cdoEpoch
    vm.prank(address(cdo)); vault.requestInstantWithdraw(100e6, A); // claimsByEpoch[N]=100, pending=100
    vm.prank(address(cdo)); vault.requestInstantWithdraw(100e6, B); // claimsByEpoch[N]=200, pending=200

    // CDO funds only A's share
    deal(underlying, address(cdo), 100e6);
    vm.prank(address(cdo)); vault.collectInstantWithdrawFunds(100e6); // pending=100

    // A claims funded cash in same epoch; by-epoch ledgers NOT cleared
    vm.prank(address(cdo)); vault.claimInstantWithdrawRequest(A);
    assertEq(vault.instantWithdrawClaimsByEpoch(N), 200); // stale: only 100 still owed

    // borrower defaults within epoch N; finalizeDefaultRecovery
    // pendingBasis includes 200 (should be 100); prefundedReserve = 200-100 = 100 (phantom)
    // recovered = real recovery R
    vault.finalizeDefaultRecovery(R, recoverySource);

    // defaultRecoveryPrice inflated; B and other claimants overpaid pro rata,
    // reserve exhausts early -> last claimant's _transferDefaultRecovery reverts
    // Assert: vault balance < sum(claimBasis_i * defaultRecoveryPrice / 1e18)
}
```

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L398-403)
```text
  function collectInstantWithdrawFunds(uint256 _amount) external {
    _onlyIdleCDO();
    if (_amount == 0) return;
    pendingInstantWithdraws -= _amount;
    underlyingToken.safeTransferFrom(idleCDO, address(this), _amount);
  }
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L844-853)
```text
    claimBasis = instantWithdrawsRequestsByEpoch[_user][defaultEpoch];
    if (claimBasis == 0) return claimBasis;

    instantWithdrawsRequestsByEpoch[_user][defaultEpoch] = 0;
    instantWithdrawsRequests[_user] -= claimBasis;
    uint256 pending = pendingInstantWithdraws;
    // `pendingInstantWithdraws` is only the unfunded remainder. If this user's claim is larger,
    // the extra amount was already counted as prefunded reserve during default finalization.
    pendingInstantWithdraws = claimBasis >= pending ? 0 : pending - claimBasis;
    instantWithdrawClaimsByEpoch[defaultEpoch] -= claimBasis;
```
