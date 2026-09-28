### Title
Stale per-epoch instant-withdraw ledger overruns the outstanding receipt, permanently freezing claims and distorting default recovery - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The dw-axi-dmac bug is a lifetime mismatch: the deallocation path walks `descs_allocated` (cumulative) instead of the number of `hw_desc` actually allocated for that descriptor, overrunning the array. `IdleCreditVault` has the same shape: `instantWithdrawsRequestsByEpoch[user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]` are incremented on every `requestInstantWithdraw` but are **never decremented** when the receipt is funded and claimed via `claimInstantWithdrawRequest`. The post-default claim path then consumes the cumulative epoch total as if it were the still-outstanding receipt count, exactly like `axi_desc_put` consuming `descs_allocated`.

### Finding Description
On `requestInstantWithdraw`, three counters are increased: `instantWithdrawsRequests[_user]` (outstanding receipt), `instantWithdrawsRequestsByEpoch[_user][epochNumber]` and `instantWithdrawClaimsByEpoch[epochNumber]` (per-epoch basis). On a successful funded claim (`claimInstantWithdrawRequest`), only `instantWithdrawsRequests[_user]` is cleared; the two per-epoch ledgers are left stale. They are only decremented in `_claimDefaultedInstantWithdrawRequest`, which runs after default finalization.

Two consequences:

1. **Re-request overrun (double-count of the same receipt).** A user who claims a funded instant withdrawal and requests again in the same epoch ends with `instantWithdrawsRequests[u] = 50` but `instantWithdrawsRequestsByEpoch[u][epoch] = 150`. After `finalizeDefaultRecovery`, `_claimDefaultedInstantWithdrawRequest` computes `claimBasis = 150` and executes `instantWithdrawsRequests[_user] -= claimBasis` (line 848), which underflows against the real outstanding balance of 50. Every subsequent `claimInstantWithdrawRequest` reverts — the user's legitimate 50-underlying receipt is permanently frozen.

2. **Inflated recovery basis and phantom reserve.** `defaultPendingClaimBasis` adds `instantWithdrawClaimsByEpoch[epochNumber]` (150, including the already-paid 100) to the default basis, and `_defaultPrefundedInstantReserve` counts `instantBasis - pendingInstant` = 100 underlying that was already transferred out as if it were still held reserve. `defaultRecoveryReserve`/`defaultRecoveryPrice` are therefore computed on a bloated denominator with phantom backing: all defaulted claimants are diluted, and the reserve bookkeeping can exceed the real balance, making later `_transferDefaultRecovery` calls revert (`defaultRecoveryReserve -= _amount` underflow / insufficient balance) — permanently freezing the remaining recovery funds.

### Impact Explanation
- Direct permanent freezing of an unprivileged user's unfunded instant-withdraw receipt after a borrower default (scenario 1: user loses 50 underlying-equivalent claim entirely).
- Recovery-price dilution plus reserve insolvency for all default claimants: the phantom prefunded amount inflates `recoveryPrice`, so early claimants are overpaid relative to actual holdings and the last claimants' transfers revert, locking residual recovery underlying in the strategy forever (only `transferToken` owner rescue could recover it).
- No privileged misbehavior required: funding (`collectInstantWithdrawFunds`) and default finalization are honest owner/borrower actions; the attacker/victim is any user who requests an instant withdraw, gets funded, claims, and requests again in the same epoch — a normal usage pattern.

### Likelihood Explanation
Instant withdrawals are a supported, manager-enabled flow (`setInstantWithdrawParams`), and re-requesting within the same epoch is unrestricted — `requestInstantWithdraw` has no "existing request" guard analogous to the `requestWithdraw` `lossRecoveryPrice` check. The bug triggers deterministically whenever a funded-then-claimed user re-requests in the same epoch and that epoch ends in default finalization with `pendingInstantWithdraws != 0`. Borrower default is a designed-for state (`finalizeDefaultRecovery`), not an anomaly.

