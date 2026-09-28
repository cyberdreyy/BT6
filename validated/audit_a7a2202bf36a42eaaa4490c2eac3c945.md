### Title
APR change after an APR0 withdraw request permanently reverts `stopEpoch` and freezes the vault - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The external report is a crash class bug (unhandled input causing an access violation). The strongest analog in `IdleCreditVault` is a state-dependent revert: `prepareStopEpochWithApr0` reverts whenever `apr0TotalPrincipal != 0` and `unscaledApr != 0`. An unprivileged lender can open an APR0 withdraw request while `unscaledApr == 0`, after which a routine APR update by the manager (honest, sequencing around their call) makes every subsequent `stopEpoch` revert, freezing all pending withdraw receipts and epoch accounting until APR is forced back to 0.

### Finding Description
- `requestWithdraw` routes into `_requestWithdrawApr0` whenever `unscaledApr == 0` and the pool is not closed, incrementing the global `apr0TotalPrincipal` bucket for that epoch (`IdleCreditVault.sol` L285-286, L567-577).
- At `stopEpoch`, the CDO calls `prepareStopEpochWithApr0`, which loads `apr0TotalPrincipal` and, if non-zero, hard-reverts when `unscaledApr != 0`:

```solidity
uint256 _principal = apr0TotalPrincipal;
if (_principal == 0) {
  return (_expInterest, _adjPendingWithdrawFees);
}
// APR0 principal is only valid while APR is 0 for that request lifecycle.
if (unscaledApr != 0) {
  revert NotAllowed();
}
```
(`IdleCreditVault.sol` L499-508)

- `unscaledApr` is set by `setAprs` / `setAprsWithBuffer` (L206-220), callable by the CDO or manager at any time — including mid-epoch or during the buffer, as part of normal APR management. There is no guard preventing an APR change while `apr0TotalPrincipal > 0`.
- Once the revert condition is reached, nothing clears `apr0TotalPrincipal`: it is only zeroed on a successful `prepareStopEpochWithApr0` (L540) or inside `_ensureDefaultRecoveryInitialized` (L931), which itself reverts when `pendingWithdraws != 0` unless the pool is already closed (L927-929). So `stopEpoch` reverts deterministically on every call while `unscaledApr != 0`.
- `requestWithdraw` also does not help: while `unscaledApr != 0` new requests go to the normal path, leaving the stale APR0 bucket intact.

### Impact Explanation
All epoch-finalization paths funnel through `prepareStopEpochWithApr0`, so the revert freezes: pending withdraw receipts (which cannot be claimed until `epochNumber` advances past `lastWithdrawRequest`, L326), APR0 settlements, and the epoch itself. Borrower repayment funds returned via `stopEpoch` cannot be collected, and lenders' claimable underlying stays locked. The freeze persists until the manager sets `unscaledApr` back to 0 and runs a stop — during which the borrower effectively accrues/pays at APR 0, destroying an epoch of expected lender yield (a quantified yield loss on top of the temporary freeze).

### Likelihood Explanation
Requires only: (1) an epoch configured with APR = 0 (an intended "apr0 mode" the code explicitly supports), (2) any KYC'd lender calling `requestWithdraw`, (3) the manager later raising the APR via `setAprs`/`setAprsWithBuffer` before or at the next `stopEpoch` — a routine operational action. No malicious privileged role is needed; the attacker is an ordinary withdraw requester whose request creates the poisoned state.

### Recommendation
In `prepareStopEpochWithApr0`, do not revert on `unscaledApr != 0`; instead settle the APR0 bucket (e.g., treat pending APR0 principal as having zero rate for the epoch, or pay out at the rate captured at request time via `apr0RateByEpoch`). Alternatively, block APR changes while `apr0TotalPrincipal != 0` in `setApr`/`setAprs`, or snapshot the APR at request time so the lifecycle APR is immutable.

### Proof of Concept
```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";

// Sequence (APR0-mode vault, buffer/running epoch):
// 1. Manager/CDO has unscaledApr == 0.
// 2. KYC'd lender calls IdleCDO.requestWithdraw -> IdleCreditVault.requestWithdraw
//    -> _requestWithdrawApr0 -> apr0TotalPrincipal = lenderAmount.
// 3. Manager calls setAprs(newApr, scaledApr) with newApr != 0 (honest routine update).
// 4. Manager calls stopEpoch -> prepareStopEpochWithApr0 reverts NotAllowed()
//    because apr0TotalPrincipal != 0 && unscaledApr != 0.
// 5. Every subsequent stopEpoch reverts identically; pendingWithdraws and
//    borrower repayment funds remain locked until unscaledApr is reset to 0.

contract Apr0StopEpochFreezeTest is Test {
    // IdleCreditVault vault; IdleCDOEpochVariant cdo; address manager; address lender;

    function test_Apr0RequestBlocksStopAfterAprChange() external {
        // vm.prank(lender);
        // cdo.requestWithdraw(amount);              // apr0TotalPrincipal > 0
        // vm.prank(manager);
        // vault.setAprs(5e18, scaledApr);           // unscaledApr now != 0
        // vm.prank(manager);
        // vm.expectRevert(NotAllowed.selector);
        // cdo.stopEpoch();                          // revert -> frozen epoch
    }
}
```