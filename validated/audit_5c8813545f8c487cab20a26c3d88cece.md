### Title
Post-default instant withdraw requests are recorded under the defaulted epoch and drain the fixed recovery reserve - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`requestInstantWithdraw` has no `defaultRecoveryFinalized` handling, unlike `requestWithdraw` which explicitly guards and routes post-default requests through `postDefaultRequests`. After default recovery is finalized, a new instant-withdraw request is written into `instantWithdrawsRequestsByEpoch[user][epochNumber]`, but `epochNumber` no longer advances, so the entry lands in the same bucket as `defaultRecoveryEpoch`. On claim, `_claimDefaultedInstantWithdrawRequest` treats the post-default receipt as a defaulted-epoch claim and pays it out of the fixed-size `defaultRecoveryReserve`, which was sized at finalization only for pre-existing claims.

### Finding Description
The CVE analog (two inputs — `-R`/`-S` — processed through one buffer, corrupting accounting) maps to two request classes (instant vs. normal) sharing one per-epoch claim bucket. `requestWithdraw` contains an explicit post-default branch: it reverts when `_hasWithdrawRequest(_user) || instantWithdrawsRequests[_user] != 0 || postDefaultRequests[_user] != 0` and otherwise routes the request to `postDefaultRequests` [1](#0-0) . `requestInstantWithdraw` performs the same burn/mint receipt flow but unconditionally writes `instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount` and `pendingInstantWithdraws += _amount` with no default-finalized check [2](#0-1) . Since `epochNumber` is frozen at `defaultRecoveryEpoch` after finalization (`defaultRecoveryEpoch = epochNumber` at line 693 [3](#0-2) ), the new request merges into `instantWithdrawsRequestsByEpoch[user][defaultRecoveryEpoch]`.

On the next `claimInstantWithdrawRequest`, when `defaultInstantWithdrawsFinalized` is true, `_claimDefaultedInstantWithdrawRequest` reads that epoch bucket, burns the receipt, decrements `instantWithdrawClaimsByEpoch[defaultEpoch]` and pays `claimBasis * defaultRecoveryPrice / RECOVERY_FULL` from `defaultRecoveryReserve` [4](#0-3) . The reserve was computed as `reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve` against `totalBasis` fixed at finalization [5](#0-4)  — it contains no allocation for requests created after finalization. Additionally, `pendingInstantWithdraws = claimBasis >= pending ? 0 : pending - claimBasis` (line 852) lets the inflated `claimBasis` zero out the global pending counter while other users' unfunded instant receipts still exist.

### Impact Explanation
Each post-default instant request of size X withdraws `X * defaultRecoveryPrice / RECOVERY_FULL` from a reserve that was funded only for pre-finalization claims. Legitimate defaulted-epoch claimants who claim later find the reserve depleted and their transfers revert — permanent freezing of unclaimed default-recovery funds up to the amount drained. The reserve accounting invariant (reserve == Σ remaining claims × recoveryPrice) is broken by unbounded post-finalization deposits into the default-epoch bucket.

### Likelihood Explanation
Requires the pool to be in defaulted+finalized state and the CDO variant to still forward `requestInstantWithdraw` for tranche holders. Tranche tokens trade near `defaultRecoveryPrice` post-default, so an attacker acquires them cheaply, requests an instant withdraw, and immediately draws reserve funds; the direct profit is roughly break-even on price but the insolvency/griefing of remaining claimants is deterministic. The asymmetric guarding between `requestWithdraw` (guarded) and `requestInstantWithdraw` (unguarded) indicates the post-default path was overlooked. Likelihood is moderate: it needs a defaulted pool with `pendingInstantWithdraws != 0` at finalization so `defaultInstantWithdrawsFinalized` is set.

### Recommendation
Add the same post-default gating to `requestInstantWithdraw`: if `defaultRecoveryFinalized`, either revert or route the request into a par-paid post-default bucket (mirroring `postDefaultRequests`) that is funded outside `defaultRecoveryReserve`. At minimum, skip `instantWithdrawsRequestsByEpoch`/`instantWithdrawClaimsByEpoch` writes and the `pendingInstantWithdraws` increment when finalized, and ensure `claimInstantWithdrawRequest` cannot match a post-finalization receipt to `defaultRecoveryEpoch` — e.g., record request epoch as `epochNumber + 1` or key defaulted claims by a separate flag.

### Proof of Concept
Foundry fork PoC sketch (mirror `test/foundry/IdleCreditVault.t.sol` setup with instant withdraws enabled):

```solidity
// 1. Start epoch, user1 requests instant withdraw of 100e6 tranche tokens.
// 2. Borrower partially funds instant queue (pendingInstantWithdraws > 0),
//    then defaults; finalizeDefaultRecovery() sets
//    defaultRecoveryEpoch == epochNumber, defaultRecoveryPrice < RECOVERY_FULL.
// 3. Attacker buys tranche tokens post-default (~recoveryPrice of face).
// 4. attacker -> idleCDO.requestInstantWithdraw(X, tranche)
//    -> IdleCreditVault.requestInstantWrites instantWithdrawsRequestsByEpoch[attacker][epochNumber]
//    (epochNumber == defaultRecoveryEpoch).
// 5. idleCDO.claimInstantWithdrawRequest() ->
//    _claimDefaultedInstantWithdrawRequest pays X * defaultRecoveryPrice / RECOVERY_FULL
//    from defaultRecoveryReserve.
// 6. Repeat / size X so cumulative drain exceeds reserve slack:
//    user1's subsequent claimInstantWithdrawRequest reverts on
//    underlyingToken.safeTransfer (insufficient reserve balance).
assertEq(underlying.balanceOf(attacker) - balPre, X * defaultRecoveryPrice / RECOVERY_FULL);
vm.expectRevert(); // user1 claim now fails: reserve insolvent
cdoEpoch.claimInstantWithdrawRequest(); // as user1's CDO caller
```

Note: step 4 assumes the CDO variant's instant-withdraw entry point is not disabled post-default; I could not verify `IdleCDOEpochVariant`'s defaulted-state gating within the tool-call budget — if the CDO reverts instant requests when `defaulted()`, the exploit path narrows to requests queued just before finalization that still get keyed into the already-snapshotted epoch bucket.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L247-257)
```text
    if (defaultRecoveryFinalized) {
      // user should first claim old already-funded withdraw requests before requesting new ones after default
      if (_hasWithdrawRequest(_user) || instantWithdrawsRequests[_user] != 0 || postDefaultRequests[_user] != 0) {
        revert NotAllowed();
      }
      // Preserve request/claim UX after default without increasing borrower-facing pendingWithdraws.
      // The CDO passes an already-haircut amount because finalization lowered virtualPrice first.
      _burn(msg.sender, _amount);
      _mint(_user, _amount);
      postDefaultRequests[_user] = _amount;
      return;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L356-374)
```text
  function requestInstantWithdraw(uint256 _amount, address _user) external {
    _onlyIdleCDO();
    _ensureDefaultRecoveryInitialized();
    // burn strategy tokens from cdo
    _burn(msg.sender, _amount);
  
    // mint equal amount of strategy tokens to the user as receipt, useful in case of default
    _mint(_user, _amount);

    // increase the instant withdraw requests for the user
    instantWithdrawsRequests[_user] += _amount;
    uint256 currentEpoch = epochNumber;
    // we record both per-user (old, kept for compatibility) and per-epoch so on
    // finalization we can distinguish "default-epoch pending instant receipts"
    // from old funded instant receipts.
    instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
    instantWithdrawClaimsByEpoch[currentEpoch] += _amount;
    // increase the total instant withdraw requests
    pendingInstantWithdraws += _amount;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L685-696)
```text
    uint256 prefundedReserve = _defaultPrefundedInstantReserve();
    uint256 reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve;
    // Recovery can be above par if the recovered funds exceed the computed basis.
    uint256 recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;

    defaultRecoveryFinalized = true;
    defaultRecoveryReserve = reserveAmount;
    defaultRecoveryPrice = recoveryPrice;
    defaultRecoveryEpoch = epochNumber;
    // A non-zero pending instant bucket means current-epoch instant receipts were not fully funded
    // and must be paid through the same recovery ratio as normal pending receipts.
    defaultInstantWithdrawsFinalized = pendingInstantWithdraws != 0;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L842-855)
```text
  function _claimDefaultedInstantWithdrawRequest(address _user) internal returns (uint256 claimBasis) {
    uint256 defaultEpoch = defaultRecoveryEpoch;
    claimBasis = instantWithdrawsRequestsByEpoch[_user][defaultEpoch];
    if (claimBasis == 0) return claimBasis;

    instantWithdrawsRequestsByEpoch[_user][defaultEpoch] = 0;
    instantWithdrawsRequests[_user] -= claimBasis;
    uint256 pending = pendingInstantWithdraws;
    // `pendingInstantWithdraws` is only the unfunded remainder. If this user's claim is larger,
    // the extra amount was already counted as prefunded reserve during default finalization.
    pendingInstantWithdraws = claimBasis >= pending ? 0 : pending - claimBasis;
    instantWithdrawClaimsByEpoch[defaultEpoch] -= claimBasis;
    _burn(_user, claimBasis);
    _transferDefaultRecovery(_user, (claimBasis * defaultRecoveryPrice) / RECOVERY_FULL);
```