### Recommendation
Keep the per-epoch ledgers in sync with the outstanding receipt ledger on every claim, mirroring the fix of tracking the actually-allocated count:

- In `claimInstantWithdrawRequest`, after computing the funded payout, decrement `instantWithdrawsRequestsByEpoch[_user]` across the epochs that make up the claimed amount and `instantWithdrawClaimsByEpoch` accordingly (or store a separate "claimed" cursor per epoch).
- Alternatively, record a per-epoch "funded/claimed" amount at funding time (`collectInstantWithdrawFunds`) so `defaultPendingClaimBasis` and `_defaultPrefundedInstantReserve` only count receipts that are still outstanding, and `_claimDefaultedInstantWithdrawRequest` uses outstanding-basis rather than cumulative requested basis.

### Proof of Concept
Foundry fork test (against the epoch-enabled `IdleCreditVault` + `IdleCDOEpochVariant` setup used in `test/foundry/IdleCDOEpochQueue.t.sol`):

```solidity
function testInstantWithdrawStaleEpochBasisFreezesClaim() external {
    _useStandardEpochVariant();
    _stopCurrentEpoch();                       // buffer of epoch 0

    // user deposits and gets tranche tokens
    uint256 amount = 200e6;
    address alice = makeAddr('alice');
    uint256 tranches = _depositWithUser(alice, amount);

    // enable instant withdrawals for the epoch
    vm.prank(manager);
    cdoEpoch.setInstantWithdrawParams(instantDelay, 10e18, true);
    vm.prank(manager);
    cdoEpoch.startEpoch();                     // epoch N running

    // 1) alice instant-withdraws 100
    _requestInstantWithdrawWithUser(alice, 100e18);

    // 2) honest manager/borrower funds the instant request
    vm.prank(manager);
    cdoEpoch.collectInstantWithdrawFunds(100e6);

    // 3) alice claims her funded instant withdrawal
    vm.prank(alice);
    cdoEpoch.claimInstantWithdrawRequest(tranche);
    assertEq(strategy.instantWithdrawsRequests(alice), 0);
    // per-epoch ledger still records 100 -- STALE
    assertEq(strategy.instantWithdrawsRequestsByEpoch(alice, strategy.epochNumber()), 100e18);

    // 4) alice re-requests 50 in the SAME epoch
    _requestInstantWithdrawWithUser(alice, 50e18);
    assertEq(strategy.instantWithdrawsRequests(alice), 50e18);
    assertEq(strategy.instantWithdrawsRequestsByEpoch(alice, strategy.epochNumber()), 150e18);

    // 5) borrower defaults; owner finalizes recovery with unfunded instant bucket
    _defaultBorrowerAndFinalize();             // pendingInstantWithdraws = 50e6 != 0

    // 6) alice tries to claim her remaining 50 -- reverts on underflow
    vm.expectRevert();                         // instantWithdrawsRequests -= 150 underflows
    vm.prank(alice);
    cdoEpoch.claimInstantWithdrawRequest(tranche);
    // her 50-underlying receipt is permanently frozen
}
```

Key assertions showing the corruption: `instantWithdrawClaimsByEpoch[N] == 150e18` while only `50e18` is outstanding, so `defaultPendingClaimBasis()` overstates basis by `100e18` and `_defaultPrefundedInstantReserve()` counts `100e18` of already-paid underlying as reserve, inflating `defaultRecoveryPrice`/`defaultRecoveryReserve` beyond actual holdings and causing later `_transferDefaultRecovery` calls to revert. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L380-393)
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
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L644-648)
```text
  function defaultPendingClaimBasis() public view returns (uint256 basis) {
    basis = pendingWithdraws;
    if (pendingInstantWithdraws != 0) {
      basis += instantWithdrawClaimsByEpoch[epochNumber];
    }
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L842-855)
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
```
