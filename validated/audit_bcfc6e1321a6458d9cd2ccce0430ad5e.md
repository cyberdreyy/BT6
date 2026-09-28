### Title
Post-default instant withdraw requests are indexed under the frozen `defaultRecoveryEpoch`, letting them drain `defaultRecoveryReserve` and freezing legitimate recovery claims - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
The Excelize bug is a missing *lower-bound* check on a shared-string index: only the upper bound is validated, so an out-of-range index slips through and panics. The closest analog in `IdleCreditVault` is epoch-indexed receipt accounting that never bounds which epoch a receipt belongs to. `requestInstantWithdraw` writes new receipts into `instantWithdrawsRequestsByEpoch[_user][epochNumber]` unconditionally, but after a default is finalized `epochNumber` is frozen at `defaultRecoveryEpoch` (no further `stopEpoch`/`deposit` increments it). A tranche holder who opens an instant withdrawal post-default has that new, unfunded receipt silently merged into the defaulted epoch's claim bucket, and `_claimDefaultedInstantWithdrawRequest` pays it out of the isolated `defaultRecoveryReserve`.

### Finding Description
`requestInstantWithdraw` has no post-default guard (unlike `requestWithdraw`, which routes through `postDefaultRequests` at lines 247-257). It adds `_amount` to `instantWithdrawsRequestsByEpoch[_user][currentEpoch]` where `currentEpoch = epochNumber`: [1](#0-0) 

`finalizeDefaultRecovery` stores `defaultRecoveryEpoch = epochNumber` and isolates the reserve: [2](#0-1) 

After finalization nothing increments `epochNumber` (`epochNumber += 1` only happens in `deposit` when `isEpochRunning()`), so a post-default instant request lands in `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]`. On `claimInstantWithdrawRequest`, `_claimDefaultedInstantWithdrawRequest` reads that bucket as "defaulted-epoch receipt," subtracts it from the aggregate, and pays `claimBasis * defaultRecoveryPrice / 1e18` via `_transferDefaultRecovery`, which decrements `defaultRecoveryReserve`: [3](#0-2) 

The new receipt was never part of `defaultPendingClaimBasis()` (computed at finalization, before it existed), yet it is paid from the reserve — the index into "which claims belong to the defaulted epoch" is unbounded on the post-default side. If `defaultRecoveryPrice == RECOVERY_FULL` (full recovery) the attacker recovers 100% of a receipt backed by nothing; at any positive price it still drains reserve. Additionally, `pendingInstantWithdraws` is re-incremented by the new request while `defaultInstantWithdrawsFinalized` is already latched, so consistent accounting is unrecoverable.

### Impact Explanation
Direct theft of the default recovery reserve: a post-default instant receipt of size `X` extracts `X * defaultRecoveryPrice / 1e18` underlying from `defaultRecoveryReserve`, which is earmarked for defaulted-epoch claimants and post-default requesters (`postDefaultRequests`). Once drained below the sum of legitimate claims, later `_transferDefaultRecovery` calls underflow (`defaultRecoveryReserve -= _amount`) and every remaining defaulted/post-default claim reverts — permanent freezing of the residual recovery funds.

### Likelihood Explanation
Requires `defaultInstantWithdrawsFinalized == true` (i.e., `pendingInstantWithdraws != 0` at finalization — common when instant requests were partially funded), a positive `defaultRecoveryPrice`, and the CDO forwarding a `requestInstantWithdraw` after `defaulted()` is set. The strategy itself imposes no post-default gate on this entry point; whether `IdleCDOEpochVariant.requestInstantWithdraw` blocks calls when defaulted could not be confirmed in this pass and is the main open assumption. If the CDO does not gate it, any unprivileged tranche holder can execute the sequence.

### Recommendation
Mirror the `requestWithdraw` post-default path in `requestInstantWithdraw`: when `defaultRecoveryFinalized`, either revert or route the request into a separately indexed bucket. Concretely, record post-default instant receipts under a distinct index (or reject them), so `_claimDefaultedInstantWithdrawRequest` can never see receipts created after `defaultRecoveryEpoch` — i.e., add the missing "lower bound": only clear `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` amounts recorded at or before finalization.

### Proof of Concept
Foundry fork PoC sketch (borrower is honest; default is triggered via the normal flow, attacker is a tranche holder):

```solidity
// Setup: deposit AA, epochs run, userB has an unfunded instant withdraw
// so pendingInstantWithdraws > 0 at finalization.

// 1. Epoch running; attacker deposits and holds AA tranches.
idleCDO.depositAA(attackAmount);

// 2. Borrower defaults; owner/guardian finalizes recovery (honest flow).
//    defaultRecoveryFinalized = true, defaultRecoveryEpoch = epochNumber (frozen),
//    defaultInstantWithdrawsFinalized = true, defaultRecoveryPrice > 0.

// 3. Attacker (post-default) calls instant withdraw through the CDO:
vm.prank(attacker);
cdoEpoch.requestInstantWithdraw(attackerTrancheBal, address(AAtranche));
// -> instantWithdrawsRequestsByEpoch[attacker][defaultRecoveryEpoch] += amount
//    even though the request was created AFTER finalization.

// 4. Attacker claims; _claimDefaultedInstantWithdrawRequest treats the new
//    receipt as defaulted-epoch basis and pays from the reserve:
vm.prank(attacker);
cdoEpoch.claimInstantWithdrawRequest();
// attacker received amount * defaultRecoveryPrice / 1e18 from defaultRecoveryReserve
uint256 reserveAfter = strategy.defaultRecoveryReserve();
assertLt(reserveAfter, reserveBefore); // reserve drained by non-defaulted receipt

// 5. A legitimate defaulted-epoch claimant now reverts on underflow:
vm.prank(victim);
vm.expectRevert(); // defaultRecoveryReserve -= amount underflows
cdoEpoch.claimInstantWithdrawRequest();
```

Caveat: I was unable to verify `IdleCDOEpochVariant.requestInstantWithdraw`'s behavior when `defaulted()` is true within the available iterations; if the CDO already reverts post-default instant requests, the exploitability hinges on that gate and this reduces to an accounting-hardening issue rather than a live theft path.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L356-375)
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
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L690-696)
```text
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
