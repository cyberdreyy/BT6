### Title
Stale per-epoch instant-withdraw basis is never cleared on the funded-claim path, inflating `defaultPendingClaimBasis` and corrupting default recovery pricing - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The nDPI CVE-2020-15475 analog is "omitted reinitialization": a per-epoch accounting structure is written on request but never reset on the corresponding claim, so stale state is re-read later. In `IdleCreditVault`, `requestInstantWithdraw` writes both `instantWithdrawsRequests[_user]` and the per-epoch maps `instantWithdrawsRequestsByEpoch[_user][epoch]` / `instantWithdrawClaimsByEpoch[epoch]`. The normal claim path `claimInstantWithdrawRequest` clears only the aggregate `instantWithdrawsRequests[_user]`; the per-epoch entries are only cleared inside `_claimDefaultedInstantWithdrawRequest`. `collectInstantWithdrawFunds` likewise decrements only `pendingInstantWithdraws`. As a result `instantWithdrawClaimsByEpoch[epochNumber]` permanently includes instant receipts that were already funded and paid out in the same epoch.

### Finding Description
- `requestInstantWithdraw` records `instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount` and `instantWithdrawClaimsByEpoch[currentEpoch] += _amount` at `contracts/strategies/idle/IdleCreditVault.sol:371-372`. [1](#0-0) 
- `claimInstantWithdrawRequest` burns the receipt and zeroes only `instantWithdrawsRequests[_user]`; the two per-epoch maps are untouched. [2](#0-1) 
- `collectInstantWithdrawFunds` reduces `pendingInstantWithdraws` but not `instantWithdrawClaimsByEpoch`. [3](#0-2) 
- At default finalization, `defaultPendingClaimBasis()` adds `instantWithdrawClaimsByEpoch[epochNumber]` to `pendingWithdraws` whenever `pendingInstantWithdraws != 0`, and `_defaultPrefundedInstantReserve()` counts `instantBasis - pendingInstant` as already-held reserve — both computed from stale, never-decremented data. [4](#0-3) [5](#0-4) 
- This basis feeds `recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis` and `defaultRecoveryReserve` in `finalizeDefaultRecovery`. [6](#0-5) 
- Only `_claimDefaultedInstantWithdrawRequest` clears the per-epoch entries, and it runs only post-default. [7](#0-6) 

### Impact Explanation
If an epoch contains instant receipts that were funded and already claimed (so the underlying left the strategy) while other instant receipts remain unfunded (`pendingInstantWithdraws != 0`), a subsequent borrower default finalized in that same `epochNumber` double-counts the already-paid basis:

1. `totalBasis` is inflated by the stale `instantWithdrawClaimsByEpoch` amount, so `defaultRecoveryPrice` is set too low — every legitimate recovery claimant (active AA/BB holders and pending withdraw receipts) is underpaid.
2. `_defaultPrefundedInstantReserve` counts `instantBasis - pendingInstant` as underlying the strategy holds. If the strategy actually holds less than that (part of it was paid out to the already-claimed user), `defaultRecoveryReserve` is set above the real balance. Early claimants are paid via `_transferDefaultRecovery` (`defaultRecoveryReserve -= _amount` then `safeTransfer`), and late claimants revert on insufficient balance — permanent freezing of the remaining recovery fund.

Broken invariant: solvency of the recovery reserve and fair loss waterfall — an epoch's instant-claim basis must equal outstanding receipts, not cumulative historical requests.

### Likelihood Explanation
Requires: instant-withdraw mode enabled, at least one funded+claimed instant receipt and at least one still-unfunded instant receipt coexisting in the same `epochNumber`, and a borrower default finalized in that epoch. The attacker is an ordinary KYC-passing lender whose own claimed instant receipt is what leaves the stale basis; the default itself is an honest-role event. No privileged malicious action is needed. The window is bounded by the epoch duration, and whether the pool actually holds less than the computed prefunded reserve depends on funding order via `getInstantWithdrawFunds`/`collectInstantWithdrawFunds`, so impact ranges from recovery-price dilution (certain) to reserve insolvency for late claimants (when the strategy's real balance is below the counted prefunded share).

### Recommendation
Decrement `instantWithdrawsRequestsByEpoch[_user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]` when an instant receipt is claimed or funded — e.g., in `claimInstantWithdrawRequest`, locate the user's outstanding per-epoch entries and subtract the claimed amount (or track per-user per-epoch settlement), and in `collectInstantWithdrawFunds`/`getInstantWithdrawFunds` reduce `instantWithdrawClaimsByEpoch` by the funded portion so `defaultPendingClaimBasis` and `_defaultPrefundedInstantReserve` only ever see still-outstanding basis.

### Proof of Concept
Foundry fork sketch (modeled on `test/foundry/IdleCreditVault.t.sol` instant-withdraw tests):

```solidity
function testPocStaleInstantBasisInflatesDefault() external {
    _setFeeParams(TL_MULTISIG, 10000, FULL_ALLOC, cdoEpoch.managementFee());
    uint256 amountWei = 10000 * ONE_SCALE;
    uint256 mintedAA = idleCDO.depositAA(amountWei);

    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr / 2, _expectedFundsEndEpoch());
    _startEpochAndCheckPrices(1); // running epoch, epochNumber = 1

    // user A (this contract) requests instant withdraw and gets funded + claimed
    uint256 reqA = cdoEpoch.requestWithdraw(mintedAA / 2, address(AAtranche));
    // user B requests instant withdraw but stays unfunded
    // (second CDO/user context requesting instant withdraw, amount reqB)
    vm.warp(block.timestamp + cdoEpoch.instantWithdrawDelay() + 1);
    _getInstantFunds(); // funds reqA only; pendingInstantWithdraws still > 0 (reqB)
    cdoEpoch.claimInstantWithdrawRequest(); // clears instantWithdrawsRequests[user] only

    // stale state: instantWithdrawClaimsByEpoch[1] still includes reqA
    assertGt(strategy.instantWithdrawClaimsByEpoch(1), reqB_basis);

    // borrower defaults; owner/manager finalizes recovery
    _handleBorrowerDefault();           // honest role action
    uint256 basis = strategy.defaultPendingClaimBasis();
    // basis incorrectly includes already-paid reqA
    assertGt(basis, strategy.pendingWithdraws() + reqB_basis);

    _finalizeDefaultRecovery();
    // recoveryPrice is depressed; when the strategy's real balance is below
    // counted prefundedReserve, the last recovery claim reverts in
    // _transferDefaultRecovery -> safeTransfer (permanent freeze).
}
```

Note: the exploitability of step 2 (reserve insolvency vs. only price dilution) depends on the exact funding order in `IdleCDOEpochVariant.getInstantWithdrawFunds`, which I was not able to fully trace within the available iterations; the stale-basis write/never-clear itself is confirmed in the code above.

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L644-649)
```text
  function defaultPendingClaimBasis() public view returns (uint256 basis) {
    basis = pendingWithdraws;
    if (pendingInstantWithdraws != 0) {
      basis += instantWithdrawClaimsByEpoch[epochNumber];
    }
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L685-696)
```text
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
