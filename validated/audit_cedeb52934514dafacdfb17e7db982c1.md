### Title
`requestWithdraw` treats a never-started pool as a closed pool because `epochEndDate == 0` is used as the "closed" sentinel — pre-first-epoch withdrawal receipts are never funded - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.requestWithdraw` decides whether the pool is "closed" by checking `IIdleCDOEpochVariant(idleCDO).epochEndDate() == 0`. That sentinel is ambiguous: `epochEndDate` is 0 both after a successful pool close and before the very first epoch is ever started (it is only written in `startEpoch`, `IdleCDOEpochVariant.sol:266`). Before the first `startEpoch`, a KYC'd lender who deposits during the buffer phase and then requests a withdrawal is silently routed onto the "closed pool" path: `pendingWithdraws` is not incremented and the APR0 bucket is skipped. Because `pendingWithdraws` is the global basis `stopEpoch` uses to source funds from the borrower/strategy (`collectWithdrawFunds`, `previewLossAdjustedWithdrawFunds`), these receipts are never funded, so the user's claim can never be paid even after multiple epochs complete.

### Finding Description
In `requestWithdraw` (contracts/strategies/idle/IdleCreditVault.sol:259):

```solidity
bool isClosed = IIdleCDOEpochVariant(idleCDO).epochEndDate() == 0;
...
if (!isClosed) {
  pendingWithdraws += _amount;
}
lastWithdrawRequest[_user] = currentEpoch; // == 0 for the first epoch
if (unscaledApr == 0 && !isClosed) {
  _requestWithdrawApr0(_amount, _user);
} else {
  withdrawsRequests[_user] += _amount;
  withdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
}
```

`epochEndDate` is set only in `IdleCDOEpochVariant.startEpoch` (`epochEndDate = block.timestamp + _epochDuration`) and cleared back to 0 when the pool is successfully closed via the `_interest == 1` sentinel path. Therefore `isClosed` is true in two semantically different states:

1. A pool that ran epochs and was closed (all funds recalled — correct to skip `pendingWithdraws`).
2. A fresh pool still in its initial buffer phase, before the first `startEpoch` — deposits exist, receipts are minted to the user via `_mint(_user, _amount)`, but `pendingWithdraws` is never increased.

Once the epoch machinery starts running, `stopEpoch`/`collectWithdrawFunds` only fund `pendingWithdraws`. The victim's basis lives only in `withdrawsRequests`/`withdrawsRequestsByEpoch`, so no underlying is ever transferred to the strategy for them. `_claimFundedWithdrawRequest` will pass the epoch-wait check after `epochNumber` increments to 1 (`epochNumber <= lastWithdrawRequest` becomes `1 <= 0`, false) and then try to pay `amount` from the strategy balance — which is 0 for these receipts, so it reverts on transfer or, worse, can be paid only by draining underlying that was funded for other users' receipts (the `_transferFundedClaim` reserve guard only protects `defaultRecoveryReserve`, not other funded claims).

This mirrors the external report exactly: a zero value on a field (`rewardsInterval_.start` / `epochEndDate`) is used as a "feature not initialized/closed" sentinel, while zero is also a legitimate live state (`start = 0` schedule / pre-first-epoch buffer pool), causing the accumulation/funding step to be skipped.

### Impact Explanation
Any lender who deposits in the buffer phase and calls `requestWithdraw` before the first `startEpoch` gets a permanently unfunded receipt. Their tranche tokens were already burned (`_burn(msg.sender, _principal)` at line 273) so they cannot redeposit or reclaim principal — one receipt, no payout. Quantified loss: 100% of the requested amount for each affected user. If the strategy later holds funded underlying for other users' claims, the victim's `claimWithdrawRequest` can spend that balance, shifting the loss to other claimants (theft of funded yield); otherwise the position is permanently frozen.

### Likelihood Explanation
Requires only ordinary, unprivileged user behavior: deposit during the initial buffer window (the phase that exists precisely to collect deposits before `startEpoch`), then request a withdrawal before the first epoch starts. No malicious privileged role, no default, no timing manipulation needed — `epochEndDate` is 0 by construction in this phase. Likelihood depends on whether operations allow withdrawal requests pre-first-epoch, but nothing in `requestWithdraw` or the CDO's `requestWithdraw`/`allow*WithdrawRequest` flags gates on "at least one epoch completed", so the window is fully open.

### Recommendation
Distinguish "closed" from "not yet started" explicitly instead of keying on `epochEndDate == 0`. Options:
- Only treat the pool as closed if at least one epoch ran: `bool isClosed = epochEndDate == 0 && epochNumber != 0;` (note `epochNumber` only increments via `deposit()`/`stopEpoch`, so the strategy should read the CDO's own notion of completed epochs — e.g. expose/track a `poolClosed` flag set only on the `_interest == 1` close path).
- Alternatively always increment `pendingWithdraws` for non-post-default requests and let the first `stopEpoch` fund them normally.
Apply the same fix to the `unscaledApr == 0 && !isClosed` branch so APR0 requests made pre-first-epoch are still booked in `apr0TotalPrincipal`.

### Proof of Concept
```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
// deploys: underlying (e.g. USDC), IdleCDOEpochVariant CDO, IdleCreditVault strategy

contract PreFirstEpochWithdrawUnfundedTest is Test {
    // ... standard CDO + strategy + KYC'd lender setup, epoch NOT yet started ...

    function testPreFirstEpochRequestNeverFunded() public {
        // 1. Buffer phase: epochEndDate == 0 because startEpoch has never run.
        assertEq(IIdleCDOEpochVariant(address(cdo)).epochEndDate(), 0);

        // 2. KYC'd lender deposits during buffer, then requests withdraw.
        token.mint(alice, DEPOSIT);
        vm.startPrank(alice);
        token.approve(address(cdo), DEPOSIT);
        cdo.depositAA(DEPOSIT);
        cdo.requestWithdrawAA(requestShares); // -> strategy.requestWithdraw
        vm.stopPrank();

        // 3. BUG: pendingWithdraws stayed 0 even though a receipt was minted.
        assertEq(strategy.withdrawsRequests(alice), expectedAmount); // user receipt exists
        assertEq(strategy.pendingWithdraws(), 0);                    // but no funding basis

        // 4. First epoch runs and stops normally.
        vm.prank(manager);
        cdo.startEpoch();
        vm.warp(block.timestamp + epochDuration + bufferDuration);
        vm.prank(manager);
        cdo.stopEpoch(); // borrower funds pendingWithdraws == 0 => nothing collected for alice

        // 5. epochNumber advanced past alice's request epoch, so the wait check passes,
        //    but the strategy holds no underlying for her claim -> claim fails / steals
        //    other users' funded balance.
        vm.prank(alice);
        vm.expectRevert(); // ERC20 transfer of insufficient balance (or drains others' funds)
        cdo.claimWithdrawRequest();
    }
}
```

Key assertion: after step 2, `pendingWithdraws()` is 0 while `withdrawsRequests(alice) > 0` — the funding accounting was skipped solely because `epochEndDate == 0` was misread as "pool closed".