### Title
Cross-epoch instant-withdraw receipts are recorded per epoch but never included in default-recovery basis, leaving them unfunded and unclaimable - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault` tracks instant-withdraw receipts per user and per epoch (`instantWithdrawsRequestsByEpoch`, `instantWithdrawClaimsByEpoch`), mirroring the external report's "delegation is stored but never consumed" bug class: when a default is finalized, `defaultPendingClaimBasis()`, `_defaultPrefundedInstantReserve()`, and `_claimDefaultedInstantWithdrawRequest()` only ever read the bucket for the *current* `epochNumber` / `defaultRecoveryEpoch`. An instant receipt that was requested in an earlier epoch and remained unfunded across a `stopEpoch` is permanently stored under the old epoch index and is ignored by every recovery path — yet it still counts in the aggregate `pendingInstantWithdraws` and `instantWithdrawsRequests[user]`.

### Finding Description
`requestInstantWithdraw` records three things: `instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch[_user][currentEpoch]`, and `instantWithdrawClaimsByEpoch[currentEpoch]` [1](#0-0) . The per-epoch aggregates are only decremented in `_claimDefaultedInstantWithdrawRequest` for `defaultRecoveryEpoch` [2](#0-1) ; a normally funded claim (`claimInstantWithdrawRequest`) never clears the per-epoch entries, and `collectInstantWithdrawFunds` only decreases the aggregate `pendingInstantWithdraws` [3](#0-2) .

When the borrower defaults and `finalizeDefaultRecovery` runs, the pending-claim basis is computed as `pendingWithdraws + instantWithdrawClaimsByEpoch[epochNumber]` — only the current epoch's instant bucket [4](#0-3) . The prefunded-reserve calculation has the same single-epoch blind spot: it compares `instantWithdrawClaimsByEpoch[epochNumber]` against the *aggregate* `pendingInstantWithdraws`, which still contains older-epoch unfunded receipts [5](#0-4) .

Attack/loss sequence:

1. Epoch N (running, instant-withdraw mode): user requests an instant withdraw; `instantWithdrawClaimsByEpoch[N] = X`, `pendingInstantWithdraws = X`. The CDO collects only part (or none) via `collectInstantWithdrawFunds`, leaving `pendingInstantWithdraws = X` with the basis parked under epoch N.
2. `stopEpoch` bumps `epochNumber` to N+1 and `startEpoch` opens a new epoch; the old unfunded instant receipt is carried in the aggregate counter but is now indexed under a stale epoch key — the delegation-style "saved but orphaned" state.
3. Epoch N+1: borrower repays nothing, `stopEpoch` marks `defaulted()`, manager calls `finalizeDefaultRecovery`. `totalBasis` includes only `instantWithdrawClaimsByEpoch[N+1]` (the old X is excluded), while `_defaultPrefundedInstantReserve` subtracts the full aggregate `pendingInstantWithdraws` (which still includes X) — so the reserve math treats funds earmarked for the old receipt as covering current-epoch claims or drops the basis entirely.
4. After finalization, the old receipt's claim goes through `claimInstantWithdrawRequest` → `_transferFundedClaim`, which either pays it at par out of the recovery reserve's backing (the `balance - reserve >= amount` guard only protects `defaultRecoveryReserve`, so if balance > reserve it steals reserve-adjacent funds) or reverts `NotAllowed()` once only reserve funds remain, permanently freezing the user's receipt. Meanwhile `recoveryPrice` was computed against a basis that excluded X, so every genuine recovery claimant's payout is mispriced — diluted if the old receipt is paid, or the reserve is stranded dust if it cannot be.

Broken invariant: one receipt one payout / fair recovery distribution — recorded claim basis never enters `totalBasis`, while the aggregate counters still reserve for it.

Existing guards do not stop it: `_ensureDefaultRecoveryInitialized` only blocks initialization when `pendingInstantWithdraws != 0` [6](#0-5) , and nothing prevents `stopEpoch`/`startEpoch` from rolling the epoch while instant receipts remain pending, so the per-epoch key goes stale in normal operation.

### Impact Explanation
An unprivileged tranche holder with an instant-withdraw receipt can have their claim permanently frozen (claim reverts once only recovery reserve remains) or, if paid, drain underlying that was reserved for finalized recovery claimants, producing a direct shortfall for other users. The recovery price is computed over a basis that silently omits real liabilities, so recovered funds are distributed pro-rata to the wrong claimant set — quantifiable loss equals the orphaned instant receipt amount X.

### Likelihood Explanation
Requires an instant request to remain unfunded across an epoch boundary and a subsequent borrower default — an operational sequence, not attacker-privileged action. The attacker is simply a tranche-token holder using `requestInstantWithdraw`; timing is created by honest manager/borrower flows (partial instant funding, epoch roll, then default). Medium likelihood conditional on default, but the accounting gap exists unconditionally.

### Recommendation
Track instant-withdraw claim basis per user across all outstanding epochs (or keep a single aggregate `instantWithdrawClaimBasis` alongside `pendingInstantWithdraws`), and use that aggregate in `defaultPendingClaimBasis`, `_defaultPrefundedInstantReserve`, and the defaulted-claim path rather than indexing only `epochNumber`/`defaultRecoveryEpoch`. Alternatively block epoch rollover while `pendingInstantWithdraws != 0`, matching the initialization guard.

### Proof of Concept
```solidity
// Foundry fork PoC sketch (setup mirrors test/foundry/IdleCreditVault.t.sol helpers)
function testOrphanedInstantReceiptExcludedFromRecovery() external {
    uint256 amount = 10_000 * ONE_SCALE;
    _depositWithUser(user, amount, true);

    // Epoch N: user requests instant withdraw; only partially funded
    vm.prank(user);
    cdoEpoch.requestInstantWithdraw(amount / 2, address(AAtranche)); // strategy: pendingInstantWithdraws = X
    // manager collects 0 via collectInstantWithdrawFunds -> X stays pending under epoch N

    _stopEpochAndCheckPrices(0, initialProvidedApr, 0); // epochNumber -> N+1
    vm.prank(manager);
    cdoEpoch.startEpoch();

    // Epoch N+1 defaults
    deal(defaultUnderlying, borrower, 0);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);
    assertTrue(cdoEpoch.defaulted());

    // Finalize recovery covering active basis + current-epoch basis only
    uint256 activeBasis = cdoEpoch.getContractValue() + cdoEpoch.expectedEpochInterest()
        - cdoEpoch.pendingWithdrawFees();
    deal(defaultUnderlying, manager, activeBasis);
    vm.startPrank(manager);
    underlying.approve(address(strategy), activeBasis);
    cdoEpoch.finalizeDefault(activeBasis, manager);
    vm.stopPrank();

    // The old instant receipt's basis X was never added to totalBasis:
    // defaultPendingClaimBasis used instantWithdrawClaimsByEpoch[N+1] == 0
    // while pendingInstantWithdraws still carried X into _defaultPrefundedInstantReserve.

    // User's instant claim now either steals reserve backing or reverts:
    vm.prank(user);
    vm.expectRevert(); // NotAllowed once only defaultRecoveryReserve remains
    cdoEpoch.claimInstantWithdrawRequest();
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L843-853)
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
