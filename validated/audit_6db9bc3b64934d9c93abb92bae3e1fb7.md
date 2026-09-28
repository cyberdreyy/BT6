### Title
Instant-withdraw per-epoch receipt refcount is never released on the funded-claim path, corrupting default recovery claims - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`requestInstantWithdraw` registers each receipt in three counters: the per-user aggregate `instantWithdrawsRequests`, the per-user-per-epoch `instantWithdrawsRequestsByEpoch`, and the per-epoch global `instantWithdrawClaimsByEpoch` [1](#0-0) . The normal funded claim path in `claimInstantWithdrawRequest` clears only the aggregate counter and burns the receipt tokens; it never decrements `instantWithdrawsRequestsByEpoch` or `instantWithdrawClaimsByEpoch` [2](#0-1) . This is the direct analog of the reported kernel bug: `of_node_put()` was added on one return path but not on all paths — here the per-epoch "refcount" is released on the defaulted-claim path (`_claimDefaultedInstantWithdrawRequest`) but leaked on the already-funded-claim path [3](#0-2) .

### Finding Description
Instant withdrawals can be requested and claimed *within the same running epoch* — the queue contract explicitly states instant-withdraw claims are processable during the epoch, and `collectInstantWithdrawFunds` decrements only `pendingInstantWithdraws` [4](#0-3) . Because `epochNumber` only increments at `stopEpoch` (via `deposit`) [5](#0-4) , a user can hold two instant receipts recorded under the same `instantWithdrawsRequestsByEpoch[user][epochNumber]` in one epoch.

When the borrower defaults mid-epoch, `finalizeDefaultRecovery` sets `defaultRecoveryEpoch = epochNumber` and `defaultInstantWithdrawsFinalized = pendingInstantWithdraws != 0` [6](#0-5) . Two corruptions follow from the stale per-epoch count:

1. **Inflated default claim basis.** `defaultPendingClaimBasis` adds `instantWithdrawClaimsByEpoch[epochNumber]` [7](#0-6) , which still includes already-claimed-and-paid instant receipts. `recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis` is therefore computed on phantom basis, diluting the recovery multiplier paid to *every* defaulted claimant [8](#0-7) .

2. **Frozen claims.** When a victim who claimed an earlier instant receipt in the same epoch calls `claimInstantWithdrawRequest` after finalization, `_claimDefaultedInstantWithdrawRequest` computes `claimBasis` from the stale epoch map (old claimed amount + new pending amount), then executes `instantWithdrawsRequests[_user] -= claimBasis`, which underflows against the smaller live aggregate and reverts permanently [9](#0-8) . The same stale `claimBasis` is also used for `_burn(_user, claimBasis)`, exceeding the receipt tokens the user actually holds.

### Impact Explanation
Every defaulted-epoch recovery claimant receives a smaller payout than entitled because `totalBasis` is inflated by already-paid receipts; the un-spendable surplus stays locked in `defaultRecoveryReserve` forever. Any user who claimed a funded instant withdrawal and re-requested in the same epoch has their legitimate receipt permanently unclaimable — the subtraction underflow in `_claimDefaultedInstantWithdrawRequest` cannot be bypassed by any other code path. This is permanent freezing of user funds plus dilution of the recovery pool, both within the accepted impact classes.

### Likelihood Explanation
The sequence requires only unprivileged actions around honest-role calls: an unprivileged user (any EOA holding strategy tokens via the CDO) requests an instant withdrawal during a running epoch, the honest manager/queue funds and processes the claim, the user requests a second instant withdrawal in the same epoch, and the borrower then defaults mid-epoch with `pendingInstantWithdraws != 0`. Instant-withdraw request/claim is a normal user flow and mid-epoch borrower defaults are a designed state transition, so no privileged misbehavior or exotic precondition is needed.

### Recommendation
Mirror the refcount-release discipline of the upstream fix: release the per-epoch reference on *every* claim path. In `claimInstantWithdrawRequest`, before clearing the aggregate, decrement `instantWithdrawsRequestsByEpoch[_user]` for the recorded request epoch(s) and `instantWithdrawClaimsByEpoch` correspondingly — or track the user's outstanding per-epoch entry and delete it when the aggregate is cleared. Alternatively, settle instant receipts strictly per-epoch (like `withdrawsRequestsByEpoch`) so a funded claim always clears its own epoch entry. Add a regression test: request → collect → claim instant, re-request in the same epoch, finalize a default, and assert both the recovery price and the second claim are correct.

### Proof of Concept
```solidity
// Foundry fork PoC — contracts/strategies/idle/IdleCreditVault.sol
// Phase: running epoch E, instant-withdraw mode, then mid-epoch borrower default.

// 1. User deposits and requests instant withdraw of X via the CDO.
cdoEpoch.requestInstantWithdraw(X, address(AAtranche)); // or AA flow
//    -> instantWithdrawsRequestsByEpoch[user][E] += X
//    -> instantWithdrawClaimsByEpoch[E] += X; pendingInstantWithdraws += X

// 2. Honest manager/queue collects and processes the claim inside epoch E.
cdoEpoch.getInstantWithdrawFunds();           // collectInstantWithdrawFunds(X)
cdoEpoch.claimInstantWithdrawRequest();       // pays user X
//    instantWithdrawsRequests[user] == 0, but:
//    instantWithdrawsRequestsByEpoch[user][E] == X   <-- LEAKED REFCOUNT
//    instantWithdrawClaimsByEpoch[E]          == X   <-- LEAKED REFCOUNT

// 3. User requests a second instant withdraw of Y in the same epoch E.
cdoEpoch.requestInstantWithdraw(Y, ...);
//    instantWithdrawsRequestsByEpoch[user][E] == X + Y

// 4. Borrower defaults mid-epoch; guardian triggers default handling and
//    finalizeDefaultRecovery runs with pendingInstantWithdraws == Y > 0.
//    -> defaultRecoveryEpoch = E
//    -> defaultPendingClaimBasis() includes instantWithdrawClaimsByEpoch[E] = X + Y
//       (X is phantom basis already paid out) -> recoveryPrice diluted for all claimants.

// 5. User tries to claim their legitimate Y receipt.
vm.prank(user);
cdoEpoch.claimInstantWithdrawRequest();
//    _claimDefaultedInstantWithdrawRequest: claimBasis = X + Y
//    instantWithdrawsRequests[user] (Y) -= (X + Y)  -> underflow -> revert
//    Result: user's Y is permanently frozen; all recovery claimants underpaid.
```

Note: the exact CDO-side call names for triggering the instant-withdraw claim and default finalization were not fully traced within the available search iterations, but the strategy-side accounting gap in `claimInstantWithdrawRequest` — clearing the aggregate while leaving both per-epoch counters untouched — is confirmed directly from the code.

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L398-403)
```text
  function collectInstantWithdrawFunds(uint256 _amount) external {
    _onlyIdleCDO();
    if (_amount == 0) return;
    pendingInstantWithdraws -= _amount;
    underlyingToken.safeTransferFrom(idleCDO, address(this), _amount);
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L607-611)
```text
    if (IIdleCDOEpochVariant(idleCDO).isEpochRunning()) {
      // deposit done on stopEpoch (before setting the var to false) so we reset the counter
      totEpochDeposits = 0;
      epochNumber += 1;
    } else {
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L693-696)
```text
    defaultRecoveryEpoch = epochNumber;
    // A non-zero pending instant bucket means current-epoch instant receipts were not fully funded
    // and must be paid through the same recovery ratio as normal pending receipts.
    defaultInstantWithdrawsFinalized = pendingInstantWithdraws != 0;
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
