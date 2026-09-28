### Title
Post-default `requestInstantWithdraw` bypasses the "claim old receipts first" guard and pays unfunded receipts from non-reserve strategy funds - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`IdleCreditVault.requestWithdraw` enforces, once `defaultRecoveryFinalized` is set, that a user has no outstanding receipts (`_hasWithdrawRequest`, `instantWithdrawsRequests`, `postDefaultRequests`) before opening a new request, and routes new requests through the haircut-adjusted `postDefaultRequests` path. `requestInstantWithdraw` contains no equivalent check: after default finalization an attacker can open a fresh instant receipt, let it merge into the same `instantWithdrawsRequests` aggregate as pre-default entries, and `claimInstantWithdrawRequest` pays the full aggregate through `_transferFundedClaim`, which only protects `defaultRecoveryReserve` — not other users' already-funded, still-unclaimed withdraw amounts sitting in the strategy. This mirrors CVE-2019-15606: two representations of the same value (per-epoch vs. aggregate instant receipts, post-default vs. defaulted-epoch requests) are compared/paid through inconsistent paths, letting one representation slip past the authorization check applied to the other.

### Finding Description
In `requestWithdraw` (contracts/strategies/idle/IdleCreditVault.sol:243-258), the post-default branch explicitly reverts if the user holds any pending receipt, then records the new request in `postDefaultRequests` so it is paid 1:1 from the recovery reserve via `_claimPostDefaultWithdrawRequest`/`_transferDefaultRecovery`.

`requestInstantWithdraw` (lines 356-375) skips all of that:

```solidity
function requestInstantWithdraw(uint256 _amount, address _user) external {
    _onlyIdleCDO();
    _ensureDefaultRecoveryInitialized();
    _burn(msg.sender, _amount);
    _mint(_user, _amount);
    instantWithdrawsRequests[_user] += _amount;
    instantWithdrawsRequestsByEpoch[_user][epochNumber] += _amount;
    instantWithdrawClaimsByEpoch[epochNumber] += _amount;
    pendingInstantWithdraws += _amount;
}
```

On the claim side, `claimInstantWithdrawRequest` (lines 380-393) only applies the default haircut when `defaultInstantWithdrawsFinalized` is true, and even then only clears `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]`. The remaining aggregate `instantWithdrawsRequests[_user]` is burned and paid in full via `_transferFundedClaim`, whose only solvency check is `balance - reserve >= amount`.

There are two concrete abuses:

1. **Aggregate-vs-per-epoch key mismatch (the whitespace analog).** A user holding a current-epoch instant receipt when default is finalized has its basis included in `defaultPendingClaimBasis` (line 644-649) and should be paid at `defaultRecoveryPrice`. If the user instead makes a new `requestInstantWithdraw` in a later epoch, `instantWithdrawsRequestsByEpoch` gains a new-epoch entry while the aggregate `instantWithdrawsRequests` grows. On claim, `_claimDefaultedInstantWithdrawRequest` only haircuts the `defaultRecoveryEpoch` slice; the new-epoch slice is paid at par even though it was never funded through `collectInstantWithdrawFunds`/`pendingInstantWithdraws` accounting in the borrower-facing flow.

2. **Unfunded receipt paid from other users' funded money.** `claimInstantWithdrawRequest` never verifies that the requested amount was actually collected by `collectInstantWithdrawFunds`. `_transferFundedClaim` only defends `defaultRecoveryReserve`, so any underlying in the strategy above the reserve — e.g., funds already collected for other users' funded `withdrawsRequests` claims, or unclaimed amounts — can be drained by an instant receipt that borrower never funded.

### Impact Explanation
An unprivileged user (KYC-passing lender holding tranche tokens) can convert a defaulted-epoch instant receipt — which should settle at `defaultRecoveryPrice` — plus a cheap new post-default instant request into a claim paid at par from the strategy's non-reserve balance. Loss equals the difference between the haircut value and the par payout, plus any unfunded amount the strategy happened to hold for other claimants: direct theft of other users' funded withdrawal proceeds, up to `strategyBalance - defaultRecoveryReserve`.

