### Title
`requestWithdraw` books APR0 principal that permanently bricks `stopEpoch` if APR is later raised - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.requestWithdraw` routes a user's request into the APR0 bucket (`apr0Users`/`apr0TotalPrincipal`) whenever `unscaledApr == 0` at request time, but `prepareStopEpochWithApr0` reverts if `unscaledApr != 0` at stop time while `apr0TotalPrincipal != 0`. Nothing prevents an (honest) manager or the CDO from setting a non-zero APR between the request and the epoch stop, so every `stopEpoch` call reverts and the epoch can never close until APR is manually restored to 0. This mirrors the reported bug class: the contract permits an inconsistent state (`apr0TotalPrincipal != 0 && unscaledApr != 0`) instead of enforcing the lifecycle precondition, and then punishes it with a revert that freezes the vault.

### Finding Description
- `requestWithdraw` buckets the request as APR0 purely on the current value of `unscaledApr` (`contracts/strategies/idle/IdleCreditVault.sol:285-294`), incrementing the global `apr0TotalPrincipal` (`:576`).
- `prepareStopEpochWithApr0`, called inside `stopEpoch`, requires `unscaledApr == 0` whenever `apr0TotalPrincipal != 0` and reverts otherwise (`:506-508`), and only clears `apr0TotalPrincipal` on success (`:540`).
- `setApr`/`setAprs` (`:206-235`) can be called by the manager or the CDO at any time with no check on pending APR0 principal.
- Consequence: a single APR0 withdraw request followed by any APR change makes `stopEpoch` (and therefore `stopEpochWithDuration`, `_handleBorrowerDefault` paths that call it, epoch rollover, and all `claimWithdrawRequest` calls gated on `epochNumber > lastWithdrawRequest`) revert indefinitely. Funds are frozen — for all depositors, not just the requester — until the manager happens to restore `unscaledApr` to 0.

### Impact Explanation
Unprivileged trigger: any KYC-passing lender can create the poison state by calling `requestWithdraw` during an APR0 epoch. The manager merely changing the APR (a normal, honest operation between or during epochs) then makes every epoch-stop path revert with `NotAllowed`. Withdrawals cannot be claimed because `epochNumber` never advances (`:326`), borrower funding and accounting are stalled, and interest accounting is blocked. This is temporary-to-permanent freezing of all vault funds; recovery requires privileged intervention that the current code gives no warning about.

### Likelihood Explanation
Requires only (a) an epoch configured with `unscaledApr == 0` (an explicitly supported mode with dedicated accounting), (b) one withdraw request in that mode, and (c) an APR update before `stopEpoch`. The APR-setter functions carry no guard against pending APR0 principal, so this state is reachable through entirely legitimate calls. The freeze is deterministic once the state exists.

### Recommendation
Do not let `prepareStopEpochWithApr0` revert on `unscaledApr != 0`. Either settle the APR0 bucket at the stored request-epoch rate regardless of the current APR (the requests already locked in their terms), or gate `setApr`/`setAprs` to revert while `apr0TotalPrincipal != 0` so the inconsistent state can never be entered. Additionally, zero `apr0TotalPrincipal` before any possible revert so partial failures cannot wedge the bucket.

### Proof of Concept
```solidity
// test/foundry/IdleCreditVault.t.sol context: _depositWithUser, _startEpochAndCheckPrices helpers
function testApr0RequestThenAprChangeBricksStopEpoch() external {
    IdleCreditVault cv = IdleCreditVault(address(strategy));
    vm.startPrank(manager);
    cv.setAprs(0, 0); // APR0 epoch
    vm.stopPrank();

    uint256 amountWei = 10000 * ONE_SCALE;
    idleCDO.depositAA(amountWei);
    _startEpochAndCheckPrices(0);

    // Unprivileged lender requests withdraw while APR is 0 -> apr0TotalPrincipal > 0
    address user = makeAddr("user1");
    _depositWithUser(user, 1000 * ONE_SCALE, true);
    vm.prank(user);
    // via CDO withdraw request flow
    // cdoEpoch.requestWithdraw(...) -> cv.requestWithdraw -> _requestWithdrawApr0
    assertGt(cv.apr0TotalPrincipal(), 0);

    // Honest manager raises APR before epoch end
    vm.prank(manager);
    cv.setAprs(1e18, 1e18); // unscaledApr != 0

    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    // Every retry reverts; epochNumber never advances, claims frozen
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);
    assertEq(cv.epochNumber(), 1, 'epoch stuck');
}
```