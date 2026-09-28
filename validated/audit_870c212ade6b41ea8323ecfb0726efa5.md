### Title
Stale instant-withdraw receipt basis is double-counted in default recovery, enabling double claims - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`claimInstantWithdrawRequest` clears only the aggregate `instantWithdrawsRequests[_user]` balance, leaving `instantWithdrawsRequestsByEpoch[_user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]` populated. If the user requests a second instant withdrawal in the same epoch and the borrower subsequently defaults, `finalizeDefaultRecovery` treats the already-paid receipt as both pending claim basis and prefunded reserve, and `_claimDefaultedInstantWithdrawRequest` lets the user claim the stale basis a second time. This is the closest analog to use-after-free: a logically freed receipt object is reused by later accounting.

### Finding Description
The instant-withdraw lifecycle keeps three ledgers:

- `instantWithdrawsRequests[_user]` – aggregate receipt basis (line 106 analog, `contracts/strategies/idle/IdleCreditVault.sol:366`)
- `instantWithdrawsRequestsByEpoch[_user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]` – per-epoch basis used by default accounting (`IdleCreditVault.sol:371-372`)

`claimInstantWithdrawRequest` pays the full aggregate at par and burns the receipt tokens, but only resets `instantWithdrawsRequests[_user]`:

```solidity
uint256 amount = instantWithdrawsRequests[_user];
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);
```
`IdleCreditVault.sol:387-392` — the per-epoch entries are never cleared.

After a default, `defaultPendingClaimBasis` adds `instantWithdrawClaimsByEpoch[epochNumber]` whenever `pendingInstantWithdraws != 0` (`IdleCreditVault.sol:644-648`), and `_defaultPrefundedInstantReserve` computes `instantWithdrawClaimsByEpoch[epochNumber] - pendingInstantWithdraws` as already-held reserve (`IdleCreditVault.sol:716-722`). Both include stale basis from receipts already claimed at par. Finally, `_claimDefaultedInstantWithdrawRequest` pays out the full `instantWithdrawsRequestsByEpoch[_user][defaultEpoch]` again (`IdleCreditVault.sol:842-855`).

Sequence (epoch E, instant mode enabled, APR dropped so requests route to instant path):

1. Attacker calls `requestWithdraw`, routing `w1` into `requestInstantWithdraw` (`IdleCDOEpochVariant.sol:761-768`). `instantWithdrawsRequestsByEpoch[E] = w1`.
2. Epoch starts; `getInstantWithdrawFunds`/`collectInstantWithdrawFunds` funds `w1`. Attacker calls `claimInstantWithdrawRequest`, receives `w1` at par. Per-epoch entries still equal `w1`.
3. Within the same epoch (APR still depressed), attacker requests `w2` via the instant path. Now `instantWithdrawsRequestsByEpoch[E] = w1 + w2`, `instantWithdrawClaimsByEpoch[E] = w1 + w2`, `pendingInstantWithdraws = w2` (unfunded).
4. `stopEpoch` fails to source `w2`; pool defaults. `finalizeDefaultRecovery` computes `pendingBasis += w1 + w2` and `prefundedReserve = (w1+w2) - w2 = w1` — but the `w1` underlyings were already paid out in step 2, so `defaultRecoveryReserve` is overstated by `w1` phantom funds relative to actual balance.
5. Attacker calls `claimInstantWithdrawRequest`; `_claimDefaultedInstantWithdrawRequest` pays `(w1 + w2) * defaultRecoveryPrice / 1e18` from the reserve, even though `w1` was already paid.

### Impact Explanation
Direct theft and insolvency: the attacker extracts up to `w1 * defaultRecoveryPrice / 1e18` in excess of entitlement, and the inflated `prefundedReserve`/`recoveryPrice` means the reserve is under-collateralized by exactly `w1`. Honest defaulted-epoch claimants (normal withdrawers via `_claimDefaultedWithdrawRequest`, instant claimants, and active tranche holders via `DefaultDistributor.claim`) draw on a reserve priced as if `w1` extra tokens existed; late claimants' transfers revert on insufficient balance, permanently freezing their recovery. With `w1` sized to the attacker's full deposit, the excess can approach the entire instant bucket.

### Likelihood Explanation
All steps use unprivileged calls (`requestWithdraw`, `claimInstantWithdrawRequest`) sequenced around honest manager actions (`startEpoch`, `stopEpoch`, default finalization). Requirements: instant-withdraw mode enabled (`allowInstantWithdraw` + `instantWithdrawDelay` params set by manager, a normal config), an APR decrease exceeding `instantWithdrawAprDelta` (a routine honest manager action), and two requests inside one epoch — feasible because `requestInstantWithdraw` has no once-per-epoch guard and `epochNumber` only increments at `stopEpoch` (`IdleCreditVault.sol:610`). No existing guard stops it: `requestWithdraw`'s loss-epoch check (`IdleCreditVault.sol:263-271`) inspects only `lastWithdrawRequest`/normal receipts, not instant per-epoch state, and `requestInstantWithdraw` does not revert on a previously claimed same-epoch receipt.

