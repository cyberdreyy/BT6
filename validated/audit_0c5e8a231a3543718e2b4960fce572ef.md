Let me check the vault claim paths for a permanently-reverting condition an attacker could trigger.### Title
A single APR0 withdraw request permanently bricks `stopEpoch` after any APR change — vault-wide freeze of deposits - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`IdleCreditVault.prepareStopEpochWithApr0` unconditionally reverts with `NotAllowed` whenever `apr0TotalPrincipal != 0` and `unscaledApr != 0` (`IdleCreditVault.sol:499-508`). An unprivileged tranche holder can open an APR0 withdraw receipt while `unscaledApr == 0`; if the owner/manager later sets a nonzero APR (a normal, honest operation between epochs), every subsequent `stopEpoch`/`stopEpochWithDuration` reverts inside `prepareStopEpochWithApr0`, so the current epoch can never close and no further deposits, withdraw processing, or claims can proceed. Like CVE-2019-18217's overly long command that puts the process into an infinite loop, a single attacker-crafted request puts the epoch state machine into a state it can never leave.

### Finding Description
- When `unscaledApr == 0`, `requestWithdraw` routes principal into `_requestWithdrawApr0`, which increments `apr0Users[_user].principal` and the global `apr0TotalPrincipal` (`IdleCreditVault.sol:285-286`).
- `prepareStopEpochWithApr0` is called by `IdleCDOEpochVariant` during `stopEpoch`. It loads `_principal = apr0TotalPrincipal` and, if nonzero, enforces `if (unscaledApr != 0) revert NotAllowed();` before it ever reaches the `apr0TotalPrincipal = 0` cleanup at line 540 (`IdleCreditVault.sol:499-508,540`).
- There is no user-facing or privileged path that decrements `apr0TotalPrincipal` while APR is nonzero. The only decrements are inside `_clearWithdrawClaimForEpoch` (`IdleCreditVault.sol:821-831`), which runs on claim — but a claim requires `epochNumber > lastWithdrawRequest[_user]`, i.e., requires `stopEpoch` to succeed first. Circular dependency: the revert blocks the very epoch transition that would allow the receipt to be cleared.
- The attacker needs no privilege: any KYC-passing lender holding tranche tokens calls `cdoEpoch.requestWithdraw(amount, tranche)` (directly or via `IdleCDOEpochQueue.requestWithdraw` + `processWithdrawRequests`) while the vault APR is 0.

### Impact Explanation
Permanent or long-lived freezing of all vault funds. Once `apr0TotalPrincipal > 0` and `unscaledApr != 0`, `stopEpoch` always reverts, so: queued deposits/withdrawals are stuck, tranche holders cannot redeem (virtual price/claims all depend on epoch progression), and borrower repayment cannot be booked. The loss is bounded only by whatever rescue path exists (borrower default finalization / emergency shutdown, which itself may route through the same stop flow), and the stuck principal can be arbitrarily small — a dust APR0 request is enough to arm the revert.

### Likelihood Explanation
High for any vault that ever operates in APR0 mode. The sequence — user requests withdraw at APR=0, manager later announces/sets a nonzero APR for a subsequent epoch — is an ordinary operational transition, not an exotic state. The attacker only needs to leave one open APR0 receipt (no minimum size) outstanding across that transition. No malicious privileged role is required; the guard itself is the bug because it reverts instead of skipping APR0 accounting when APR is nonzero.

### Recommendation
In `prepareStopEpochWithApr0`, do not revert on `unscaledApr != 0`. Instead, treat APR0 principal as settled at zero interest for that epoch: still run `pendingWithdraws += 0`, skip `apr0RateByEpoch` (leave it 0), and always execute `apr0TotalPrincipal = 0` to close the bucket. Alternatively, add an owner/manager function to force-close the APR0 bucket (zeroing `apr0TotalPrincipal` and marking each `apr0Users[_user].principal` settled with `apr0RateByEpoch[principalEpoch] = 0`) so claims can proceed and unblock the epoch machine.

### Proof of Concept
```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
// Assumes the repo's existing foundry setUp with cdoEpoch, strategy (IdleCreditVault),
// queue, tranche, underlying, manager, owner wired as in test/foundry/IdleCDOEpochQueue.t.sol

contract Apr0StopEpochBrickPoC is Test {
    function test_apr0RequestThenAprChangeBricksStopEpoch() external {
        // --- epoch phase: running, APR0 mode ---
        // 1. user deposits, gets tranches (helper from existing tests)
        uint256 tranches = _depositWithUser(attacker, 100e6);

        // 2. while unscaledApr == 0, attacker requests withdraw
        vm.prank(attacker);
        cdoEpoch.requestWithdraw(tranches, address(tranche));
        assertGt(strategy.apr0TotalPrincipal(), 0, "apr0 bucket armed");

        // 3. epoch ends; manager sets NONZERO apr for next epoch (honest action)
        vm.prank(manager);
        cdoEpoch.setApr(10e18); // or equivalent apr setter used in tests

        // 4. EVERY stopEpoch path now reverts inside prepareStopEpochWithApr0
        vm.warp(cdoEpoch.epochEndDate() + 1);
        vm.prank(manager);
        vm.expectRevert(NotAllowed.selector);
        cdoEpoch.stopEpoch(0, 0); // same for stopEpochWithDuration

        // 5. attacker cannot self-clear: claim reverts because epochNumber
        //    <= lastWithdrawRequest[attacker]
        vm.prank(attacker);
        vm.expectRevert(NotAllowed.selector);
        cdoEpoch.claimWithdrawRequest();

        // funds frozen: epochNumber never increments, no claims/deposits proceed
        assertEq(strategy.epochNumber(), epochBefore, "epoch never closes");
    }
}
```

Note: the exact setter name for `unscaledApr` (e.g., `setApr`) and the `stopEpoch` signature should be matched to `IdleCDOEpochVariant.sol` / `IdleCreditVault.sol`; the revert site at `IdleCreditVault.sol:506-508` and the cleanup at line 540 are the load-bearing lines.