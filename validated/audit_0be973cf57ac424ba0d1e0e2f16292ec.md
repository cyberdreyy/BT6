### Title
Unfunded instant-withdraw receipts from a pre-default epoch bypass the recovery haircut and drain funded claims via `claimInstantWithdrawRequest` - (contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The external bug is a "carry-over erased by a special-cased path": `LockManager._lock` skips the `remainder` computation when called by `MigrationManager` but still writes `lockedToken.remainder`, wiping the user's accumulated remainder. The closest idle-tranches analog is in `IdleCreditVault.claimInstantWithdrawRequest` / `finalizeDefaultRecovery`: instant-withdraw receipts keyed to a request epoch **older** than `defaultRecoveryEpoch` are skipped by the haircut path `_claimDefaultedInstantWithdrawRequest` (which only looks at `defaultEpoch`) and are also excluded from `defaultPendingClaimBasis`/`defaultRecoveryReserve`, yet the unconditional fallback still pays `instantWithdrawsRequests[_user]` **at par** through `_transferFundedClaim` and zeroes the aggregate. The leftover claim is paid in full from underlyings that back other users' already-funded receipts — or, if no free balance exists, the receipt is frozen forever.

### Finding Description
`finalizeDefaultRecovery` computes the default-epoch instant basis only from the current epoch: `basis += instantWithdrawClaimsByEpoch[epochNumber]`, and sets `defaultInstantWithdrawsFinalized = pendingInstantWithdraws != 0` [1](#0-0) , [2](#0-1) .

The keyed lookups in `_claimDefaultedInstantWithdrawRequest` only clear `instantWithdrawsRequestsByEpoch[_user][defaultEpoch]` — an older-epoch receipt is invisible to it [3](#0-2) .

After that, `claimInstantWithdrawRequest` unconditionally pays the whole `instantWithdrawsRequests[_user]` at par and zeroes it, without decrementing `pendingInstantWithdraws` or clearing the per-epoch keys [4](#0-3) . `_transferFundedClaim` only refuses to spend `defaultRecoveryReserve`, so the claim is satisfied out of any other strategy underlyings [5](#0-4) .

Reachability: instant requests are recorded per current `epochNumber` [6](#0-5)  and funded via `collectInstantWithdrawFunds` at stopEpoch; if only part is collected, `pendingInstantWithdraws` stays non-zero. `deposit()` bumps `epochNumber` during the running-epoch stop [7](#0-6) , so a still-unfunded instant receipt ends up keyed to an epoch strictly below a later `defaultRecoveryEpoch`. Recovery then ignores it in both basis and prefunded-reserve math [8](#0-7) .

### Impact Explanation
An unprivileged lender holding an old unfunded instant receipt is paid 100% instead of `defaultRecoveryPrice`, consuming underlyings held for other users' funded withdraw receipts — a direct haircut bypass and insolvency for later claimants. Symmetrically, if the strategy holds no free balance, `_transferFundedClaim` reverts and the receipt is permanently frozen even though `defaultPendingClaimBasis` denied it a recovery share.

### Likelihood Explanation
Requires a default occurring one or more epochs after a partially unfunded instant-withdraw queue existed. Partial instant funding is a normal mode (only `collectInstantWithdrawFunds` amounts already sitting in the CDO are pulled), and borrower default finalization is a designed flow. Triggering the claim is a single unprivileged `claimInstantWithdrawRequest` call by the receipt holder.

### Recommendation
In `finalizeDefaultRecovery`/`defaultPendingClaimBasis`, include *all* outstanding instant receipts (aggregate `pendingInstantWithdraws` or a total per-user basis) rather than only `instantWithdrawClaimsByEpoch[epochNumber]`, or iterate/clear all per-epoch instant keys per user in `_claimDefaultedInstantWithdrawRequest`. At minimum, gate the par-payout branch of `claimInstantWithdrawRequest` so that receipts whose epoch predates `defaultRecoveryEpoch` are routed through the recovery price instead of `_transferFundedClaim`.

### Proof of Concept
Foundry test (place in `test/foundry/IdleCreditVault.t.sol`, reusing existing helpers `_depositWithUser`, `_startEpochAndCheckPrices`, `_stopEpochAndCheckPrices`):

```solidity
function testOldEpochInstantReceiptBypassesRecoveryHaircut() external {
    IdleCreditVault cv = IdleCreditVault(address(strategy));
    address bob = makeAddr('instantUser');

    _depositWithUser(bob, 10_000 * ONE_SCALE, true);

    // Epoch 1: bob requests instant withdraw in buffer; CDO only partially funds it
    // so pendingInstantWithdraws stays > 0 (instant request is keyed to epoch N).
    // (drive via cdoEpoch.requestInstantWithdraw + partial collectInstantWithdrawFunds,
    //  or prank cdoEpoch: cv.requestInstantWithdraw(x, bob))

    // stop epoch 1 -> epochNumber increments past bob's request epoch
    _startEpochAndCheckPrices(0);
    // ... fund only part of pendingInstantWithdraws ...
    vm.prank(manager);
    cdoEpoch.stopEpoch(initialProvidedApr, 0); // epochNumber now N+1

    // Epoch 2 runs, borrower defaults
    _startEpochAndCheckPrices(1);
    // under-fund borrower and finalize default recovery
    // -> defaultRecoveryEpoch = N+1, bob's receipt keyed at N is excluded from
    //    instantWithdrawClaimsByEpoch[N+1] and from the recovery reserve.

    // bob claims: _claimDefaultedInstantWithdrawRequest finds 0 at epoch N+1,
    // fallback pays full instantWithdrawsRequests[bob] at par from funded balance.
    uint256 pre = underlying.balanceOf(bob);
    vm.prank(bob);
    cdoEpoch.claimInstantWithdrawRequest();
    assertGt(underlying.balanceOf(bob) - pre, 0, 'paid at par, bypassing haircut');
    // recovery reserve untouched but other claimants' funded balance drained,
    // or call reverts if no free balance (permanent freeze).
}
```

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L367-374)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L607-614)
```text
    if (IIdleCDOEpochVariant(idleCDO).isEpochRunning()) {
      // deposit done on stopEpoch (before setting the var to false) so we reset the counter
      totEpochDeposits = 0;
      epochNumber += 1;
    } else {
      // deposit done between epochs so we increase the counter
      totEpochDeposits += _amount;
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L696-696)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L897-906)
```text
  function _transferFundedClaim(address _user, uint256 _amount) internal {
    if (_amount == 0) return;
    uint256 reserve = defaultRecoveryReserve;
    if (reserve != 0) {
      uint256 balance = underlyingToken.balanceOf(address(this));
      // This should be unreachable when accounting is consistent. Keep the guard so old funded
      // receipts can never spend underlyings reserved for default recovery claimants.
      if (balance < reserve || balance - reserve < _amount) revert NotAllowed();
    }
    underlyingToken.safeTransfer(_user, _amount);
```
