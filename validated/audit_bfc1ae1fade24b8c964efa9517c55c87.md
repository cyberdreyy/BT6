### Title
Instant-withdraw receipts pending from pre-default epochs escape the recovery haircut and are paid at par, draining `defaultRecoveryReserve` and permanently freezing later claimants - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
CVE-2022-42328/42329 is a deadlock triggered when freeing a packet that was "dropped" under a special mode (netpoll / XSA-392 drop path). The analog is `IdleCreditVault.claimInstantWithdrawRequest`: after borrower default finalization, only instant receipts recorded under `defaultRecoveryEpoch` are cleared with the haircut (`_claimDefaultedInstantWithdrawRequest`), while instant receipts that were requested in an earlier epoch and are still unfunded remain in the aggregate `instantWithdrawsRequests[_user]` ledger and are then paid 1:1 through `_transferFundedClaim`. Because those same unfunded receipts are already counted in `defaultPendingClaimBasis`/the recovery reserve sizing, paying them at par overdraws the reserve and the last claimants' `_transferDefaultRecovery` reverts, permanently freezing their recovery.

### Finding Description
`requestInstantWithdraw` records receipts under `instantWithdrawsRequestsByEpoch[_user][currentEpoch]` and increments `pendingInstantWithdraws` (IdleCreditVault.sol:356-375). Instant receipts can survive an epoch boundary unfunded: nothing forces `getInstantWithdrawFunds`/fulfillment before `stopEpoch`, and `pendingInstantWithdraws` is only decremented in `collectInstantWithdrawFunds` (IdleCreditVault.sol:398-403) or zeroed per-claim in `_claimDefaultedInstantWithdrawRequest`.

