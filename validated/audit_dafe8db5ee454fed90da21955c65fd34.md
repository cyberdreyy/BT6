### Title
`restoreOperations` re-arms deposit/withdraw flows on a permanently closed pool, letting a KYC'd user drain the receipt reserve and freeze pre-close claims - ([File: contracts/IdleCDOEpochVariant.sol])

### Summary
The kernel bug is a re-schedule-after-cancel: teardown cancels async work, a later step re-arms it, and it runs against freed state. The direct analog lives in `IdleCDOEpochVariant.restoreOperations()`. A healthy pool close via `stopEpoch(_newApr, 1)` permanently terminates the pool (`epochDuration = 0`, `epochEndDate = 0`; `startEpoch` then reverts forever on the `_epochDuration == 0` check at line 239). However, `restoreOperations()` only refuses to run when `defaulted || priceAA == 0` (line 622), so on a healthy closed pool it unpauses and re-enables `allowAAWithdrawRequest`/`allowBBWithdrawRequest` — re-arming the deposit and withdrawal-request machinery on a dead pool that will never start another epoch.

### Finding Description
`restoreOperations` at `contracts/IdleCDOEpochVariant.sol:619-632`:

```solidity
_checkNotAllowed(defaulted || priceAA == 0);
skipDefaultCheck = false;
if (isEpochRunning) return;
if (paused()) { _unpause(); }
allowAAWithdrawRequest = true;
allowBBWithdrawRequest = true;
```

There is no `epochDuration == 0` (closed-pool) guard. After a healthy close, an attacker who is a KYC-passing lender (`isWalletAllowed`) can:

1. Call `depositAA`/`depositBB` — `_deposit` only requires `whenNotPaused` and a wallet check (lines 643-650), so deposits succeed on the closed pool.
2. Call `cdoEpoch.requestWithdraw(...)` — in `IdleCreditVault.requestWithdraw` (`contracts/strategies/idle/IdleCreditVault.sol:259-280`) the `isClosed` branch (`epochEndDate == 0`) skips the `pendingWithdraws += _amount` accounting that would normally obligate a future `stopEpoch` to fund the receipt.
3. Call `claimWithdrawRequest()` immediately — `_claimFundedWithdrawRequest` skips the "wait one epoch" check precisely when `epochEndDate == 0` (line 326), and `_transferFundedClaim` pays out from the strategy's reserve.

That reserve is the cash the borrower repaid at close, earmarked for the pre-close pending receipts and instant-withdraw claimants. Each post-close deposit→request→claim cycle pays the attacker at par out of that reserve while their deposited underlying stays stranded in the CDO (later skimmed to `feeReceiver`). Repeating the loop drains the reserve; subsequent legitimate `claimWithdrawRequest`/`claimInstantWithdrawRequest` calls by pre-close receipt holders revert on underflow — their already-funded payouts are permanently unclaimable.

### Impact Explanation
Permanent freezing/effective theft of unclaimed, already-funded withdrawal receipts belonging to honest users. The reserve funded at `stopEpoch(0, 1)` is bounded; once consumed by post-close claims, ` _transferFundedClaim` reverts for every remaining receipt holder. Loss is quantified: up to the full reserve balance owed to outstanding claimants at close.

### Likelihood Explanation
Requires only the honest owner to call `restoreOperations()` after a close — a plausible operational action since the function's documented purpose is resuming service, and nothing signals that a closed pool must never be "restored". The attacker is an unprivileged KYC'd lender; no privileged misbehavior, no oracle manipulation, no timing race. All existing guards (`_checkNotAllowed(defaulted || priceAA == 0)`, `_beforeUnpause`'s `skipDefaultCheck` check, `startEpoch`'s closed-pool revert) fail to cover this state.

### Recommendation
In `IdleCDOEpochVariant.restoreOperations()`, also revert when `epochDuration == 0` (pool permanently closed), e.g. `_checkNotAllowed(defaulted || priceAA == 0 || epochDuration == 0)`. Alternatively, keep deposits/requests disabled whenever `epochEndDate == 0 && epochDuration == 0` inside `_deposit` and `requestWithdraw`.

### Proof of Concept
```solidity
// test/foundry/RestoreOperationsClosedPool.t.sol
function testRestoreOperationsOnClosedPoolDrainsReserve() external {
    uint256 amount = 10_000 * ONE_SCALE;
    address victim = makeAddr('victim');
    address attacker = makeAddr('attacker-kyc');

    _depositWithUser(victim, amount, true);
    // victim opens a withdraw request that will be funded at close
    vm.prank(victim);
    uint256 victimReceipt = cdoEpoch.requestWithdraw(amount / 2, address(AAtranche));

    _startEpochAndCheckPrices(0);

    // borrower repays everything; pool closes permanently
    uint256 totFunds = _expectedFundsEndEpoch() + cdoEpoch.getContractValue();
    deal(defaultUnderlying, borrower, totFunds);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 1);
    assertEq(cdoEpoch.epochEndDate(), 0);

    // honest owner restores ops: re-arms deposit/withdraw on a dead pool
    vm.prank(owner);
    cdoEpoch.restoreOperations();
    assertFalse(cdoEpoch.paused());

    // KYC'd attacker cycles deposit -> request -> claim, draining reserve
    uint256 atk = victimReceipt; // sized to consume the reserve
    deal(defaultUnderlying, attacker, atk);
    vm.startPrank(attacker);
    underlying.approve(address(idleCDO), atk);
    idleCDO.depositAA(atk);
    cdoEpoch.requestWithdraw(0, address(AAtranche));
    cdoEpoch.claimWithdrawRequest(); // paid at par from victim's reserve
    vm.stopPrank();

    // victim's pre-close funded receipt is now unclaimable
    vm.prank(victim);
    vm.expectRevert(); // underflow / insufficient reserve
    cdoEpoch.claimWithdrawRequest();
}
```