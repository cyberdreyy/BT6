### Title
Oversized `instantWithdrawDelay` permanently bricks `stopEpoch` and freezes all vault funds once an instant withdrawal request is pending - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
`setInstantWithdrawParams` performs no bound check on `_delay`. If `instantWithdrawDelay` is set to a value far beyond the epoch duration (e.g., 50 years) and an epoch starts with any pending instant withdrawal request that isn't fully covered by CDO-held underlyings, the protocol is permanently deadlocked: `getInstantWithdrawFunds` reverts until `instantWithdrawDeadline` (`startEpoch` timestamp + delay) is reached, `pendingInstantWithdraws` can never be cleared, and `stopEpoch` reverts while `_pendingInstant() != 0`. Since the contract is paused during an epoch, the setter cannot be called to fix the delay mid-epoch. All tranche holders' funds are frozen.

### Finding Description
The analog to `grace >= expiry` disabling liquidations is the pair `instantWithdrawDelay` / epoch lifecycle:

1. `setInstantWithdrawParams` (contracts/IdleCDOEpochVariant.sol:131-137) accepts any `_delay` with only `paused()` and caller checks. No relation to `epochDuration` is enforced.
2. `startEpoch` (line 268) sets `instantWithdrawDeadline = block.timestamp + instantWithdrawDelay`. For large but non-overflowing delays this is a timestamp decades in the future.
3. `getInstantWithdrawFunds` (line 561) reverts unless `isEpochRunning && block.timestamp >= instantWithdrawDeadline`. This is the only non-default path that calls `collectInstantWithdrawFunds` (IdleCreditVault.sol:398-403), which decrements `pendingInstantWithdraws`.
4. `_stopEpoch` (line 345) reverts while `_pendingInstant() != 0`.

So if `pendingInstantWithdraws > 0` at epoch start and the instant queue is underfunded by contract underlyings (`pendingInstant > totUnderlyings`, line 286-290, which takes the early return without setting `allowInstantWithdraw`), the only way to fund the remainder is `getInstantWithdrawFunds`, which is unreachable until the deadline. `stopEpoch` is therefore unreachable, `isEpochRunning` stays true, deposits stay paused, `allowAAWithdrawRequest`/`allowBBWithdrawRequest` stay false, and no withdrawal path exists.

Confirming no escape hatch:
- `setInstantWithdrawParams` reverts while paused (line 133), and `startEpoch` pauses the contract (line 246), so the delay cannot be corrected mid-epoch. `_beforeUnpause` (line 636) blocks unpause while `isEpochRunning`.
- `_ensureDefaultRecoveryInitialized` (IdleCreditVault.sol:926) reverts when `pendingInstantWithdraws != 0`.
- `_claimDefaultedInstantWithdrawRequest` only runs after `finalizeDefaultRecovery`, which requires an actual borrower repayment failure; honest borrowers repaying normally cannot reach it.
- `transferToken` (IdleCreditVault.sol:963) could move underlying but cannot repair `pendingInstantWithdraws` accounting.

### Impact Explanation
Permanent (for the duration of the configured delay) freezing of all vault TVL. With `instantWithdrawDelay = 50 years`, every AA/BB tranche holder's principal and accrued interest is locked for ~50 years; for practical purposes this equals a total loss of all deposited funds — identical in spirit to the external report's permanently disabled liquidation causing insolvency, except here it is the redemption/epoch-settlement mechanism that is bricked.

### Likelihood Explanation
Requires an honest owner/manager to set an excessive `instantWithdrawDelay` (fat-finger, wrong units — e.g., milliseconds vs seconds — would trivially produce a multi-century delay). From that point an unprivileged user (KYC-passing lender) triggers the deadlock themselves: when APR drops (`lastEpochApr > unscaledApr + instantWithdrawAprDelta`), their `requestWithdraw` is routed to `requestInstantWithdraw` (lines 761-769), creating `pendingInstantWithdraws` the borrower cannot fund before the deadline. No privileged actor acts maliciously; the lender just uses the normal flow.

### Recommendation
In `setInstantWithdrawParams`, require `_delay <= epochDuration` (and reject `epochDuration == 0`), or add a sane upper bound (e.g., `MAX_INSTANT_WITHDRAW_DELAY`). Additionally, allow `stopEpoch` to proceed when `block.timestamp >= epochEndDate` even with pending instant withdrawals by treating unfunded instant receipts through the existing loss/default path, so a misconfiguration cannot deadlock the epoch state machine.

### Proof of Concept
```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "./IdleCreditVault.t.sol"; // reuse existing fork test harness

contract InstantDelayDeadlockTest is IdleCreditVaultTest {
    function testOversizedInstantDelayBricksStopEpoch() external {
        uint256 amount = 10_000 * ONE_SCALE;

        // 1. Honest owner misconfigures: delay of 50 years
        vm.prank(owner);
        cdoEpoch.setInstantWithdrawParams(50 * 365 days, 1, false);

        // 2. Lender deposits during buffer
        idleCDO.depositAA(amount);

        // 3. Epoch 1 runs and stops; new APR is lowered so instant
        //    withdrawals trigger (lastEpochApr > unscaledApr + delta)
        _toggleEpoch(true, 0, 0);            // start
        vm.warp(cdoEpoch.epochEndDate() + 1);
        vm.prank(manager);
        cdoEpoch.stopEpoch(0, 0);            // stop, newApr = 0

        // 4. Unprivileged lender's requestWithdraw routes to instant path,
        //    creating pendingInstantWithdraws
        idleCDO.requestWithdraw(0, idleCDO.AATranche());
        assertGt(IdleCreditVault(strategy).pendingInstantWithdraws(), 0);

        // 5. Epoch 2 starts; instant queue is underfunded, early-return path
        vm.warp(cdoEpoch.epochEndDate() + cdoEpoch.bufferPeriod());
        vm.prank(manager);
        cdoEpoch.startEpoch();
        assertGt(IdleCreditVault(strategy).pendingInstantWithdraws(), 0);
        assertFalse(cdoEpoch.allowInstantWithdraw());

        // 6. Epoch ends; manager tries to stop -> reverts: pending instant
        vm.warp(cdoEpoch.epochEndDate() + 1);
        vm.prank(manager);
        vm.expectRevert(NotAllowed.selector);
        cdoEpoch.stopEpoch(0, 0);

        // 7. getInstantWithdrawFunds unreachable for ~50 years
        vm.prank(manager);
        vm.expectRevert(NotAllowed.selector);
        cdoEpoch.getInstantWithdrawFunds();

        // 8. Delay cannot be fixed: contract is paused during epoch
        vm.prank(owner);
        vm.expectRevert(NotAllowed.selector);
        cdoEpoch.setInstantWithdrawParams(1, 1, false);

        // stopEpoch is bricked for ~50 years -> all funds frozen
    }
}
```