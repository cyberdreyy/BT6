### Title
Deposits are still accepted after the pool is permanently closed, locking user funds forever - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
`IdleCDOEpochVariant` closes the pool when `stopEpoch` is called with `_interest == 1` ("request all funds back" mode). In that path `epochDuration` and `epochEndDate` are set to `0`, so `startEpoch` can never run again (`_checkNotAllowed(... || _epochDuration == 0)`). However, the same stop flow unconditionally calls `_unpause()` and re-enables withdraw requests, which re-opens the ordinary `depositAA`/`depositBB` → `_deposit` path. The external bug class — "the contract keeps accepting contributions after a terminal threshold, so contributors receive tokens worth less than (or nothing against) their investment" — maps directly here: after pool closure, new depositors still get tranche tokens minted at `priceAA`/`priceBB`, but there is no future epoch, no borrower funding round, and no mechanism that will ever send underlying to `pendingWithdraws` for their redemption. Their funds are permanently frozen in the strategy/CDO.

### Finding Description
In `_stopEpoch`, when `_isRequestingAllFunds` is true (`_interest == 1`):

- `isEpochRunning` is set to `false` and, because `skipDefaultCheck` is false, `_unpause()` runs and `allowAAWithdrawRequest`/`allowBBWithdrawRequest` are set to `true` (IdleCDOEpochVariant.sol:474-483).
- `epochDuration = 0` and `epochEndDate = 0` are set (IdleCDOEpochVariant.sol:488-493), which permanently blocks `startEpoch` via `_checkNotAllowed(... || _epochDuration == 0)` (IdleCDOEpochVariant.sol:239).

The epoch-variant `_deposit` override only checks `isWalletAllowed`, skims donations, and relies on `whenNotPaused` (IdleCDOEpochVariant.sol:644-650). It has no "pool closed" guard (`epochEndDate == 0` / `epochDuration == 0`). After a close-pool stop, the contract is unpaused, so any KYC'd wallet can call `depositAA`/`depositBB` (or the `TrancheWrapper`), pull in underlyings, and be minted tranche shares via `_mintSharesAtCurrPrice` at the last tranche price.

Those shares are unredeemable: `requestWithdraw` burns tranche tokens and registers a claim in `IdleCreditVault.pendingWithdraws`, but funding for pending withdrawals only ever arrives via `stopEpoch`/`getInstantWithdrawFunds` borrower pulls — and no epoch can ever start or stop again because `epochDuration == 0` blocks `startEpoch` and `!isEpochRunning` blocks `stopEpoch`. The deposited underlying is either sitting in the CDO/strategy or was pushed via `IIdleCDOStrategy(strategy).deposit(_amount)` into strategy tokens, with no path back.

This mirrors the GenesisGroup bug: a terminal condition (max price reached / pool closed) is reached, but the contribution entry point keeps accepting funds and issuing tokens that can never be worth what was paid.

### Impact Explanation
Any user (or an attacker tricking users / a router) who deposits after pool closure permanently loses 100% of the deposited amount. Because `epochEndDate = 0` and `epochDuration = 0`, `startEpoch` reverts forever, `isEpochRunning` stays false, `stopEpoch` reverts, and there is no epoch transition that can fund `pendingWithdraws` or otherwise return principal. The loss is permanent freezing of user funds, quantified as the full deposit amount per affected depositor.

### Likelihood Explanation
Likelihood depends on whether real deployments use the close-pool flow (`stopEpoch(apr, 1)`), which is a supported, documented manager path ("special case where we request everything back from the borrower"). Once executed, the vault remains unpaused and externally indistinguishable from a normal buffer phase — `paused() == false`, `depositAA`/`depositBB` do not revert, and `maxDeposit` in `TrancheWrapper` only consults the TVL `limit`. Any subsequent depositor (including via ERC4626 wrappers that check `maxDeposit`, which stays positive) loses their funds. No privileged misbehavior is required; the honest manager's routine close-pool call creates the trap.

### Recommendation
Block ordinary deposits once the pool is closed. Concretely:

- Add a check in `IdleCDOEpochVariant._deposit` (or in `depositAA`/`depositBB`) that reverts when `epochEndDate == 0` / `epochDuration == 0` (closed-pool state), or
- Keep the contract paused after a close-pool stop (skip the `_unpause()` when `_isRequestingAllFunds` is true), since only withdrawals/claims are meaningful afterwards, or
- Introduce an explicit `poolClosed` flag checked by all deposit entry points, including `TrancheWrapper.maxDeposit`, so integrators see `0` capacity.

### Proof of Concept
Foundry-style test sketch (place under `test/foundry/`):

```solidity
function test_DepositAfterPoolClose_LocksFunds() public {
    // --- normal setup: two depositors, run one epoch ---
    uint256 amount = 1000 * ONE_SCALE;
    _depositAA(address(this), amount);          // KYC'd lender
    vm.prank(owner); idleCDO.setKeyringParams(...); // per existing test utils
    vm.prank(manager); idleCDO.startEpoch();

    // warp past epoch end, owner requests ALL funds back => pool closed
    vm.warp(idleCDO.epochEndDate() + 1);
    borrower.approveUnderlyingTo(idleCDO);       // borrower honest
    vm.prank(manager);
    idleCDO.stopEpoch(newApr, 1);                // _interest == 1

    // pool is closed permanently
    assertEq(idleCDO.epochDuration(), 0);
    assertEq(idleCDO.epochEndDate(), 0);
    assertFalse(idleCDO.isEpochRunning());
    assertFalse(idleCDO.paused());               // deposits are OPEN

    // --- victim deposits after closure ---
    address victim = kycAllowedUser;
    underlying.mint(victim, amount);
    vm.startPrank(victim);
    underlying.approve(address(idleCDO), amount);
    uint256 minted = idleCDO.depositAA(amount);  // succeeds, mints shares
    vm.stopPrank();
    assertGt(minted, 0);

    // victim requests withdraw -> receipt recorded, but nothing will ever fund it
    vm.prank(victim);
    idleCDO.requestWithdraw(0, AATranche);
    vm.warp(block.timestamp + 365 days);
    vm.prank(victim);
    // reverts / returns 0: pendingWithdraws never funded, no epoch can start
    vm.expectRevert();
    idleCDO.claimWithdrawRequest();

    // startEpoch is permanently bricked
    vm.prank(manager);
    vm.expectRevert();
    idleCDO.startEpoch();
}
```

Note: exact helper names (`_depositAA`, claim signature, manager/owner handles) should be matched to the repo's existing `TestIdleCDOBase` / `IdleCreditVault.t.sol` utilities; the key assertions are that `depositAA` succeeds while `epochDuration == 0` and that the resulting claim can never be paid because neither `startEpoch` nor `stopEpoch` can execute again.