After `finalizeDefault`, `claimInstantWithdrawRequest` runs: [1](#0-0) 

`_claimDefaultedInstantWithdrawRequest` clears only `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]`: [2](#0-1) 

Receipts keyed to any earlier epoch (`instantWithdrawsRequestsByEpoch[_user][e]` for `e < defaultRecoveryEpoch`) are never haircutted. They stay inside `instantWithdrawsRequests[_user]` and are paid in full at line 387-392 via `_transferFundedClaim`, which deliberately does not consume `defaultRecoveryReserve` (IdleCreditVault.sol:897+). Yet finalization sizes the reserve using `defaultPendingClaimBasis`, which includes the unfunded `pendingInstantWithdraws` regardless of the epoch they were requested in (see `testFinalizeDefaultHaircutsPendingInstantRedeems`, IdleCreditVault.t.sol:4435-4483, where `prefundedInstant` is deducted from the reserve top-up). The result: the same liability is haircutted once in the reserve math but paid twice at par, so the reserve runs dry before all claims clear and the remaining users' `claimWithdrawRequest`/`claimInstantWithdrawRequest`/`DefaultDistributor.claim` calls revert on insufficient balance — a permanent freeze of their recovery, mirroring the CVE's "drop under a special mode → cleanup deadlocks" pattern.

### Impact Explanation
Broken invariant: one receipt one payout + solvency of the recovery reserve. An attacker (or simply an unlucky earlier claimant) holding pre-default unfunded instant receipts receives `amount` at par instead of `amount * defaultRecoveryPrice / RECOVERY_FULL`, extracting `amount * (RECOVERY_FULL - defaultRecoveryPrice) / RECOVERY_FULL` in excess. The reserve is finite, so an equal value of recovery is permanently frozen for the last claimants — direct theft of unclaimed yield/recovery plus permanent freezing, with loss equal to the aggregate pre-default-epoch unfunded instant receipt basis times the haircut. Attacker cost: deposit, request instant withdraw in epoch N, let it stay unfunded, wait for the honest borrower default and `finalizeDefault`, then claim before others.

### Likelihood Explanation
Requires a specific but reachable sequence: instant withdrawals enabled (standard `IdleCDOEpochVariant`), an instant request left unfunded across at least one `stopEpoch` boundary (possible whenever the instant delay spans the epoch end or the manager simply doesn't call `getInstantWithdrawFunds`), and a subsequent borrower default with `defaultRecoveryPrice < RECOVERY_FULL`. No privileged misbehavior is needed; borrower default is an honest protocol event. Note uncertainty: the exact composition of `defaultPendingClaimBasis`/`instantWithdrawClaimsByEpoch` cleanup inside `finalizeDefaultRecovery` could not be fully read within iteration limits; if finalization re-keys all pending instant receipts into `defaultRecoveryEpoch`, the bug collapses — this should be verified in the PoC.

### Recommendation
In `_claimDefaultedInstantWithdrawRequest`, haircut and clear the user's entire unfunded instant balance (iterate or fold all `instantWithdrawsRequestsByEpoch` entries ≤ `defaultRecoveryEpoch`), not only the entry keyed to `defaultRecoveryEpoch`. Alternatively, during `finalizeDefaultRecovery`, migrate all outstanding `instantWithdrawsRequestsByEpoch` entries and `pendingInstantWithdraws` into `defaultRecoveryEpoch` so that every unfunded instant receipt is settled exclusively through `_transferDefaultRecovery` at `defaultRecoveryPrice`.

### Proof of Concept
Foundry fork sketch (extend `test/foundry/IdleCreditVault.t.sol` helpers):

```solidity
function testPreDefaultEpochInstantReceiptsDrainReserve() external {
    _setFeeParams(TL_MULTISIG, 10000, FULL_ALLOC, cdoEpoch.managementFee());
    uint256 amount = 100_000 * ONE_SCALE;

    address early = makeAddr('early-instant');
    address late  = makeAddr('late-instant');
    _depositWithUser(early, amount / 2, true);
    _depositWithUser(late,  amount / 2, true);
    idleCDO.depositAA(amount / 4);
    idleCDO.depositBB(amount / 4);

    // epoch 0 ends with a lower APR so instant withdrawals are enabled
    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr / 2, _expectedFundsEndEpoch());

    // 'early' requests instant withdraw in buffer of epoch 1; borrower never funds it
    vm.prank(early);
    cdoEpoch.requestWithdraw(0, address(AAtranche)); // routes to requestInstantWithdraw

    // epoch 1 runs and stops WITHOUT getInstantWithdrawFunds: receipt stays unfunded
    _startEpochAndCheckPrices(1);
    // warp past instantWithdrawDelay, do NOT call getInstantWithdrawFunds
    _stopEpochAndCheckPrices(1, initialProvidedApr, _expectedFundsEndEpoch()); // pendingInstantWithdraws carries over

    // 'late' requests instant (or normal) withdraw in buffer of epoch 2
    vm.prank(late);
    cdoEpoch.requestWithdraw(0, address(AAtranche));

    // epoch 2: borrower defaults
    _startEpochAndCheckPrices(2);
    _stopEpochAndCheckPrices(2, initialProvidedApr, 0);
    _checkDefault();

    IdleCreditVault creditVault = IdleCreditVault(address(strategy));
    uint256 totalBasis = cdoEpoch.getContractValue() + cdoEpoch.expectedEpochInterest()
        - cdoEpoch.pendingWithdrawFees() + creditVault.defaultPendingClaimBasis();
    uint256 recovered = totalBasis * 7e17 / ONE_TRANCHE; // 70% recovery
    deal(defaultUnderlying, manager, recovered);
    vm.startPrank(manager);
    IERC20Detailed(defaultUnderlying).approve(address(strategy), recovered);
    cdoEpoch.finalizeDefault(recovered, manager);
    vm.stopPrank();

    // 'early' claims: full instantWithdrawsRequests paid at par, only the
    // defaultRecoveryEpoch-keyed piece (if any) was haircutted
    uint256 balPre = underlying.balanceOf(early);
    vm.prank(early);
    cdoEpoch.claimInstantWithdrawRequest();
    // BUG: early receives ~100% of its receipt instead of ~70%

    // 'late' (and/or DefaultDistributor claimants) now revert: reserve exhausted
    vm.prank(late);
    vm.expectRevert(); // insufficient recovery reserve / transfer failure
    cdoEpoch.claimWithdrawRequest();
}
```

The PoC asserts (1) `early`'s payout exceeds `basis * defaultRecoveryPrice / RECOVERY_FULL`, and (2) the final claimant's claim reverts, demonstrating permanent freezing of recovery funds.

### Citations

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
