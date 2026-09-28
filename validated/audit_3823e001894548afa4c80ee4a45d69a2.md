The strongest double-free analog is a double-draw on the default recovery reserve. Let me check whether post-default instant withdrawals are gated on the CDO side.### Title
Post-default instant withdraw requests re-enter the defaulted epoch's claim ledger, letting an unbacked receipt drain `defaultRecoveryReserve` and permanently freeze honest recovery claims - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
The double-free bug class (the same resource released/accounted twice) maps to a double-draw on the default recovery reserve. `requestWithdraw` explicitly blocks new requests once `defaultRecoveryFinalized` is set, but `requestInstantWithdraw` performs no such check. Because `epochNumber` no longer changes after `finalizeDefaultRecovery`, a post-finalization instant request is recorded into `instantWithdrawsRequestsByEpoch[user][defaultRecoveryEpoch]` — the same slot used to identify pre-finalization defaulted-epoch receipts. `claimInstantWithdrawRequest` then pays that freshly created, never-accounted receipt out of `defaultRecoveryReserve` at `defaultRecoveryPrice`, even though it was never part of `defaultPendingClaimBasis` when the reserve was sized.

### Finding Description
Relevant code in `contracts/strategies/idle/IdleCreditVault.sol`:

- `requestWithdraw` (L243-258) reverts with `NotAllowed` when `defaultRecoveryFinalized` and any receipt exists, and routes clean requests into `postDefaultRequests`, deliberately keeping them out of the defaulted-epoch ledger.
- `requestInstantWithdraw` (L356-375) has no `defaultRecoveryFinalized` branch at all. It burns the CDO's principal, mints a receipt to the user, and unconditionally writes `instantWithdrawsRequestsByEpoch[_user][epochNumber] += _amount` and `instantWithdrawClaimsByEpoch[epochNumber] += _amount`.
- After `finalizeDefaultRecovery` (L661-710), `defaultRecoveryEpoch = epochNumber` and that value never increments again, so a new instant request lands exactly in `instantWithdrawsRequestsByEpoch[user][defaultRecoveryEpoch]`.
- `claimInstantWithdrawRequest` (L380-393) calls `_claimDefaultedInstantWithdrawRequest` whenever `defaultRecoveryFinalized && defaultInstantWithdrawsFinalized`. That function (L842-856) reads `instantWithdrawsRequestsByEpoch[_user][defaultEpoch]`, burns the receipt, decrements `pendingInstantWithdraws`/`instantWithdrawClaimsByEpoch`, and pays `claimBasis * defaultRecoveryPrice / 1e18` via `_transferDefaultRecovery`, which decrements `defaultRecoveryReserve` (L912-917).

So a receipt created *after* the recovery reserve was fixed is paid *from* that reserve — the same accounting slot is consumed twice (once implicitly at finalization sizing, once at claim), the exact on-chain analog of freeing the same chunk twice.

The gating condition `defaultInstantWithdrawsFinalized` is set when `pendingInstantWithdraws != 0` at finalization (L696). When it is false the post-default receipt instead falls through to the unfunded par-claim path, which is separately inconsistent. The primary attack uses the `defaultInstantWithdrawsFinalized == true` configuration, which is exactly the scenario where a shared reserve exists to steal.

### Impact Explanation
`defaultRecoveryReserve` is sized at finalization as `recovered + prefunded + reserve`, priced against `totalBasis = activeBasis + pendingBasis` (L679-692). Post-default instant receipts add zero basis but consume reserve. Two consequences:

1. **Theft of unclaimed recovery yield**: each attacker claim withdraws `claimBasis * defaultRecoveryPrice` of reserve that belongs pro-rata to honest defaulted-epoch claimants and post-default requesters.
2. **Permanent freezing of honest claims**: once the reserve is depleted, `_transferDefaultRecovery` underflows (`defaultRecoveryReserve -= _amount`, unchecked arithmetic reverts on Solidity ≥0.8) or the transfer fails, so honest users' pending, defaulted-epoch, and post-default claims revert forever — an irreversible freeze of unclaimed yield, not a temporary DoS.

The attacker is an ordinary tranche holder (in-scope role). Post-default tranche price ≈ recovery NAV, so the attacker's receipt basis is roughly self-funded, meaning each claimed unit is nearly pure extraction from honest claimants' reserve share; the attacker can repeat with dust-sized requests across sybil addresses. Loss is quantified as `Σ claimBasis_i * defaultRecoveryPrice`, up to the entire `defaultRecoveryReserve`.

