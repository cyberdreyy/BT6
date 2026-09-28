### Title
Zero-amount withdraw request is silently dropped by IdleCreditVault while IdleCDOEpochVariant still burns tranche tokens and leaks `pendingWithdrawFees`/`interestForOverUnderPerformance` — (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The upstream bug releases no socket reference when a PSP policy check fails: a resource is acquired, an edge/failure path is taken, and the accounting is never unwound. The analog sits in `IdleCreditVault.requestWithdraw` (contracts/strategies/idle/IdleCreditVault.sol:243-246), which returns early when `_amount == 0` — without burning the CDO's `_principal` strategy tokens, minting a user receipt, or recording the request — while the caller `IdleCDOEpochVariant.requestWithdraw` (contracts/IdleCDOEpochVariant.sol:773-790) has already committed `pendingWithdrawFees += totalFees` and `interestForOverUnderPerformance += diff`, and then unconditionally executes `_withdrawOps(_amount, principal, _tranche)`, burning the user's tranche tokens and reducing NAV. The request "fails" (no receipt created) but both sides' references to it are partially kept: the user's principal is stranded in the strategy with no claim, and the leaked fee/interest deltas are still charged to the borrower at the next epoch boundary.

### Finding Description
`IdleCreditVault.requestWithdraw` bails out before any state change: [1](#0-0) 

Because the early `return` is silent (no revert), execution continues in `IdleCDOEpochVariant.requestWithdraw` after the strategy call, so `_withdrawOps` burns `_amount` tranche tokens and subtracts `principal` from the saved tranche NAV: [2](#0-1) 

`_underlyings = principal + interest - totalFees` can equal exactly zero while `principal > 0` whenever `_totalWithdrawFees` equals `principal + interest` — reachable through fee rounding on small requests (e.g., a 1-wei request where `_calculateManagementFee` rounds up to cover `principal`) or under a high `managementFee`/long `epochDuration + bufferPeriod` configuration where the upfront fee at contracts/IdleCDOEpochVariant.sol:900-906 consumes the whole requested amount. In that case:

1. No strategy-token receipt is minted and no `withdrawsRequests`/`withdrawsRequestsByEpoch`/`pendingWithdraws` entry is created — the burned principal's underlying backing remains stranded inside the strategy with no owner, so it can never be claimed (permanent freezing of the user's funds).
2. `pendingWithdrawFees` was incremented by `totalFees` for a receipt that does not exist. At the next `startEpoch` it is folded into `expectedEpochInterest` (contracts/IdleCDOEpochVariant.sol:260-262), forcing the borrower to repay it, and at `stopEpoch` it is paid out to fee receivers via `_transferFeeUnderlyings(_pendingWithdrawFees)` (contracts/IdleCDOEpochVariant.sol:418-420). The leaked fee is never released — exactly the "reference held on the failure path" shape of the kernel bug.
3. `interestForOverUnderPerformance` is likewise permanently skewed by `diff`, distorting the next epoch's `expectedEpochInterest` for all tranche holders.

### Impact Explanation
- Permanent freezing/loss of funds: the requester's `principal` underlyings are locked in `IdleCreditVault` forever — no receipt exists for anyone to claim them, and `pendingWithdraws` was never incremented so `stopEpoch`/`collectWithdrawFunds` never accounts for them. Loss = the full requested `principal` for each affected request.
- Theft of value from the honest borrower: `pendingWithdrawFees` and `interestForOverUnderPerformance` leaks are charged to the borrower as `expectedEpochInterest` and distributed to fee receivers/LPs even though no withdraw receipt was created. The inflated fee can exceed the burned principal (it includes `principal + interest`-scale management fees for `epochDuration + remaining buffer`), so the borrower overpays more than the requester's burned amount.
- Broken invariant: `pendingWithdrawFees` must equal fees on outstanding receipts; after this path it is positive with zero corresponding receipts.

### Likelihood Explanation
The trigger requires `principal + interest == totalFees` exactly (larger `totalFees` underflows and safely reverts). That equality is reachable by rounding on dust-sized requests regardless of fee configuration, and for larger amounts whenever the configured management fee over `epochDuration + bufferPeriod` approaches ~100% of principal plus projected interest — a legitimate (if aggressive) owner-set configuration, not attacker-controlled. An unprivileged KYC'd tranche holder triggers it via the ordinary `requestWithdraw` path in the buffer phase; in the post-default flow (`defaultRecoveryFinalized`) the same silent early return at IdleCreditVault.sol:246 also applies after `_ensureDefaultRecoveryInitialized`. Each occurrence strands the requester's principal and leaks fees, so the finding is real but the per-attack magnitude is bounded by the fee size when only dust requests satisfy the equality; likelihood of material loss depends on a high-fee pool configuration.

### Recommendation
In `IdleCDOEpochVariant.requestWithdraw`, revert (or skip all accounting) when the computed `_underlyings == 0` after fees — compute fees before mutating `pendingWithdrawFees`/`interestForOverUnderPerformance`, and only call `creditVault.requestWithdraw`/`_withdrawOps` for a non-zero receipt, e.g.:

```solidity
_underlyings = principal + interest - totalFees;
_checkNotAllowed(_underlyings == 0); // release nothing, keep no reference
pendingWithdrawFees += totalFees;
interestForOverUnderPerformance += diff;
```

Equivalently (defense in depth), make `IdleCreditVault.requestWithdraw` revert instead of returning silently on `_amount == 0`, so the caller's speculative fee/interest mutations are rolled back atomically — mirroring the upstream fix of releasing the acquired reference on the failure path.

### Proof of Concept
Foundry fork sketch (adapt the existing `IdleCreditVault.t.sol` harness `_depositWithUser`/`_stopEpochAndCheckPrices` helpers):

```solidity
function testZeroAmountRequestLeaksFeesAndStrandsPrincipal() external {
    // Setup: pool in buffer phase, positive managementFee, allow withdraw requests.
    address user = makeAddr('user');
    _depositWithUser(user, 100e6, true); // small deposit

    // Craft _amount such that principal + interest - totalFees == 0.
    // With dust tranche amount (e.g. 1 wei) fee rounding makes mgmtFee cover it.
    uint256 dustTranche = 1;
    uint256 feesPre = cdoEpoch.pendingWithdrawFees();

    vm.prank(user);
    cdoEpoch.requestWithdraw(dustTranche, address(AAtranche));

    // 1) Tranche tokens were burned and NAV decreased even though no receipt exists.
    assertEq(AAtranche.balanceOf(user), /* pre - dustTranche */);
    // 2) Strategy has NO record: no receipt minted, nothing pending.
    IdleCreditVault strat = IdleCreditVault(address(strategy));
    assertEq(strat.balanceOf(user), 0, 'no receipt minted');
    assertEq(strat.withdrawsRequests(user), 0, 'request silently dropped');
    assertEq(strat.pendingWithdraws(), 0, 'pendingWithdraws not incremented');
    // 3) pendingWithdrawFees leaked: fees for a non-existent receipt.
    assertGt(cdoEpoch.pendingWithdrawFees(), feesPre, 'fee counter leaked');

    // 4) At next startEpoch/stopEpoch the borrower is charged the leaked fee.
    vm.prank(manager);
    cdoEpoch.startEpoch();
    assertGe(cdoEpoch.expectedEpochInterest(), cdoEpoch.pendingWithdrawFees(), 'leaked fee charged to borrower');

    // 5) The user can never recover the burned principal: claim reverts/pays 0.
    _stopCurrentEpoch();
    uint256 balPre = underlying.balanceOf(user);
    vm.prank(user);
    cdoEpoch.claimWithdrawRequest();
    assertEq(underlying.balanceOf(user), balPre, 'principal permanently stranded');
}
```

Note: the zero-`payout` handling in `IdleCDOEpochQueue.claimWithdrawRequest` was inspected and is out of scope per the rules; the queue already clears receipts on zero-price epochs. The vulnerable asymmetry is exclusively in the CDO↔strategy `requestWithdraw` handshake described above.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L243-247)
```text
  function requestWithdraw(uint256 _amount, address _user, uint256 _principal) external {
    _onlyIdleCDO();
    _ensureDefaultRecoveryInitialized();
    if (_amount == 0) return;
    if (defaultRecoveryFinalized) {
```

**File:** contracts/IdleCDOEpochVariant.sol (L786-790)
```text
    // The receipt is fixed now and leaves live NAV. Charge management fees upfront
    // for the time it waits outside live NAV: remaining buffer plus the next epoch.
    creditVault.requestWithdraw(_underlyings, msg.sender, principal);
    // burn tranche tokens and decrease NAV without interest for the next epoch as it was not yet counted in NAV
    _withdrawOps(_amount, principal, _tranche);
```
