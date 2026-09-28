### Title
Stale-epoch instant-withdraw receipt escapes the default-recovery haircut and is paid at par — (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The kernel bug (CVE-2025-22085) is a use-after-free: `fill_nldev_handle` dereferences a device name pointer after `dev_set_name` freed and reallocated it — i.e., an object is still reachable through a *stale key* after the canonical key was replaced. The analog in `IdleCreditVault` is per-epoch receipt indexing: instant-withdraw receipts are recorded under `instantWithdrawsRequestsByEpoch[user][epochNumber]`, but `finalizeDefaultRecovery` computes the recovery basis using only `instantWithdrawClaimsByEpoch[epochNumber]` — the *current* epoch bucket. An instant receipt created in an earlier epoch that was never funded survives as a "dangling" receipt keyed to a dead epoch, exactly like the freed RDMA device name.

### Finding Description
`requestInstantWithdraw` keys each receipt to the epoch in which it was created [1](#0-0) . Instant requests can remain unfunded across an epoch boundary — `pendingInstantWithdraws` is only decremented by `collectInstantWithdrawFunds` [2](#0-1) , and the test `testClaimInstantWithdrawRequestAfterAnEpochWithInstantWithdraw` confirms receipts routinely persist unfunded for multiple epochs [3](#0-2) .

When the borrower later defaults and `finalizeDefaultRecovery` runs, the pending claim basis adds only the *current* epoch's instant bucket [4](#0-3) , and the prefunded-reserve computation likewise reads only `instantWithdrawClaimsByEpoch[epochNumber]` [5](#0-4) . On claim, `_claimDefaultedInstantWithdrawRequest` clears only `instantWithdrawsRequestsByEpoch[user][defaultRecoveryEpoch]` [6](#0-5) . The residue — the stale-epoch receipt still in `instantWithdrawsRequests[user]` — is then burned and paid **at par** via `_transferFundedClaim` [7](#0-6) .

Two bad outcomes follow, depending on strategy cash:

1. **Theft / reserve shortfall**: if the strategy holds non-reserve underlyings (e.g., new deposits, interest, or `_defaultPrefundedInstantReserve` counted only for the current epoch), the stale claimant is paid 100% while identically-unfunded default-epoch claimants receive `defaultRecoveryPrice` — the recovery reserve was sized without them, so their payout dilutes or drains funds belonging to active-LP recovery.
2. **Permanent freezing**: if `balance - reserve < amount`, `_transferFundedClaim` reverts `NotAllowed` [8](#0-7) , permanently bricking `claimInstantWithdrawRequest` for that user — and because `claimWithdrawRequest` and `requestWithdraw` gate on receipt state, it can wedge their whole position.

The `NotAllowed` guard at `requestWithdraw` (lines 263–271) does not apply — it protects `lossRecoveryPriceByEpoch`, not the instant path, and `requestInstantWithdraw` has no equivalent stale-epoch check.

### Impact Explanation
Unfunded instant receipts from pre-default epochs either (a) extract full-par payouts worth `amount × (1 − defaultRecoveryPrice)` more than entitled — direct theft from the recovery pool/other claimants — or (b) permanently freeze the user's claim and block subsequent requests. Loss is quantifiable: `staleInstantBasis × (1 − recoveryPrice)` stolen, or `staleInstantBasis` frozen.

### Likelihood Explanation
Requires an unprivileged user to request an instant withdraw that stays unfunded until epoch end (attacker-controllable timing — request just before `stopEpoch` or when instant liquidity is absent), then a borrower default in a later epoch (external trigger, not attacker-caused but a normal protocol state). No privileged misbehavior needed; KYC'd lender suffices.

### Recommendation
In `finalizeDefaultRecovery`, include **all** unfunded instant receipts in the basis — iterate is impossible, so track a global `totalInstantClaimBasis` (or sum `instantWithdrawClaimsByEpoch` across live epochs) rather than only `instantWithdrawClaimsByEpoch[epochNumber]`. Correspondingly, `_claimDefaultedInstantWithdrawRequest` should clear every epoch key the user holds (e.g., track the user's request epochs), not just `defaultRecoveryEpoch`, and `_defaultPrefundedInstantReserve` should use the same aggregate basis.

### Proof of Concept
Foundry fork scenario (mirroring `testClaimInstantWithdrawRequestAfterAnEpochWithInstantWithdraw` + `testProcessPostDefaultWithdrawalAsNormalClaim`):

```solidity
// 1. Epoch N running: attacker (KYC'd AA holder) requests instant withdraw.
cdoEpoch.requestInstantWithdraw(amount, address(AAtranche)); // recorded under epoch N

// 2. stopEpoch N without calling getInstantWithdrawFunds -> receipt stays unfunded.
cdoEpoch.stopEpoch(apr, 0);   // pendingInstantWithdraws > 0, bucket keyed at N

// 3. startEpoch N+1, warp past end, stop with insufficient borrower repayment.
cdoEpoch.startEpoch();
// borrower underfunds -> defaulted() == true
cdoEpoch.stopEpoch(0, 0);

// 4. finalizeDefaultRecovery: basis = pendingWithdraws + instantWithdrawClaimsByEpoch[N+1]
//    -> attacker's epoch-N instant basis EXCLUDED; recoveryPrice computed too high.
cdoEpoch.finalizeDefault(recovered, recoverySource);

// 5. claimInstantWithdrawRequest: _claimDefaultedInstantWithdrawRequest clears only
//    epoch-N+1 key (zero for attacker); then pays instantWithdrawsRequests[attacker] AT PAR
//    via _transferFundedClaim -> overpays vs recovery price, or reverts NotAllowed if
//    balance - reserve < amount (permanent freeze).
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L387-392)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L843-855)
```text
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

**File:** test/foundry/IdleCreditVault.t.sol (L4835-4851)
```text
    // request instant withdraw (5000)
    uint256 requestedAA1 = cdoEpoch.requestWithdraw(mintedAA / 2, address(AAtranche));

    // run epoch 1 (same apr for the next one)
    _startEpochAndCheckPrices(1);
    vm.warp(block.timestamp + cdoEpoch.instantWithdrawDelay() + 1);
    _getInstantFunds();
    // Here we could have claimed but we did not
    _stopEpochAndCheckPrices(1, initialProvidedApr / 2, _expectedFundsEndEpoch());

    // run epoch 2
    _startEpochAndCheckPrices(2);

    // claim right away as it was an old withdraw request
    uint256 balPre = IERC20Detailed(defaultUnderlying).balanceOf(address(this));
    cdoEpoch.claimInstantWithdrawRequest();
    assertEq(IERC20Detailed(defaultUnderlying).balanceOf(address(this)) - balPre, requestedAA1, 'claimInstantWithdrawRequest is wrong');
```