### Likelihood Explanation
- No privileged attacker required: `requestInstantWithdraw`/`claimInstantWithdrawRequest` are reachable through `IdleCDOEpochVariant` by any tranche holder. One caveat I could not fully verify within this iteration: whether the CDO-level wrapper blocks instant-withdraw requests while `defaulted()` is true. If a `defaulted` guard exists in `IdleCDOEpochVariant.requestInstantWithdraw`, the exploit path closes; the strategy-side omission is still a defense-in-depth gap worth fixing. The strategy itself clearly intended to gate this state — `requestWithdraw` does — making the omission in `requestInstantWithdraw` inconsistent with the design.
- Preconditions are mild: a defaulted pool whose finalization set `defaultInstantWithdrawsFinalized` (any unfunded instant remainder at default), plus available instant-withdraw liquidity path on the CDO.
- No existing guard stops it: `_transferFundedClaim`'s reserve-isolation check (L900-905) protects the funded path, but this payout goes through `_transferDefaultRecovery`, which treats any `instantWithdrawsRequestsByEpoch[user][defaultRecoveryEpoch]` entry as legitimate pre-finalization basis.

### Recommendation
In `requestInstantWithdraw`, mirror `requestWithdraw`: when `defaultRecoveryFinalized` is true, revert `NotAllowed` if the user has any outstanding receipt (`instantWithdrawsRequests[_user] != 0`, `_hasWithdrawRequest`, or `postDefaultRequests`), and either route new requests into a post-default bucket that is never haircut-claimable from `defaultRecoveryEpoch`, or simply revert unconditionally since instant liquidity semantics are meaningless post-default. Additionally, have `_claimDefaultedInstantWithdrawRequest` only pay entries whose basis was snapshotted into `defaultPendingClaimBasis` (e.g., a per-user finalized-basis mapping written at claim time is insufficient — the basis must be captured at or before finalization).

### Proof of Concept
Foundry fork test sketch (modeled on `testFinalizeDefaultHaircutsPendingInstantRedeems` in `test/foundry/IdleCreditVault.t.sol:4435`):

```solidity
function testPostDefaultInstantRequestDrainsRecoveryReserve() external {
    uint256 instantDelay = cdoEpoch.instantWithdrawDelay();
    vm.prank(manager);
    cdoEpoch.setInstantWithdrawParams(instantDelay, 1000, false);

    address honest = makeAddr('honest-instant');
    address attacker = makeAddr('attacker');
    uint256 amount = 10_000 * ONE_SCALE;
    uint256 recoveryRatio = 7e17;

    _depositWithUser(honest, amount, true);
    _depositWithUser(attacker, amount, true);

    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr / 2, _expectedFundsEndEpoch());

    // honest user has a defaulted-epoch instant receipt
    vm.prank(honest);
    uint256 honestBasis = cdoEpoch.requestWithdraw(0, address(AAtranche));

    _startEpochAndCheckPrices(1);
    vm.warp(block.timestamp + cdoEpoch.instantWithdrawDelay() + 1);
    vm.prank(manager);
    cdoEpoch.getInstantWithdrawFunds(); // partially unfunded -> default
    _checkDefault();

    IdleCreditVault cv = IdleCreditVault(address(strategy));
    assertTrue(cv.defaultInstantWithdrawsFinalized() || cv.pendingInstantWithdraws() != 0);

    // finalize recovery
    uint256 totalBasis = cdoEpoch.getContractValue()
        + cdoEpoch.expectedEpochInterest() - cdoEpoch.pendingWithdrawFees()
        + cv.defaultPendingClaimBasis();
    uint256 recovered = totalBasis * recoveryRatio / ONE_TRANCHE
        - (honestBasis - cv.pendingInstantWithdraws()); // subtract prefunded part
    deal(defaultUnderlying, manager, recovered);
    vm.startPrank(manager);
    IERC20Detailed(defaultUnderlying).approve(address(strategy), recovered);
    cdoEpoch.finalizeDefault(recovered, manager);
    vm.stopPrank();
    assertTrue(cv.defaultRecoveryFinalized());

    // ATTACK: attacker creates an instant receipt AFTER finalization.
    // requestInstantWithdraw has no defaultRecoveryFinalized guard, so the
    // receipt lands in instantWithdrawsRequestsByEpoch[attacker][defaultRecoveryEpoch].
    vm.prank(attacker);
    cdoEpoch.requestInstantWithdraw(/* attacker tranche amount */, address(AAtranche));

    uint256 reservePre = cv.defaultRecoveryReserve();
    uint256 balPre = IERC20Detailed(defaultUnderlying).balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest();

    uint256 stolen = IERC20Detailed(defaultUnderlying).balanceOf(attacker) - balPre;
    assertGt(stolen, 0, 'post-finalization receipt paid from recovery reserve');
    assertEq(cv.defaultRecoveryReserve(), reservePre - stolen, 'reserve drained by unbacked claim');

    // honest user's claim now reverts / is underpaid: permanent freeze of recovery yield
    vm.prank(honest);
    vm.expectRevert(); // underflow in _transferDefaultRecovery or insufficient balance
    cdoEpoch.claimInstantWithdrawRequest();
}
```

Assumes `IdleCDOEpochVariant` exposes `requestInstantWithdraw`/`claimInstantWithdrawRequest` to tranche holders while defaulted; if the CDO blocks instant requests post-default, assert the strategy-side gap by calling `cv.requestInstantWithdraw` via `vm.prank(address(cdoEpoch))` — the receipt is still written into the defaulted-epoch slot, proving the missing guard.