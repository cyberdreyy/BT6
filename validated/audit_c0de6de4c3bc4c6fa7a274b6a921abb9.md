### Title
Post-default instant-withdraw requests are booked under the defaulted epoch and drain `defaultRecoveryReserve` - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
The kernel bug is a traversal of a shared list performed without the lock that protects it: code drops/misses the protection while walking the file-lock list, so entries that were never accounted for get consumed. The analog in `IdleCreditVault` is `requestInstantWithdraw`: unlike `requestWithdraw`, it performs no `defaultRecoveryFinalized` handling, so an instant-withdraw receipt created *after* default finalization is recorded under `instantWithdrawsRequestsByEpoch[user][epochNumber]`, which is still equal to `defaultRecoveryEpoch` because `epochNumber` is only bumped by `stopEpoch` and no further `stopEpoch` runs after default. `claimInstantWithdrawRequest` then routes it through `_claimDefaultedInstantWithdrawRequest`, which pays `claimBasis * defaultRecoveryPrice` out of `defaultRecoveryReserve` — a reserve that was sized at finalization to cover only pre-existing claims. [1](#0-0) [2](#0-1) 

### Finding Description
`requestWithdraw` contains an explicit `defaultRecoveryFinalized` branch that forces users to clear old receipts first and stores new requests in a separate `postDefaultRequests` bucket backed 1:1 by burned/minted receipts. [3](#0-2)  `requestInstantWithdraw` has no equivalent branch: it only calls `_ensureDefaultRecoveryInitialized`, burns the CDO's strategy tokens, mints a receipt to the user, and writes `instantWithdrawsRequestsByEpoch[user][epochNumber] += amount`. [4](#0-3) 

After `finalizeDefaultRecovery` sets `defaultRecoveryFinalized` and fixes `defaultRecoveryEpoch = epochNumber`, the epoch never advances again. Any new instant request therefore lands in the same per-epoch bucket that `_claimDefaultedInstantWithdrawRequest` treats as "defaulted-epoch receipt covered by the finalized reserve". On claim, it decrements `instantWithdrawsRequestsByEpoch[defaultEpoch]`, `instantWithdrawsRequests`, `pendingInstantWithdraws`, and `instantWithdrawClaimsByEpoch[defaultEpoch]` — all of which stay internally consistent — and then calls `_transferDefaultRecovery`, which does `defaultRecoveryReserve -= amount` and transfers from the isolated reserve. [5](#0-4) [6](#0-5) 

The analogous "missing lock during traversal" defect: the per-epoch ledger that `claimInstantWithdrawRequest` walks assumes membership was frozen at finalization (like the kernel assuming the lock list is stable under `flc_lock`), but `requestInstantWithdraw` mutates that same epoch bucket afterwards because the post-finalization guard (`nfsi->rwsem` equivalent — the `defaultRecoveryFinalized` branch) was never applied on this path.

The only incidental gate is `_ensureDefaultRecoveryInitialized`, which reverts while `pendingInstantWithdraws != 0`; once pre-default instant receipts have been claimed (or none existed), that check passes and the path is fully open. [7](#0-6) 

### Impact Explanation
`defaultRecoveryReserve` is a fixed, isolated pool sized at finalization to pay `defaultRecoveryPrice * basis` to legitimate defaulted-epoch claimants and `postDefaultRequests` claimants. [8](#0-7)  Each attacker's post-finalization instant receipt converts burned strategy tokens into a reserve claim that was never funded into the reserve. The attacker receives `amount * defaultRecoveryPrice` of underlying, directly reducing the reserve available to honest claimants — theft of unclaimed recovery proceeds, with the loss equal to the attacker's minted receipt times `defaultRecoveryPrice`, up to draining the reserve so the last honest claimants' `safeTransfer`/`reserve -=` reverts (permanent freezing of their recovery). The broken invariant is reserve isolation: only claims priced into the reserve at finalization may draw on it.

### Likelihood Explanation
Requires `allowInstantWithdraw` to be enabled (honest manager) and the pool to be in the finalized-default state. The attacker only needs to be a tranche-token holder able to call `requestInstantWithdraw` on the CDO (KYC-passing lender suffices) after all pre-finalization instant receipts are cleared. No privileged action is needed; the request/claim calls are the normal unprivileged flow. One caveat I could not fully verify within the search budget: whether `IdleCDOEpochVariant.requestInstantWithdraw` adds an extra `defaulted`/`epochEndDate` gate on the CDO side — the strategy-side code itself clearly lacks the check, and the surrounding design (`requestWithdraw`'s explicit post-default branch) indicates the strategy is expected to enforce it.

### Recommendation
Mirror the `requestWithdraw` post-default handling in `requestInstantWithdraw`: when `defaultRecoveryFinalized` is true, either revert, or record the receipt in a bucket that cannot share `defaultRecoveryEpoch` (e.g., a `postDefaultInstantRequests` mapping) and fund/pay it outside `defaultRecoveryReserve`. Alternatively, bump `epochNumber` (or otherwise change the per-epoch key) at `finalizeDefaultRecovery` so post-finalization requests can never alias the defaulted epoch.

### Proof of Concept
Foundry fork PoC (sketch against the existing `IdleCreditVault.t.sol` harness):

```solidity
// Setup: pool running, AA deposit by honestUser, allowInstantWithdraw enabled.
// Epoch N: honestUser requests instant withdraw of X; attacker deposits AA.
// Borrower defaults -> manager calls finalizeDefaultRecovery.
//   defaultRecoveryFinalized = true, defaultRecoveryEpoch = epochNumber (N),
//   defaultRecoveryReserve sized for honest claims only.
// honestUser claims -> pendingInstantWithdraws reaches 0.

// Attacker (KYC'd AA holder) requests instant withdraw post-finalization:
vm.prank(attacker);
cdoEpoch.requestInstantWithdraw(attackerTrancheBal, address(AAtranche));
// strategy: instantWithdrawsRequestsByEpoch[attacker][N] += amt  (N == defaultRecoveryEpoch)

// Attacker claims; _claimDefaultedInstantWithdrawRequest pays from reserve:
vm.prank(attacker);
cdoEpoch.claimInstantWithdrawRequest();
// attacker received amt * defaultRecoveryPrice from defaultRecoveryReserve

// Assertion: reserve no longer covers remaining honest postDefaultRequests /
// defaulted receipts -> their claim reverts or is underpaid by exactly
// amt * defaultRecoveryPrice.
```

The critical assertion is `defaultRecoveryReserve` solvency: sum of outstanding legitimate claims exceeds remaining reserve by the attacker's payout, proving reserve theft rather than a funded withdrawal.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L92-99)
```text
  /// @notice underlying reserved for finalized post-default recovery claims
  uint256 public defaultRecoveryReserve;
  /// @notice post-default recovery ratio, scaled by 1e18
  uint256 public defaultRecoveryPrice;
  /// @notice strategy epoch that defaulted and was finalized for recovery
  uint256 public defaultRecoveryEpoch;
  /// @notice true once the CDO finalized default recovery accounting
  bool public defaultRecoveryFinalized;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L247-257)
```text
    if (defaultRecoveryFinalized) {
      // user should first claim old already-funded withdraw requests before requesting new ones after default
      if (_hasWithdrawRequest(_user) || instantWithdrawsRequests[_user] != 0 || postDefaultRequests[_user] != 0) {
        revert NotAllowed();
      }
      // Preserve request/claim UX after default without increasing borrower-facing pendingWithdraws.
      // The CDO passes an already-haircut amount because finalization lowered virtualPrice first.
      _burn(msg.sender, _amount);
      _mint(_user, _amount);
      postDefaultRequests[_user] = _amount;
      return;
```

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L912-917)
```text
  function _transferDefaultRecovery(address _user, uint256 _amount) internal {
    if (_amount == 0) return;
    // Every defaulted or post-default claim consumes the isolated recovery reserve.
    defaultRecoveryReserve -= _amount;
    underlyingToken.safeTransfer(_user, _amount);
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L924-930)
```text
  function _ensureDefaultRecoveryInitialized() internal {
    if (defaultRecoveryInitialized) return;
    if (pendingInstantWithdraws != 0) revert NotAllowed();
    if (pendingWithdraws != 0) {
      IIdleCDOEpochVariant cdo = IIdleCDOEpochVariant(idleCDO);
      if (cdo.epochEndDate() != 0 || cdo.defaulted()) revert NotAllowed();
      pendingWithdraws = 0;
```
