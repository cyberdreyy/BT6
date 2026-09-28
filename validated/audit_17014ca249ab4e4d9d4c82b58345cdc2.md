### Title
Instant-withdraw receipts are classified as "funded" vs "defaulted" solely by an epoch tag lookup, so a receipt pending across an epoch boundary escapes the default haircut and is paid at par — (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The OCI CVE-2021-41190 bug class is *type confusion by an external tag*: the same bytes are interpreted as manifest or index depending on a context header rather than intrinsic content. The analog lives in `IdleCreditVault.claimInstantWithdrawRequest`: an instant-withdraw receipt is interpreted as either a *defaulted* receipt (haircut via `_transferDefaultRecovery`) or a *funded* receipt (par via `_transferFundedClaim`) purely by whether `instantWithdrawsRequestsByEpoch[user][defaultRecoveryEpoch]` is non-zero. A receipt created in epoch N that stays unfunded past an epoch boundary and into a default finalized in epoch N+1 matches neither interpretation correctly: it is excluded from the default claim basis, yet the claim path falls through to pay it at par, draining underlyings that back other users' funded withdraw receipts.

### Finding Description
In `requestInstantWithdraw` the receipt is tagged with the *current* `epochNumber` (line ~367-372): `instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount` and `instantWithdrawClaimsByEpoch[currentEpoch] += _amount`, while `pendingInstantWithdraws` tracks the still-unfunded remainder globally.

At default finalization (`finalizeDefaultRecovery`, lines ~644-696):

- `defaultPendingClaimBasis()` adds to the recovery basis only `instantWithdrawClaimsByEpoch[epochNumber]` — i.e., receipts tagged with the *finalization* epoch.
- `defaultRecoveryEpoch = epochNumber` and `defaultInstantWithdrawsFinalized = pendingInstantWithdraws != 0`.

Later, `claimInstantWithdrawRequest` (lines 380-393) does:

```solidity
if (defaultRecoveryFinalized && defaultInstantWithdrawsFinalized) {
  _claimDefaultedInstantWithdrawRequest(_user);   // looks up ByEpoch[defaultRecoveryEpoch]
}
uint256 amount = instantWithdrawsRequests[_user]; // aggregate
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);              // pays at par
```

`_claimDefaultedInstantWithdrawRequest` (lines 842-856) keys the haircut strictly on `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]`. The "type" of the receipt — defaulted vs funded — is therefore determined by an epoch tag match, exactly like the CVE's Content-Type ambiguity.

Now consider the sequence:

1. Epoch N running. Attacker calls `requestInstantWithdraw` → receipt tagged epoch N; `pendingInstantWithdraws += X`.
2. `stopEpoch`/`startEpoch` occurs while the instant queue is only partially funded (the code comments in `defaultPendingClaimBasis` explicitly acknowledge partial instant funding: "cash covered only part of the instant queue"). `epochNumber` increments to N+1 via `deposit()` on stopEpoch (line ~610). `pendingInstantWithdraws` still > 0; the attacker's receipt remains tagged epoch N, and `instantWithdrawClaimsByEpoch[N+1] == 0`.
3. Borrower defaults; `finalizeDefaultRecovery` runs in epoch N+1. `defaultRecoveryEpoch = N+1`. The attacker's receipt is **not** counted in `defaultPendingClaimBasis` (no basis, nothing reserved for it).
4. Attacker calls `claimInstantWithdrawRequest`. `_claimDefaultedInstantWithdrawRequest` reads `instantWithdrawsRequestsByEpoch[user][N+1] == 0` → returns 0 → the aggregate `instantWithdrawsRequests[user] = X` is burned and paid **at par** through `_transferFundedClaim`.

`_transferFundedClaim` only protects `defaultRecoveryReserve` (`balance - reserve >= amount`), so the par payout is sourced from underlyings that fund *other users'* pending normal-withdraw receipts (collected via `collectWithdrawFunds`/`collectInstantWithdrawFunds`), or, if none exist, the claim reverts — permanently freezing a claim that should have been paid at `defaultRecoveryPrice`.

The same epoch-tag ambiguity also affects the normal path's loss/funded split (`lossRecoveryPriceByEpoch[lastWithdrawRequest[user]]` vs `withdrawsRequestsByEpoch`), though `requestWithdraw`'s guard (lines 263-271) partially mitigates it there; no equivalent guard exists for instant receipts.

### Impact Explanation
The attacker recovers 100% of their instant-withdraw receipt while every other defaulted claimant recovers only `defaultRecoveryPrice`. Because the receipt was never included in `defaultPendingClaimBasis`, the recovery price was computed without it — the payout is unbacked and directly consumes underlyings reserved for other users' funded withdraw requests (theft of up to the attacker's full principal). In the alternative branch (strategy balance == reserve), the attacker's claim — and, since claims revert atomically, effectively the whole instant-claim path — is permanently frozen, so legitimate haircut payouts are unclaimable. This is direct theft / permanent freezing of user funds, quantified by `X * (1 - defaultRecoveryPrice)` stolen plus up to `X` frozen.

