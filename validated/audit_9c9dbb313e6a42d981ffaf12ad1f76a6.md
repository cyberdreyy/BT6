### Title
Instant withdrawal is denied when the APR drop equals `instantWithdrawAprDelta` exactly - ([File: contracts/IdleCDOEpochVariant.sol](https://github.com/Thankgod67Ikhide/idle-tranches--015/blob/main/contracts/IdleCDOEpochVariant.sol))

### Summary

In `IdleCDOEpochVariant.requestWithdraw`, the check that routes a withdrawal to the instant-withdraw path uses a strict `>` against `currentApr + instantWithdrawAprDelta`. When the new epoch APR drops by *exactly* the configured delta, the request is silently processed as a normal (queued) withdrawal instead of an instant one — the same boundary bug class as the referenced `AuctionHouse.bid()` report, where `>` was used instead of `>=` on a "minimum increment/threshold" check.

### Finding Description

`instantWithdrawAprDelta` is documented as the "min apr change to trigger instant withdraw" (`setInstantWithdrawParams`, line 129). The routing logic is:

```solidity
// contracts/IdleCDOEpochVariant.sol:761-770
if (_isInstantWithdrawEnabled()) {
  uint256 currentApr = creditVault.unscaledApr();
  if (lastEpochApr > (currentApr + instantWithdrawAprDelta)) {
    creditVault.requestInstantWithdraw(_underlyings, msg.sender);
    _withdrawOps(_amount, _underlyings, _tranche);
    return _underlyings;
  }
}
```

If `lastEpochApr == currentApr + instantWithdrawAprDelta`, the condition is false and execution falls through to the normal withdraw-request path (lines 772-790). There the request:

- burns tranche tokens and creates a receipt claimable only after the *next* epoch settles (`claimWithdrawRequest` requires at least one full epoch, ~`epochDuration` + `bufferPeriod`, versus `instantWithdrawDelay` — default ~3 days — for instant claims),
- charges upfront management fees via `_totalWithdrawFees(principal, interest)` computed over `_withdrawRequestManagementFeeDuration()` (one epoch plus remaining buffer, lines 900-906 and 925-931), which an instant request does not pay,
- mutates `pendingWithdrawFees` and `interestForOverUnderPerformance`, changing epoch accounting for everyone.

A lender requesting withdrawal in the buffer window right after `stopEpoch` sets an APR exactly `instantWithdrawAprDelta` below `lastEpochApr` is therefore forced into the slower, fee-charging path despite meeting the documented minimum threshold.

### Impact Explanation

Direct user cost and temporary freezing of funds for unprivileged, KYC'd lenders:

- The user pays the upfront management fee on the queued receipt (and performance-fee math on projected interest) that an instant withdrawal avoids, so they receive strictly less underlying than the instant path would deliver on the same principal.
- Their funds are locked for a full epoch + buffer instead of `instantWithdrawDelay`, i.e., a materially longer freeze than the instant path's "claimable when the next epoch funds arrive" guarantee.
- The misrouting also inflates `pendingWithdrawFees` and `pendingWithdraws`, so the borrower must return additional cash at `stopEpoch`; if the borrower shortfall pushes the pool into `_handleBorrowerDefault`, other users' claims are also delayed.

Quantified: for a request of `P` underlying, the loss is the management fee `mgmtFee = _calculateManagementFee(P, epochDuration + remainingBuffer)` plus the time-value of funds locked for `epochDuration + bufferPeriod` rather than `instantWithdrawDelay`.

### Likelihood Explanation

- Requires only that a manager sets a new APR that differs from `lastEpochApr` by exactly `instantWithdrawAprDelta`. This is a plausible, even natural, configuration boundary (e.g., delta of 1% with APR moving from 10% to 9%), not an exotic edge.
- `instantWithdrawAprDelta` is an integer in the same units as `unscaledApr`, so equality is a single, easily-hit value.
- An attacker cannot force the APR (manager is honest), so likelihood is moderate rather than high — matching the original Medium severity.
- No existing guard corrects this: `_isInstantWithdrawEnabled`, `allowAA/BBWithdrawRequest`, and KYC checks all pass; the misrouting is purely the boundary comparison.

### Recommendation

Allow the boundary case to use the instant path:

```diff
// contracts/IdleCDOEpochVariant.sol
- if (lastEpochApr > (currentApr + instantWithdrawAprDelta)) {
+ if (lastEpochApr >= (currentApr + instantWithdrawAprDelta)) {
```

Equivalently `currentApr + instantWithdrawAprDelta <= lastEpochApr`. This matches the documented semantic of `instantWithdrawAprDelta` as the *minimum* APR change that triggers instant withdrawal, mirroring the fix recommended in the source report.

### Proof of Concept

Foundry fork-style PoC sketch (adapt to repo test harness in `test/foundry/IdleCreditVault.t.sol`):

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {IdleCDOEpochVariant} from "../contracts/IdleCDOEpochVariant.sol";
import {IdleCreditVault} from "../contracts/strategies/idle/IdleCreditVault.sol";
import {IERC20Detailed} from "../contracts/interfaces/IERC20Detailed.sol";

contract InstantWithdrawBoundaryPoC is Test {
  IdleCDOEpochVariant cdo;      // deployed epoch-variant CDO
  IdleCreditVault vault;        // strategy
  IERC20Detailed underlying;
  IERC20Detailed AA;

  address manager = address(0xA11CE); // owner/manager (honest)
  address lender  = address(0xB0B);   // KYC'd lender

  uint256 aprOld = 10_000;  // 10% in strategy APR units
  uint256 delta  = 1_000;   // instantWithdrawAprDelta = 1%
  uint256 aprNew = aprOld - delta; // drop exactly equals delta

  function setUp() public {
    // deploy cdo + vault, whitelist `lender` via keyring mock,
    // setInstantWithdrawParams(3 days, delta, false)
    // lender deposits into AA, epoch 1 runs at aprOld, manager stops epoch
    // with newApr == aprNew so lastEpochApr == aprOld and
    // vault.unscaledApr() == aprNew  =>  aprOld == aprNew + delta
  }

  function testBoundaryDeniedInstantWithdraw() public {
    uint256 amt = AA.balanceOf(lender);
    vm.prank(lender);
    uint256 requested = cdo.requestWithdraw(amt, address(AA));

    // BUG: with `>` the request went to the NORMAL path:
    // - pendingInstantWithdraws was NOT incremented
    assertEq(vault.pendingInstantWithdraws(), 0, "instant path not taken at boundary");
    // - a normal receipt exists instead, claimable only after next epoch
    assertGt(vault.withdrawsRequestsByEpoch(cdo.epochNumber()), 0);
    // - and upfront management fees were charged into pendingWithdrawFees
    assertGt(cdo.pendingWithdrawFees(), 0, "unexpected fee charged vs instant path");

    // After only instantWithdrawDelay the user cannot claim:
    vm.warp(block.timestamp + cdo.instantWithdrawDelay() + 1);
    vm.prank(lender);
    vm.expectRevert(); // no instant request exists; normal request not yet matured
    cdo.claimInstantWithdrawRequest();
  }
}
```

Expected result: the assertions hold, demonstrating that a drop exactly equal to `instantWithdrawAprDelta` routes to the fee-charging, epoch-locked path; flipping `>` to `>=` makes `pendingInstantWithdraws() == requested` and lets the lender claim once funds arrive after `instantWithdrawDelay`.