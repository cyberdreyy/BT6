### Title
Post-default instant withdraw requests alias the defaulted-epoch claim slot and drain `defaultRecoveryReserve` — (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The `cgc` advisory describes unsound sharing of mutable state: multiple references to one object allow unsynchronized writes and aliased access. The analog in `IdleCreditVault` is that a **post-default** instant withdraw request is written into the same `instantWithdrawsRequestsByEpoch[user][epochNumber]` slot used for defaulted-epoch receipts, because `epochNumber` stays frozen at `defaultRecoveryEpoch` after finalization. The claim path then treats that new, unfunded receipt as a defaulted claim and pays it out of the isolated `defaultRecoveryReserve`.

### Finding Description
`requestInstantWithdraw` (contracts/strategies/idle/IdleCreditVault.sol:356-375) has no `defaultRecoveryFinalized` branch, unlike `requestWithdraw` (line 247-257) which routes post-default requests into `postDefaultRequests`. After `finalizeDefaultRecovery` sets `defaultRecoveryFinalized`, `defaultRecoveryEpoch = epochNumber`, and `defaultInstantWithdrawsFinalized` (lines 690-696), an attacker calls `requestInstantWithdraw(_amount)`. This mints receipt tokens and records `instantWithdrawsRequestsByEpoch[attacker][epochNumber] += _amount` — and since the vault is defaulted, `epochNumber == defaultRecoveryEpoch`, so the write lands in the defaulted-epoch claim slot.

On `claimInstantWithdrawRequest` (lines 380-392), `defaultRecoveryFinalized && defaultInstantWithdrawsFinalized` is true, so `_claimDefaultedInstantWithdrawRequest` reads that aliased slot and pays `(claimBasis * defaultRecoveryPrice) / 1e18` via `_transferDefaultRecovery` (lines 842-856), decrementing `defaultRecoveryReserve` (line 915). The attacker contributed **zero underlying** — `requestInstantWithdraw` only burns CDO-held strategy tokens and mints a receipt. Every wei paid out is recovery reserve earmarked for genuine defaulted claimants.

### Impact Explanation
Direct theft / permanent freezing of unclaimed recovery funds. With `defaultRecoveryPrice` near or at `RECOVERY_FULL` (full or above-par recovery is explicitly allowed, line 687), the attacker extracts nearly the full `_amount` from the reserve for free. Each stolen wei reduces `defaultRecoveryReserve`; once enough is drained, `defaultRecoveryReserve -= _amount` underflows on later legitimate `_claimDefaultedWithdrawRequest` / `_claimDefaultedInstantWithdrawRequest` calls (line 915), permanently freezing the remaining defaulted claimants' recovery payouts. Loss is bounded by `defaultRecoveryReserve` and quantified as `attackerAmount * defaultRecoveryPrice / 1e18` per request.

### Likelihood Explanation
Requires a defaulted, finalized vault — the precondition state itself is not attacker-controlled, but the report's threat model explicitly sequences around honest privileged calls (borrower default + CDO finalization), so this is admissible. Once in that state, any tranche-token holder (unprivileged, KYC'd) can repeat `requestInstantWithdraw`/`claimInstantWithdrawRequest` with no stake at risk beyond the burned receipt cost. The main uncertainty is whether `IdleCDOEpochVariant.requestInstantWithdraw` reverts when `defaulted()` is true at the CDO layer — the strategy enforces `_onlyIdleCDO` only, so the issue is exploitable only if the CDO does not hard-gate post-default instant requests (the strategy's own `requestWithdraw` shows post-default requests were a designed-for flow, suggesting the CDO does not blanket-revert).

### Recommendation
Mirror the `requestWithdraw` fix: in `requestInstantWithdraw`, if `defaultRecoveryFinalized` either revert `NotAllowed()` or route to a dedicated `postDefaultRequests`-style bucket that is never keyed to `defaultRecoveryEpoch` and never paid via `_transferDefaultRecovery`. Additionally, in `_claimDefaultedInstantWithdrawRequest`, only treat receipts created before finalization as defaulted claims.

### Proof of Concept
```solidity
// test/foundry — fork/mainnet-style test on IdleCDOEpochVariant + IdleCreditVault
function testPostDefaultInstantWithdrawDrainsReserve() external {
    // 1. Running vault, epoch started, pending instant withdraws exist so
    //    pendingInstantWithdraws != 0 at finalization (defaultInstantWithdrawsFinalized = true).
    address alice = makeAddr("alice");
    _depositWithUser(alice, 10_000 * ONE_SCALE, true);
    vm.prank(alice);
    cdoEpoch.requestInstantWithdraw(5_000 * ONE_SCALE); // current-epoch instant receipt

    // 2. Borrower defaults; honest manager/guardian finalize.
    _handleBorrowerDefault(); // helper: trigger default + CDO finalizeDefaultRecovery
    assertTrue(strategy.defaultRecoveryFinalized());
    assertTrue(strategy.defaultInstantWithdrawsFinalized());
    uint256 reserveBefore = strategy.defaultRecoveryReserve();
    uint256 epoch = strategy.epochNumber();
    assertEq(epoch, strategy.defaultRecoveryEpoch());

    // 3. Attacker (any holder) requests instant withdraw post-default.
    address eve = makeAddr("eve");
    _depositWithUser(eve, 8_000 * ONE_SCALE, true);
    vm.prank(eve);
    cdoEpoch.requestInstantWithdraw(6_000 * ONE_SCALE);
    // Aliasing: request landed in the defaulted-epoch slot.
    assertEq(
        strategy.instantWithdrawsRequestsByEpoch(eve, strategy.defaultRecoveryEpoch()),
        6_000 * ONE_SCALE
    );

    // 4. Eve claims immediately — paid from defaultRecoveryReserve with zero underlying contributed.
    vm.prank(eve);
    cdoEpoch.claimInstantWithdrawRequest();
    uint256 price = strategy.defaultRecoveryPrice();
    assertEq(strategy.defaultRecoveryReserve(), reserveBefore - (6_000 * ONE_SCALE * price / 1e18));

    // 5. Repeat until reserve exhausted: alice's (and others') legitimate
    //    defaulted claims then revert on `defaultRecoveryReserve -= amount`.
    vm.prank(alice);
    vm.expectRevert(); // underflow in _transferDefaultRecovery
    cdoEpoch.claimInstantWithdrawRequest();
}
```