Let me check the ordering between `epochNumber` increment and `collectWithdrawFunds` in `_stopEpoch`.### Title
Epoch-0 sentinel collision permanently freezes all withdraw claims after a first-epoch borrower default - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary

`IdleCreditVault._claimFundedWithdrawRequest` uses `lastWithdrawRequest[_user] == 0` as the "no pending receipt" sentinel while epoch `0` is also a valid request epoch. When a borrower default occurs at the very first `stopEpoch`, `epochNumber` remains `0` forever (the `deposit()` call that increments it is skipped on the default path), and `epochEndDate` stays non-zero. Every subsequent `claimWithdrawRequest` — for defaulted-epoch recovery claims and for old funded receipts — then falls through to `_claimFundedWithdrawRequest` and hits `epochNumber (0) <= lastWithdrawRequest (0)`, reverting with `NotAllowed`. All recovery-reserve and funded underlying in the strategy become permanently unclaimable.

### Finding Description

The kernel analog is "skip sessions that are being torn down (`SES_EXITING`) to avoid UAF": a teardown-in-progress entity must be excluded from the normal lookup path. Here, withdraw receipts in the defaulting epoch are "torn down" via `_claimDefaultedWithdrawRequest`, which clears `lastWithdrawRequest[_user]` back to `0` (`IdleCreditVault.sol:832-836`), after which execution unconditionally continues into the generic funded-claim path (`IdleCreditVault.sol:312-313`).

`_claimFundedWithdrawRequest` then applies its liveness gate:

```solidity
// contracts/strategies/idle/IdleCreditVault.sol:326
if (IIdleCDOEpochVariant(idleCDO).epochEndDate() != 0 && (epochNumber <= lastWithdrawRequest[_user])) {
  revert NotAllowed();
}
```

Relevant mechanics:

- `epochNumber` is only bumped inside `deposit()` (`IdleCreditVault.sol:607-610`), which is called inside the `try` block of `IdleCDOEpochVariant._stopEpoch` (`IdleCDOEpochVariant.sol:466`). On borrower default the `catch` path runs `_handleBorrowerDefault` instead (`IdleCDOEpochVariant.sol:501-504`), so a default at the first stop leaves `epochNumber == 0` permanently (`defaulted` blocks any further `startEpoch`).
- `defaultRecoveryEpoch` is recorded as `epochNumber` at finalization (`IdleCreditVault.sol:693`), i.e. `0`.
- A user claiming their epoch-0 defaulted receipt goes through `_claimDefaultedWithdrawRequest` → `_clearWithdrawClaimForEpoch`, which clears `withdrawsRequestsByEpoch[_user][0]`, decrements `withdrawsRequests`, and sets `lastWithdrawRequest[_user] = 0` (`IdleCreditVault.sol:772-784`, `832-836`).
- Control then reaches `_claimFundedWithdrawRequest`. `epochEndDate` is non-zero (a healthy close is the only path that zeroes it, `IdleCDOEpochVariant.sol:488-492`), `epochNumber == 0`, `lastWithdrawRequest == 0` → `0 <= 0` → `revert NotAllowed()`. The revert rolls back the entire claim including the recovery transfer and the clearing of the receipt.

The same revert fires for *any* user whose `lastWithdrawRequest` is `0`, including holders of receipts funded before the default and post-default requesters calling `claimWithdrawRequest` after `_claimPostDefaultWithdrawRequest` returns `amount != 0` — note `_claimPostDefaultWithdrawRequest` `return`s early only when `amount != 0`, so post-default claimants escape; but defaulted-epoch claimants and legacy funded claimants do not.

The reserve accounting then strands funds: `defaultRecoveryReserve` is funded in `finalizeDefaultRecovery` (`IdleCreditVault.sol:686-708`) but can only leave through `_transferDefaultRecovery` or `transferToken` (owner-only rescue). With all defaulted-epoch-0 claims reverting, the recovery reserve and funded underlyings are permanently locked.

### Impact Explanation

Direct, permanent freezing of user funds. On a first-epoch borrower default, every holder of a defaulted-epoch withdraw receipt and every holder of an older funded receipt can never claim: `claimWithdrawRequest` always reverts. The entire `defaultRecoveryReserve` (recovered underlying) plus pre-default funded underlyings sit in `IdleCreditVault` forever. Loss equals the full unfunded claim basis of the defaulted pool — potentially 100% of pool NAV for epoch-0 defaults.

### Likelihood Explanation

Requires the borrower to default at the first `stopEpoch` (epoch 0), and requires `epochEndDate` to remain non-zero after `_handleBorrowerDefault` — the code only zeroes `epochEndDate` in the healthy close-pool branch (`IdleCDOEpochVariant.sol:492`), not in the default path. A first-epoch default is a routine credit event (borrower misses the first repayment), and any unprivileged tranche holder is harmed; no attacker action is needed beyond holding a withdraw receipt. I was not able to fully verify `_handleBorrowerDefault`'s state writes within the available context; if it sets `epochEndDate = 0`, the claim gate is bypassed and this defect does not trigger — that is the one unconfirmed precondition.

### Recommendation

Replace the epoch-0 sentinel ambiguity in `_claimFundedWithdrawRequest`: use `lastWithdrawRequest[_user] + 1` as the stored marker (offset by one), or track request existence via `withdrawsRequestsByEpoch`/`apr0Users` non-emptiness instead of the raw epoch number. Concretely, gate on `epochNumber < lastWithdrawRequest[_user]` semantics with an explicit "has request" flag, or early-return `0` when the user has no remaining claim basis (`withdrawsRequests[_user] == 0` and empty `apr0Users`) before applying the epoch gate.

### Proof of Concept

```solidity
// test/foundry/IdleCreditVaultEpoch0Default.t.sol
function testEpoch0DefaultFreezesClaims() external {
    // Setup: deposit via queue/CDO, start epoch 0, user requests normal withdraw
    _depositWithUser(FASA, 100e6);
    _startEpochAndCheckPrices(0);
    _requestWithdrawWithUser(FASA, ONE_TRANCHE); // recorded at epochNumber == 0

    // Borrower has no funds -> first stopEpoch defaults while epochNumber is still 0
    deal(address(underlying), strategy.borrower(), 0, true);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);
    assertTrue(cdoEpoch.defaulted());
    assertEq(strategy.epochNumber(), 0);

    // Finalize recovery with partial recovered funds
    uint256 basis = strategy.defaultPendingClaimBasis();
    deal(address(underlying), manager, basis / 2, true);
    vm.startPrank(manager);
    underlying.approve(address(strategy), basis / 2);
    cdoEpoch.finalizeDefault(basis / 2, manager);
    vm.stopPrank();

    // User claims defaulted receipt: _claimDefaultedWithdrawRequest clears
    // lastWithdrawRequest -> 0, then _claimFundedWithdrawRequest sees
    // epochEndDate != 0 && epochNumber(0) <= lastWithdrawRequest(0) -> revert
    vm.prank(FASA);
    vm.expectRevert(NotAllowed.selector);
    cdoEpoch.claimWithdrawRequest();
    // recovery reserve is now permanently stranded in the strategy
}
```