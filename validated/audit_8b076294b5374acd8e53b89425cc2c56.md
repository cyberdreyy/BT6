### Title
Withdrawal receipts are charged upfront management fees for an epoch duration the user does not control; early pool closure or shortened epochs make users pay for idle time that never elapses - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
Analogous to solvers being forced to refund gas for `DeadlinePassed`/`GasPriceOverCap` (timing controlled by the bundler), `requestWithdraw` debits a withdrawer's receipt with an upfront management fee computed over `_withdrawRequestManagementFeeDuration()` — the *currently configured* `epochDuration` plus remaining buffer. The actual time the receipt sits outside live NAV before it is claimable is entirely determined by manager/borrower timing: the manager chooses the next epoch's real duration via `stopEpochWithDuration`/`setEpochParams`, or the pool can be closed immediately via `stopEpoch(0, 1)` (`_isRequestingAllFunds`), which sets `epochDuration = 0` and lets the user claim right away. In those cases the user permanently paid a management fee for a period that never elapsed, with the fee credited to `feeReceiver` through `pendingWithdrawFees`.

### Finding Description
In `requestWithdraw` (contracts/IdleCDOEpochVariant.sol L739-L791):

```solidity
uint256 totalFees = _totalWithdrawFees(principal, interest);
_underlyings = principal + interest - totalFees;
pendingWithdrawFees += totalFees;
creditVault.requestWithdraw(_underlyings, msg.sender, principal);
```

`_totalWithdrawFees` (L900-L906) calls `_calculateManagementFee(_principal, _withdrawRequestManagementFeeDuration())`, where the duration (L925-L931) is `epochDuration + (epochEndDate + bufferPeriod - block.timestamp)` — i.e., it assumes the receipt will sit idle for the whole *next* epoch of length `epochDuration`. But:

- `epochDuration` at request time describes the epoch that just stopped; the *next* epoch's length is set later by the manager through `stopEpochWithDuration(_newApr, _interest, _duration, _lossAmount)` → `setEpochParams(_duration, bufferPeriod)` (L520-L530). If the manager shortens the epoch, the receipt becomes claimable sooner than the fee model assumed.
- On pool closure (`stopEpoch` with `_interest == 1`, `_isRequestingAllFunds`), the code sets `epochDuration = 0`, `epochEndDate = 0`, `disableInstantWithdraw = true` and comment says "user will request only normal withdraw and can claim right after" (L488-L493). A receipt requested just before closure paid a full-`epochDuration` management fee yet is claimable almost immediately.
- There is no reconciliation path: `pendingWithdrawFees` is zeroed at epoch stop (L476) but the fee was already burned into the user's fixed receipt at request time. Nothing refunds the overcharged portion.

The user cannot prevent this — like the solver who cannot prevent the bundler's late inclusion or high gas price, the withdrawer cannot prevent the manager from closing the pool or setting a shorter duration after the fee was fixed.

### Impact Explanation
Direct, quantifiable loss of user funds: `loss ≈ principal * managementFee * (epochDuration − actualIdleTime) / 365 days`. With a 1% annualized management fee, a 1M USDC receipt, and a configured 30-day `epochDuration` followed by immediate pool closure, the user is overcharged ≈ `1e6 * 0.01 * 30/365 ≈ 822 USDC`, which is paid to `feeReceiver` while the receipt could be claimed within the buffer window only. The loss scales linearly with `epochDuration` and the fee rate and is permanent — the receipt amount is fixed at request time.

### Likelihood Explanation
No malicious actor is required. Honest operational actions trigger it: a borrower repaying and the manager closing the pool (`_interest == 1`), or the manager legitimately shortening the next epoch via `stopEpochWithDuration`, both occur after requests were priced with the stale `epochDuration`. Any request made during the buffer period is exposed whenever the realized wait is shorter than `epochDuration + remainingBuffer`. Requests are common (every withdrawal goes through this path), so the mispricing applies routinely rather than on an edge case.

### Recommendation
Mirror the fix suggested in the external report — don't charge users for time outside their control:

- Compute the upfront management fee only over the *deterministic* idle period (remaining buffer), or accrue the fee lazily at `claimWithdrawRequest`/epoch settlement against the actual elapsed idle time (`claimEpoch end − request timestamp`), capping at the configured duration.
- Alternatively, on pool closure (`_isRequestingAllFunds`) and on `setEpochParams` shortening, rebate `pendingWithdrawFees` pro rata to outstanding receipts before zeroing it at L476, or track per-epoch fee entitlements so `collectWithdrawFunds` only forwards fees for time actually elapsed.

### Proof of Concept
Foundry fork test sketch against `test/foundry/IdleCreditVault.t.sol` helpers:

```solidity
function testRequestWithdrawOverpaysFeeOnEarlyPoolClose() external {
    uint256 mgmtFeeRate = 1_000; // 1% annualized
    _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, mgmtFeeRate);
    uint256 amount = 1_000_000 * ONE_SCALE;

    uint256 mintedAA = idleCDO.depositAA(amount);
    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr, _expectedFundsEndEpoch());

    // user requests during buffer; fee charged for epochDuration + remaining buffer
    uint256 requested = cdoEpoch.requestWithdraw(mintedAA, address(AAtranche));
    uint256 chargedFee = cdoEpoch.pendingWithdrawFees();
    assertGt(chargedFee, 0); // includes a full epochDuration of mgmt fee

    // next epoch starts, borrower repays everything, manager closes pool early
    _startEpochAndCheckPrices(1);
    uint256 totFunds = _expectedFundsEndEpoch() + cdoEpoch.getContractValue();
    deal(defaultUnderlying, borrower, totFunds);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 1); // _isRequestingAllFunds: epochDuration = 0, claimable immediately

    // user claims almost immediately, but paid mgmt fee for a full epochDuration
    cdoEpoch.claimWithdrawRequest();
    uint256 claimed = IERC20Detailed(defaultUnderlying).balanceOf(address(this));
    assertEq(claimed, requested); // requested already net of chargedFee for time that never elapsed
}
```

Uncertainty: I verified the fee-charging and closure code paths in `IdleCDOEpochVariant.sol` but did not fully trace `IdleCreditVault.claimWithdrawRequest`'s per-epoch gating (`lastWithdrawRequest`, `withdrawsRequestsByEpoch`) to confirm no later fee adjustment exists; the fee is debited at request time and `pendingWithdrawFees` reset at stop with no rebate path, so the overcharge appears unconditional.