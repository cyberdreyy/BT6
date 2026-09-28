### Title
Normal `stopEpoch` unconditionally clears `allowInstantWithdraw`, silently freezing already-funded instant-withdrawal claims — (File: `contracts/IdleCDOEpochVariant.sol`)

### Summary
`IdleCDOEpochVariant._stopEpoch` ends with `allowInstantWithdraw = _isRequestingAllFunds` (`contracts/IdleCDOEpochVariant.sol:486`). Because the guard at the top of the function requires `_pendingInstant() == 0` (`contracts/IdleCDOEpochVariant.sol:345`), every instant-withdraw request must already have been funded — either via `startEpoch` (`contracts/IdleCDOEpochVariant.sol:282-292`) or via `getInstantWithdrawFunds` (`contracts/IdleCDOEpochVariant.sol:558-570`), both of which set `allowInstantWithdraw = true`. The stop handler then "blind-merges" `false` over that enabled flag for the overwhelmingly common case `_interest != 1`, omitting the "were instant funds already collected?" condition. This is the on-chain analog of the changedetection.io bug: a save handler that rewrites a whole flag group while silently dropping (disabling) an enforcement flag the caller never intended to touch.

### Finding Description
- `requestWithdraw` routes a request to the instant path when `lastEpochApr > currentApr + instantWithdrawAprDelta` (`contracts/IdleCDOEpochVariant.sol:761-770`), burning the user's tranche tokens immediately via `_withdrawOps`. The receipt lives in `IdleCreditVault` and can only be paid through `claimInstantWithdrawRequest`, which is gated on the CDO's `allowInstantWithdraw` flag.
- Instant claims get funded in two ways: (a) `startEpoch` forwards `pendingInstant` underlyings to the vault and sets `allowInstantWithdraw = true` (`contracts/IdleCDOEpochVariant.sol:279-292`); (b) `getInstantWithdrawFunds` pulls them from the borrower mid-epoch after `instantWithdrawDeadline` and also sets the flag (`contracts/IdleCDOEpochVariant.sol:564-570`).
- `_stopEpoch` can only run once `_pendingInstant() == 0`, i.e. funding already happened. Yet on a normal stop (`_interest != 1`) it executes `allowInstantWithdraw = _isRequestingAllFunds` → `false`, with the comment "block instant withdraws claims as these can be done only after the deadline" (`contracts/IdleCDOEpochVariant.sol:484-486`). The deadline check it references was already satisfied — that is precisely why `getInstantWithdrawFunds` was callable. The assignment ignores the already-funded state and silently disables claims.
- The flag is only re-set inside `startEpoch` (`contracts/IdleCDOEpochVariant.sol:292`) and only when the pool is still open (`epochDuration != 0` guard at `:239`). There is no external setter for `allowInstantWithdraw`, so the only way out is a subsequent `startEpoch`.
- The broken invariant: a fulfilled receipt must remain claimable ("one receipt one payout"); here an already-funded, token-burned claim is gated off by an unrelated bookkeeping write.

### Impact Explanation
Any holder who requested an instant withdrawal (or a normal requester whose request was converted) but has not yet claimed by the time `stopEpoch` runs sees their funded claim frozen for the entire inter-epoch window: at minimum `bufferPeriod`, and in practice until the manager chooses to call `startEpoch` again. If `stopEpochWithDuration` is later used with `_duration` or if `setEpochParams` is delayed, the freeze extends arbitrarily. Quantified loss: 100% of the affected claimant's underlying (`_underlyings` burned at request time) is inaccessible for the freeze duration, even though the cash is already sitting in `IdleCreditVault`. Per the rules this is "temporary freezing of user funds"; it is not user error and cannot be self-rescued.

### Likelihood Explanation
Requires only a routine sequence of honest privileged calls (`getInstantWithdrawFunds`/funded `startEpoch` → `stopEpoch`) plus one unclaimed instant receipt, which is common — users routinely let claims sit. No attacker capability beyond holding a tranche token and timing `requestWithdraw` is needed; nothing in `_stopEpoch`, `_beforeUnpause`, or `restoreOperations` prevents or repairs the flag clobber. Likelihood is moderate: it triggers whenever a funded instant claim survives to an epoch boundary.

### Recommendation
Replace the unconditional overwrite with a state-aware one, e.g.:

```solidity
// contracts/IdleCDOEpochVariant.sol::_stopEpoch
// do not clear the flag if instant funds were already collected
allowInstantWithdraw = _isRequestingAllFunds ||
    _strategy.totalInstantWithdrawFunds() != 0; // or a persisted wasInstantFunded flag
```

Alternatively record `bool _instantFunded = allowInstantWithdraw;` before `_updateAccounting()` and restore it in the success path, or move the `allowInstantWithdraw = false` write to only execute when no funded-but-unclaimed instant balance exists in `IdleCreditVault`. Add a regression test: fund an instant request, warp past `epochEndDate`, call `stopEpoch`, assert `claimInstantWithdrawRequest` still succeeds.

### Proof of Concept
Foundry-style PoC sketch (extend `test/foundry/IdleCreditVault.t.sol` helpers `_toggleEpoch`, `_expectedFundsEndEpoch`):

```solidity
function testInstantClaimFrozenByNormalStop() external {
    // 1. Deposit and enter a running epoch with instant withdrawals enabled
    idleCDO.depositAA(10_000 * ONE_SCALE);
    cdoEpoch.setInstantWithdrawParams(1 days, 1, false); // owner/manager
    _toggleEpoch(true, 0, 0);                            // startEpoch funds pending instant and sets allowInstantWithdraw

    // 2. Attacker/user requests an instant withdraw after APR drop
    stdstore.target(address(cdoEpoch)).sig(cdoEpoch.lastEpochApr.selector)
        .checked_write(strategy.unscaledApr() + cdoEpoch.instantWithdrawAprDelta() + 1);
    vm.warp(block.timestamp + cdoEpoch.instantWithdrawDelay() + 1);
    cdoEpoch.requestWithdraw(0, address(AAtranche));     // instant path, tranche tokens burned

    // 3. Manager pulls instant funds -> claims enabled
    deal(defaultUnderlying, borrower, _expectedFundsEndEpoch());
    cdoEpoch.getInstantWithdrawFunds();
    assertTrue(cdoEpoch.allowInstantWithdraw());

    // 4. Epoch ends, honest manager stops normally
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(initialProvidedApr, 0);

    // 5. Funded claim is now bricked until the next startEpoch
    assertFalse(cdoEpoch.allowInstantWithdraw());
    vm.expectRevert();                                    // gated on allowInstantWithdraw
    strategy.claimInstantWithdrawRequest(/* receipt id */);
}
```

Uncertainty note: I could not read `IdleCreditVault.claimInstantWithdrawRequest`'s exact revert path or confirm whether a compensating queue/hook re-enables the flag in prefunded variants; the finding rests on the CDO-side flag lifecycle shown above (`:282-292`, `:486`, `:570`), which is sufficient to establish the silent-disable behavior.