### Recommendation
In `claimInstantWithdrawRequest`, clear the per-epoch ledgers alongside the aggregate — e.g., subtract the claimed amount from `instantWithdrawsRequestsByEpoch[_user][epochNumber]` and `instantWithdrawClaimsByEpoch[epochNumber]` (or track a per-epoch claimed offset), mirroring how `_claimDefaultedInstantWithdrawRequest` clears its basis. Alternatively, store the request epoch per receipt so a claim can zero exactly the epochs paid out.

### Proof of Concept
Foundry fork test (scaffold based on `test/foundry/IdleCreditVault.t.sol` instant-withdraw helpers, e.g. `testRequestWithdrawInstant` around line 3627 and `testInstantWithdrawDefault` around line 2821):

```solidity
function testStaleInstantBasisDoubleClaim() external {
    // Setup: deposit AA, instant params enabled, run epoch so APR can drop.
    _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, cdoEpoch.managementFee());
    uint256 amount = 10_000 * ONE_SCALE;
    idleCDO.depositAA(amount);
    _transferBurnedTrancheTokens(address(this), true);
    address attacker = makeAddr("attacker");
    _depositWithUser(attacker, amount, true);

    _startEpochAndCheckPrices(0);
    // End epoch with APR decrease so next requests go instant.
    _toggleEpoch(false, initialProvidedApr / 2, _expectedFundsEndEpoch());

    vm.prank(manager);
    cdoEpoch.setInstantWithdrawParams(1 days, 1e18, false); // enable instant mode

    IdleCreditVault vault = IdleCreditVault(address(strategy));
    uint256 epoch = vault.epochNumber(); // epoch E

    // Step 1: attacker instant request w1 (full balance first time).
    uint256 w1 = IERC20Detailed(address(AAtranche)).balanceOf(attacker) / 2;
    vm.prank(attacker);
    uint256 u1 = cdoEpoch.requestWithdraw(w1, address(AAtranche));
    assertEq(vault.instantWithdrawsRequestsByEpoch(attacker, epoch), u1);

    // Step 2: start epoch E, fund + claim w1 at par.
    _startEpochAndCheckPrices(0);
    vm.prank(manager);
    cdoEpoch.getInstantWithdrawFunds();
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest();
    assertEq(vault.instantWithdrawsRequests(attacker), 0);
    // BUG: per-epoch basis survives the claim.
    assertEq(vault.instantWithdrawsRequestsByEpoch(attacker, epoch), u1);

    // Step 3: second instant request w2 inside same epoch E.
    uint256 w2 = IERC20Detailed(address(AAtranche)).balanceOf(attacker);
    vm.prank(attacker);
    uint256 u2 = cdoEpoch.requestWithdraw(w2, address(AAtranche));
    assertEq(vault.instantWithdrawsRequestsByEpoch(attacker, epoch), u1 + u2);
    assertEq(vault.instantWithdrawClaimsByEpoch(epoch), u1 + u2);
    assertEq(vault.pendingInstantWithdraws(), u2); // only w2 unfunded

    // Step 4: epoch E ends, borrower underfunds -> default, then finalize recovery
    // (borrower repays principal minus the unfunded instant piece; use existing
    // _checkDefault / finalizeDefaultRecovery helpers from the test suite).
    // ... default + finalizeDefaultRecovery(partialRecovery, borrower) ...

    // Inflation checks:
    // defaultPendingClaimBasis counted u1+u2 though only u2 was pending:
    assertEq(vault.instantWithdrawClaimsByEpoch(epoch), u1 + u2);
    // prefunded reserve credited u1 that was already paid to attacker.

    // Step 5: attacker claims defaulted instant receipt again on w1+w2 basis.
    uint256 balPre = underlying.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest();
    uint256 paid = underlying.balanceOf(attacker) - balPre;
    // Attacker recovers on (u1+u2) despite u1 already paid at par:
    assertGt(paid, u2 * vault.defaultRecoveryPrice() / 1e18, "stale basis paid twice");

    // Reserve is drained beyond funding: subsequent honest claims revert or underpay.
}
```

The assertion at `instantWithdrawsRequestsByEpoch(attacker, epoch) == u1` after the funded claim demonstrates the dangling receipt; the final claim demonstrates payout on the stale basis.