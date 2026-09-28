### Title
Withdrawal-request management fee ignores actual buffer overrun: requests made after `epochEndDate + bufferPeriod` are charged only `epochDuration` of fees regardless of how long the receipt is really locked - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
The bug class in the external report is a timestamp/duration calculation that uses a stale scheduled boundary (`depositTime + expiration`) instead of the real remaining/extension time, producing a wrong duration. The direct analog lives in `IdleCDOEpochVariant._withdrawRequestManagementFeeDuration`, which computes the upfront management-fee custody duration as `epochDuration + (epochEndDate + bufferPeriod - block.timestamp)`. Once the scheduled buffer end has passed, the "remaining buffer" term silently drops to zero, so a withdrawal request submitted during a delayed buffer period is charged management fees only for `epochDuration`, even though the receipt remains locked and unclaimable for the full real delay until the manager eventually calls `startEpoch`, plus the next epoch.

### Finding Description
`requestWithdraw` burns tranche tokens and fixes a receipt that leaves live NAV but can only be claimed after the next `stopEpoch` settles it (`claimWithdrawRequest` → `IdleCreditVault.claimWithdrawRequest`). Because the funds sit outside NAV for the entire waiting time, the protocol charges an upfront management fee via `_totalWithdrawFees`, which calls `_withdrawRequestManagementFeeDuration`:

```solidity
// contracts/IdleCDOEpochVariant.sol
function _withdrawRequestManagementFeeDuration() private view returns (uint256 _duration) {
    uint256 bufferEnd = epochEndDate + bufferPeriod;
    _duration = epochDuration;
    if (block.timestamp < bufferEnd) {
      _duration += bufferEnd - block.timestamp;
    }
}
```

The documented invariant ("Receipts leave live NAV at request time but can be claimed only after the next epoch settles. Requests made during the buffer also pay for the remaining buffer time before that next epoch can start") ties the fee to the actual lock-up time. However:

- `startEpoch` only *permits* starting after `epochEndDate + bufferPeriod` (`_checkNotAllowed(block.timestamp < (epochEndDate + bufferPeriod))`); nothing forces the manager to call it at exactly that second. Any overrun — keeper latency, gas spikes, operational delay — extends the real lock-up.
- If a requester submits `requestWithdraw` at `bufferEnd + D`, the `if (block.timestamp < bufferEnd)` branch is skipped and the fee duration collapses to `epochDuration`, while the receipt still cannot be claimed until `stopEpoch` after a `startEpoch` that happens at `bufferEnd + D + furtherDelay` at the earliest — i.e. the receipt is locked `D (+furtherDelay) + epochDuration` in reality but pays fees for `epochDuration` only.

This mirrors the OpenQ `extendDeposit` defect: the code recomputes a deadline/duration from a scheduled boundary (`epochEndDate + bufferPeriod`, analogous to `depositTime + expiration`) rather than the actual elapsed/remaining time, so the produced number no longer matches the real duration the economic logic assumes.

### Impact Explanation
Every KYC-passing lender can time `requestWithdraw` into the overrun window and pay `principal * managementFee * (epochDuration)/365 days` instead of `principal * managementFee * (epochDuration + realDelay)/365 days`. The unpaid amount `principal * managementFee * delay / YEAR` is deducted from `pendingWithdrawFees` that would otherwise go to `feeReceiver`/`owner` at `stopEpoch`, i.e. direct loss of protocol fee revenue scaled by the delay and the withdrawn principal. With `managementFee` up to `MAX_FEE/10` and no cap on the delay, an attacker requesting a large withdrawal during a multi-day overrun skips a proportional fee that a request submitted seconds earlier (before `bufferEnd`) would have paid. There is also a mild fairness break: two requesters with identical lock-up horizons pay different fees depending only on whether the wall clock happened to pass the scheduled `bufferEnd`.

### Likelihood Explanation
Low-to-medium. It requires (a) `managementFee > 0`, (b) the honest manager delaying `startEpoch` past `epochEndDate + bufferPeriod` — a normal operational occurrence, not malicious behavior — and (c) a KYC'd tranche holder submitting `requestWithdraw` during the overrun. `requestWithdraw` is enabled in the buffer (`allowAAWithdrawRequest`/`allowBBWithdrawRequest` are true after `stopEpoch`), the `isWalletAllowed` KYC check is satisfiable by an ordinary lender, and no skim/flag/Default guard intercepts the path. The loss is bounded by the fee rate and overrun length, so per-event magnitude is modest, consistent with a Medium/Low.

### Recommendation
Compute the fee duration from the actual claimable horizon rather than the scheduled buffer end. Options:

1. Charge for time actually waited by accruing the management fee at claim time in `IdleCreditVault.claimWithdrawRequest` (or at `stopEpoch` when funding the receipts) using `block.timestamp - requestTimestamp`, instead of an upfront estimate.
2. If the fee must stay upfront and a `stopEpoch`-relative horizon is preferred, record the real `startEpoch` timestamp (or extend `epochEndDate`/the fee base when `startEpoch` is called late) so that `_withdrawRequestManagementFeeDuration` uses `actualNextStart - requestTime + epochDuration`, not `scheduled bufferEnd - now` clamped at zero.

At minimum, document that fees cover only the *minimum* lock-up (`epochDuration`), in which case the remaining-buffer term should be removed for consistency.

### Proof of Concept
Foundry fork-style test against the existing `IdleCreditVault.t.sol` harness shape:

```solidity
function testRequestWithdrawFeeUnderchargeOnDelayedStart() external {
    _setManagementFee(10_000); // 10% annualized, within MAX_FEE/10 cap

    uint256 amount = 100_000 * ONE_SCALE;
    idleCDO.depositAA(amount);

    // epoch 1 runs and stops normally
    _startEpochAndCheckPrices(0);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    // attacker A requests just before scheduled buffer end
    address early = makeAddr('early');
    idleCDO.depositAA(1 * ONE_SCALE); // keep vault live; assume `early` holds tranche tokens
    vm.warp(cdoEpoch.epochEndDate() + cdoEpoch.bufferPeriod() - 1);
    uint256 feesBefore = cdoEpoch.pendingWithdrawFees();
    vm.prank(early);
    cdoEpoch.requestWithdraw(0, AA);

    // manager does NOT call startEpoch at bufferEnd; buffer overruns by `delay`
    uint256 delay = 30 days;
    vm.warp(cdoEpoch.epochEndDate() + cdoEpoch.bufferPeriod() + delay);

    // attacker B requests the same size during the overrun
    address late = makeAddr('late');
    vm.prank(late);
    cdoEpoch.requestWithdraw(0, AA);

    // `late`'s receipt is locked for delay + epochDuration + ... in reality,
    // but _withdrawRequestManagementFeeDuration() returned only epochDuration.
    // Assertion: management fee embedded in pendingWithdrawFees for `late`
    // equals principal * mFee * epochDuration / YEAR,
    // while the protocol-intended charge is principal * mFee * (delay + epochDuration) / YEAR.
    // => feeReceiver loses principal * mFee * delay / YEAR.
}
```

The expectation helper `_withdrawRequestManagementFeeDuration` in `test/foundry/IdleCreditVault.t.sol` (lines ~3606-3616) already encodes the same `bufferEnd - block.timestamp` clamp, confirming the behavior is deterministic and reproducible: after `bufferEnd` the fee duration is exactly `epochDuration` regardless of the real delay until `startEpoch` and the subsequent `stopEpoch` that makes the receipt claimable.