### Likelihood Explanation
Requires three conditions, none needing privileged collusion:

- The attacker only needs to be a wallet allowed to request instant withdrawals (a KYC'd lender/tranche holder — in scope).
- The instant queue must remain partially unfunded across one epoch boundary — the code explicitly contemplates partial instant funding at `startEpoch`, and any borrower liquidity shortfall that is not yet a formal default leaves `pendingInstantWithdraws > 0`.
- A subsequent borrower default with `finalizeDefaultRecovery` — defaults are modeled in-scope (`_handleBorrowerDefault`, `finalizeDefaultRecovery` are part of the intended flow; the attacker is not the borrower).

The epoch-tag mismatch is deterministic once the pending instant receipt survives one `stopEpoch`; no race or privileged misbehavior is needed.

### Recommendation
Classify receipts by their funded status, not by epoch tag equality. Concretely:

- Track, per user, the *unfunded* instant receipt balance (e.g., a `pendingInstantByUser[user]`) rather than relying on `instantWithdrawsRequestsByEpoch[user][defaultRecoveryEpoch]`; or
- At `finalizeDefaultRecovery`, treat *all* outstanding instant receipts (aggregated across epochs) whose principal was not funded as defaulted basis — i.e., include `pendingInstantWithdraws`-backed receipts regardless of their tag epoch in `defaultPendingClaimBasis`, and have `_claimDefaultedInstantWithdrawRequest` clear `min(claimBasis, unfundedPortion)` across all epochs; or
- Simplest: revert/skip the funded-par branch in `claimInstantWithdrawRequest` whenever `defaultInstantWithdrawsFinalized` is true and the user still holds an unfunded receipt, forcing every unfunded receipt through `defaultRecoveryPrice`.

### Proof of Concept
Foundry fork PoC sketch (attacker = allowed lender; borrower default sequence):

```solidity
function testInstantReceiptEscapesDefaultHaircut() external {
    // Epoch N running, instant withdrawals enabled
    cdoEpoch.setInstantWithdrawParams(delay, aprDiff, false); // manager
    uint256 tranche = depositAA(user, 100_000e6);
    startEpoch();

    // Attacker requests instant withdraw in epoch N
    vm.prank(user);
    cdoEpoch.requestInstantWithdraw(tranche, address(AAtranche));
    uint256 receipt = strategy.instantWithdrawsRequests(user);
    assertGt(receipt, 0);

    // Borrower fails to fund the instant queue; epoch rolls: epochNumber -> N+1
    stopEpoch();          // pendingInstantWithdraws stays > 0, receipt tagged N
    startEpoch();

    // Borrower defaults; recovery finalized in epoch N+1
    handleBorrowerDefault();
    finalizeDefaultRecovery();   // defaultRecoveryEpoch = N+1
    assertTrue(strategy.defaultInstantWithdrawsFinalized());
    // attacker's receipt was NOT part of defaultPendingClaimBasis
    assertEq(strategy.instantWithdrawsRequestsByEpoch(user, N + 1), 0);

    // Attacker claims: defaulted path is a no-op, funded path pays at par
    uint256 balPre = underlying.balanceOf(user);
    vm.prank(user);
    cdoEpoch.claimInstantWithdrawRequest();
    uint256 got = underlying.balanceOf(user) - balPre;

    // Expected (correct): receipt * defaultRecoveryPrice / RECOVERY_FULL
    // Actual (bug): receipt paid at par, sourced from other claimants' funded
    // withdraw reserves sitting in the strategy.
    assertEq(got, receipt); // steals (1 - recoveryPrice) * receipt
}
```

Uncertainty note: the PoC assumes `pendingInstantWithdraws` can remain non-zero across a `stopEpoch`/`startEpoch` boundary without the CDO reverting the epoch transition — the in-code comments about partially prefunded instant claims at `startEpoch` indicate this state is reachable, but the exact `IdleCDOEpochVariant` funding ordering should be confirmed when writing the executable test.