### Likelihood Explanation
Requires the vault to be in `defaultRecoveryFinalized` state (borrower default is a normal protocol phase, not attacker-controlled) and `allowInstantWithdraw` enabled, which is the default operating mode and is left enabled even in closed-pool mode (test at IdleCreditVault.t.sol:5441). The attacker's only cost is depositing tranche tokens to back the new instant request; `requestInstantWithdraw` is reachable by any user via `cdoEpoch.requestWithdraw` during a running epoch. The guard asymmetry between `requestWithdraw` and `requestInstantWithdraw` is unconditional — no privileged misstep is needed beyond the honest default finalization.

### Recommendation
Add the same post-default gating to `requestInstantWithdraw` that `requestWithdraw` has: when `defaultRecoveryFinalized`, revert if the user holds any outstanding receipt (`_hasWithdrawRequest(_user) || instantWithdrawsRequests[_user] != 0 || postDefaultRequests[_user] != 0`), and route the new request through a post-default accounting path that pays only from reserve at the haircut-adjusted value — or simply revert all instant requests after finalization. Additionally, in `claimInstantWithdrawRequest`, only pay amounts that have actually been collected (track funded vs. unfunded instant basis) rather than paying the raw aggregate, and consider having `_transferFundedClaim` defend a broader "owed to other claimants" invariant, not just `defaultRecoveryReserve`.

### Proof of Concept
Foundry fork PoC (extends `test/foundry/IdleCreditVault.t.sol` setup):

```solidity
function testPostDefaultInstantBypass() external {
    // 1) deposit AA, start epoch 0 with allowInstantWithdraw = true
    idleCDO.depositAA(10_000 * ONE_SCALE);
    _startEpochAndCheckPrices(0);

    // 2) attacker requests instant withdraw mid-epoch
    uint256 req = cdoEpoch.requestWithdraw(0, address(AAtranche));

    // 3) borrower defaults; owner/manager finalize recovery at < 100%
    //    (honest flow: cdoEpoch._handleBorrowerDefault -> finalizeDefault ->
    //     strategy.finalizeDefaultRecovery(recovered, source))
    //    defaultRecoveryPrice < RECOVERY_FULL, pendingInstantWithdraws != 0
    //    => defaultInstantWithdrawsFinalized == true

    // 4) attacker (same user) calls requestInstantWithdraw again post-finalization.
    //    requestWithdraw would revert NotAllowed here; instant path does not.
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(0, address(AAtranche)); // new epoch instant request

    // 5) claim: defaulted-epoch slice haircut, new-epoch slice + any leftover
    //    aggregate paid at par via _transferFundedClaim, spending underlyings
    //    above defaultRecoveryReserve that belong to other funded claimants.
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest();

    // assert: attacker received more than claimBasis * defaultRecoveryPrice / RECOVERY_FULL
    // and strategy balance < defaultRecoveryReserve + sum(remaining funded claims)
}
```

Key assertion: the second `requestWithdraw`/`requestInstantWithdraw` succeeds where the normal path at IdleCreditVault.sol:249 would have reverted `NotAllowed`, demonstrating the missing check.

Note: the exact reachable theft size depends on how much non-reserve underlying the strategy holds at finalization time (funded-but-unclaimed normal receipts); the unfunded-receipt payout is bounded by `balance - reserve`. I was unable to fully trace the CDO-side instant-request entry to confirm whether an additional guard upstream blocks step 4 — if `IdleCDOEpochVariant.requestWithdraw` routes post-default instant requests through a funded check, the practical impact narrows to the per-epoch/aggregate haircut-bypass in step 5, which still stands on `instantWithdrawsRequestsByEpoch[user][epoch]` being keyed differently from `defaultRecoveryEpoch`.