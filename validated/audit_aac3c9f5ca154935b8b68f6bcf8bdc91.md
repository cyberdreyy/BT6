### Title
Uninitialized/closed `epochEndDate` makes the epoch-wait gate vacuous, letting a same-epoch withdraw request be claimed instantly and drain funded receipts - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The external Fei `Timed.isTimeEnded` bug class is "a zero-valued time/state marker is read as 'ended' before it was ever initialized". The direct analog in this repo is `epochEndDate` on `IIdleCDOEpochVariant`: a value of `0` is used as the "closed pool / no running epoch" sentinel, and `IdleCreditVault` treats that sentinel as "the wait period is over" in both `requestWithdraw` and `_claimFundedWithdrawRequest`. A withdraw request made while `epochEndDate() == 0` skips `pendingWithdraws` accounting yet still mints a full receipt, and the claim-side epoch gating `epochEndDate() != 0 && epochNumber <= lastWithdrawRequest[user]` is bypassed, so the request is claimable in the same transaction at par.

### Finding Description
In `requestWithdraw`, when `IIdleCDOEpochVariant(idleCDO).epochEndDate() == 0` (`isClosed`), the request is not added to `pendingWithdraws` (line 277-280), but it still burns principal from the CDO, mints `_amount` receipt tokens to the user, and records `withdrawsRequests[user] += _amount` (line 292). Notably, if `unscaledApr == 0`, the `!isClosed` guard pushes the request into the *normal* `withdrawsRequests` bucket (line 285-293), so `_amount` — which can include interest — becomes a normal at-par claim.

In `_claimFundedWithdrawRequest` (line 326), the revert that enforces the one-epoch waiting period only applies when `epochEndDate() != 0`:

```solidity
if (IIdleCDOEpochVariant(idleCDO).epochEndDate() != 0 && (epochNumber <= lastWithdrawRequest[_user])) {
  revert NotAllowed();
}
```

With `epochEndDate() == 0` the check is skipped entirely, and `_transferFundedClaim` pays the user from the strategy's underlying balance with no epoch wait — the same way `isTimeEnded()` returned `true` before `startTime` was set. `epochNumber` is also `0`/stale in this state, so even the fallback `epochNumber <= lastWithdrawRequest` comparison is meaningless. This mirrors the Fei bug exactly: an uninitialized/terminal marker is interpreted as "time elapsed".

### Impact Explanation
Direct theft and freezing of other users' claims. When the pool is in the `epochEndDate() == 0` state (closed via a repay-all `stopEpochWithDuration(1)`, or before/without a running epoch), the strategy can hold underlying belonging to already-funded withdraw receipts (collected via `collectWithdrawFunds`) and pending instant-withdraw recipients. An unprivileged tranche holder can, through the CDO's withdraw-request path, call `requestWithdraw` and `claimWithdrawRequest` atomically, bypassing the one-epoch queue and the instant-withdraw funding flow (`getInstantWithdrawFunds`/`collectInstantWithdrawFunds`), and receive underlying immediately at par — up to the strategy's entire free balance (bounded only by their tranche holdings converted to receipt). Earlier funded claimants and instant-withdraw recipients are left with receipts that later revert on transfer (insolvency/temporary-to-permanent freezing of unclaimed yield). The `_transferFundedClaim` guard only protects `defaultRecoveryReserve`, not other users' funded receipts.

### Likelihood Explanation
The `epochEndDate() == 0` state is reachable by design (closed pools, and any period where no epoch is running with funds still held in the strategy). The attacker needs only to be an allowed (KYC-passing) lender with tranche tokens — no privileged role. The only mitigating factor is that the exploitable balance is the strategy's residual underlying (unclaimed funded receipts, donations not yet skimmed, or mid-flow deposits), so loss scales with that residual balance rather than the whole NAV. Notably, existing guards do not stop it: `_ensureDefaultRecoveryInitialized` only runs when `defaultRecoveryInitialized == false`, the skim/reserve checks don't apply, and `_onlyIdleCDO` is satisfied by the normal CDO withdraw-request path.

### Recommendation
- Distinguish "never started" from "closed" explicitly (e.g., an `epochInitialized` flag or requiring `epochNumber > 0`), analogous to Fei's fix of reverting when the timer hasn't been started.
- In `_claimFundedWithdrawRequest`, when `epochEndDate() == 0` but `epochNumber` has not advanced past `lastWithdrawRequest[user]`, do not silently allow the claim — either revert or require an explicit closed-pool claim path.
- In `requestWithdraw`, when `isClosed`, either revert new requests or route them through the same funded-accounting path as `pendingWithdraws` so claims are still backed by a `collectWithdrawFunds` transfer rather than arbitrary strategy balance.

### Proof of Concept
Foundry fork PoC sketch:

```solidity
// Setup: deploy IdleCDOEpochVariant + IdleCreditVault (fixed-APR mode),
// lender Alice deposits AA tranche, borrower draws, epoch runs.
// 1. Manager calls stopEpochWithDuration(1) -> repays all, epochEndDate set to 0
//    (closed pool). Borrower funds pending receipts; collectWithdrawFunds moves
//    underlyings into the strategy. Some receipts remain unclaimed.
// 2. Attacker Bob (allowed lender, tranche tokens):
//    cdo.withdrawRequest(bobAA);            // -> strategy.requestWithdraw(...)
//    cdo.claimWithdrawRequest();            // -> strategy.claimWithdrawRequest(bob)
//    Both succeed in the SAME transaction: epochEndDate()==0 skips the
//    epochNumber <= lastWithdrawRequest revert, and isClosed skipped
//    pendingWithdraws accounting.
// 3. Bob receives underlying immediately at par, consuming the strategy
//    balance earmarked for earlier funded receipt holders.
// 4. assert(strategy.balanceOf(unclaimedUser) > 0) and subsequent
//    claimWithdrawRequest for the earlier user reverts (insufficient balance).
```

Caveat: I verified the strategy-side logic in `contracts/strategies/idle/IdleCreditVault.sol` (lines 243-350), but could not fully read `IdleCDOEpochVariant`/`IdleCDOCreditVault` within the iteration budget to confirm the exact CDO entry points (`withdrawRequest`/`claimWithdrawRequest` wrappers) and the exact conditions under which `epochEndDate` is set to `0`. The strategy code comments (line 276, 320-336) confirm `epochEndDate == 0` is the "closed pool / immediate claim" sentinel, which is sufficient to establish the uninitialized-state analog.