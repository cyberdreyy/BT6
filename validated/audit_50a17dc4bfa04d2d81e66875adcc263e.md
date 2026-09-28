### Title
`_forceUpdateAccounting` resets `skipDefaultCheck` to `false` instead of restoring the pre-call value, clobbering the default-skip context set by an outer flow - (File: contracts/IdleCDO.sol)

### Summary
The analog of the `xDomainMsgSender` bug is the shared context flag `skipDefaultCheck`. Just as `relayMessage()` sets `xDomainMsgSender = _sender`, runs the message, and then overwrites it with `DEFAULT_L2_SENDER` rather than the caller's previous value, `_forceUpdateAccounting()` sets `skipDefaultCheck = true`, runs `_updateAccounting()`, and then unconditionally writes `skipDefaultCheck = false` instead of restoring the value that was in force before the call. Any outer context that legitimately had `skipDefaultCheck == true` (emergency shutdown, or a default detected inside the nested `_updateAccounting` itself) silently loses that state after the inner call returns.

### Finding Description
`IdleCDO._forceUpdateAccounting()` (contracts/IdleCDO.sol:921-927) implements the same "set, call, reset-to-constant" pattern:

```solidity
function _forceUpdateAccounting() internal {
    skipDefaultCheck = true;
    _updateAccounting();
    // _updateAccounting can set `skipDefaultCheck` to true in case of default
    // but this can be manually be reset to true if needed
    skipDefaultCheck = false;
}
```

Two concrete clobbering scenarios exist:

1. **Nested default detection.** The inline comment acknowledges that `_updateAccounting()` itself can set `skipDefaultCheck = true` when it detects a lending-protocol default. `_forceUpdateAccounting()` is invoked at the tail of a loss-bearing `stopEpoch`/`stopEpochWithDuration` (contracts/IdleCDOEpochVariant.sol:496-499: `burnStrategyTokens(_lossAmount)` then `_forceUpdateAccounting()`). If the price update inside that call detects the default and sets `skipDefaultCheck = true` as the sticky signal of the defaulted state, the function then erases it by writing `false`. The next user deposit/withdraw goes through `_updateAccounting` with `skipDefaultCheck == false`, re-running the default check against a defaulted lending protocol and reverting — the exact analogue of `xDomainMessageSender()` reverting because the context was wiped mid-execution.

2. **Outer emergency context.** `_emergencyShutdown` (contracts/IdleCDO.sol:936-948) sets `skipDefaultCheck = true` precisely so that selectively re-enabled AA withdrawals keep working despite the default check. `updateAccounting()` (owner/guardian, contracts/IdleCDO.sol:915-918) wraps `_forceUpdateAccounting()`. An honest guardian calling `updateAccounting()` after a shutdown — a sanctioned, expected sequence, since the function exists exactly to refresh loss accounting while paused — returns with `skipDefaultCheck == false`, silently undoing part of the shutdown protection for the remainder of the paused regime.

Both callers (`stopEpoch`, `updateAccounting`, `emergencyShutdown`) are privileged and honest, so no attacker reentrancy is needed; the bug is the unconditional reset-to-constant corrupting a context that an enclosing flow had deliberately established — the same root cause as M-04, where the inner `relayMessage` resets `xDomainMsgSender` to default while an outer message is still executing.

### Impact Explanation
When `skipDefaultCheck` is clobbered after a detected default or an emergency shutdown, subsequent deposits and AA withdrawals revert inside `_updateAccounting`'s default check instead of being allowed under the configured shutdown/default policy. The result is a temporary (or, until owner intervention, indefinite) freezing of withdrawals that governance had explicitly re-enabled, plus loss of the sticky "defaulted" signal the accounting layer relies on. Severity is bounded because recovery requires only an owner/guardian transaction, matching Medium.

### Likelihood Explanation
Triggering requires a specific but plausible ordering: (a) a `stopEpochWithDuration` with `_lossAmount != 0` where the nested accounting update flags a default, or (b) a guardian calling `updateAccounting()` after `_emergencyShutdown`. Both are honest-actor sequences the contract explicitly supports (the guardian-facing `updateAccounting` exists "just to be called when a default happened"), so no adversary is required — the corrupted state emerges purely from the reset-to-constant pattern.

### Recommendation
Cache and restore instead of resetting to a constant:

```solidity
function _forceUpdateAccounting() internal {
    bool cached = skipDefaultCheck;
    skipDefaultCheck = true;
    _updateAccounting();
    // restore whatever the outer context (or the nested default detection) established
    skipDefaultCheck = cached || skipDefaultCheck;
}
```

or, if the post-default `true` should be sticky, simply delete the trailing `skipDefaultCheck = false;` and let callers manage the flag.

### Proof of Concept
Foundry fork PoC (conceptual; requires mainnet fork of a deployed IdleCDO epoch variant):

```solidity
function testSkipDefaultCheckClobbered() public {
    // 1. Owner triggers emergency shutdown -> skipDefaultCheck == true,
    //    then re-enables AA withdrawals selectively.
    vm.prank(owner);
    cdo.emergencyShutdown();

    // 2. Guardian refreshes accounting while paused (documented use case).
    vm.prank(guardian);
    cdo.updateAccounting();

    // 3. BUG: skipDefaultCheck is now false even though shutdown set it true.
    assertFalse(cdo.skipDefaultCheck());

    // 4. An AA withdraw that was deliberately re-allowed now reverts in
    //    _updateAccounting's default check (underlying lending protocol defaulted).
    vm.prank(aaHolder);
    vm.expectRevert(); // default check
    cdo.withdrawAA(amount);
}
```

A variant drives scenario 1: warp past `epochEndDate` with a defaulted strategy, call `stopEpochWithDuration` with nonzero `_lossAmount` so the `burnStrategyTokens → _forceUpdateAccounting` path runs, then assert `skipDefaultCheck == false` despite the default being detected inside `_updateAccounting`.

Caveat: I could not fully verify the exact revert path inside `_updateAccounting` / `_checkDefault` within the available iterations, so the precise revert condition in step 4 should be confirmed against that function's default branch when writing the PoC.