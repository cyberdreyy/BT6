### Title
Instant-withdraw receipt ledgers are never cleared on the funded-claim path, inflating default-recovery basis and prefunded reserve so recovery is over-promised and later claimants are permanently unpaid - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The kernel bug is a resource-reference leak in a cleanup path: `nfsd4_copy` file references are only released on the success path, so a failure (or a second cleanup flavor) leaves tracked references counted forever. The credit-vault analog lives in `IdleCreditVault.claimInstantWithdrawRequest`: a successful claim clears only the aggregate `instantWithdrawsRequests[user]`, but permanently leaks the per-epoch records `instantWithdrawsRequestsByEpoch[user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]`. Those leaked per-epoch records are exactly what `defaultPendingClaimBasis` and `_defaultPrefundedInstantReserve` read during `finalizeDefaultRecovery`, so a later borrower default in that epoch computes recovery on an inflated basis and a phantom prefunded reserve, leaving the recovery pool underfunded and later claimants frozen out.

### Finding Description
`requestInstantWithdraw` records a receipt in three places: `instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch[_user][epoch]`, and `instantWithdrawClaimsByEpoch[epoch]`, plus the unfunded counter `pendingInstantWithdraws` [1](#0-0) . The normal claim path `claimInstantWithdrawRequest` burns the aggregate receipt and zeroes only `instantWithdrawsRequests[_user]` — neither the per-user per-epoch entry nor the per-epoch claim total is decremented [2](#0-1) . `collectInstantWithdrawFunds` likewise decrements only `pendingInstantWithdraws` [3](#0-2) .

The only code that ever clears `instantWithdrawsRequestsByEpoch`/`instantWithdrawClaimsByEpoch` is the defaulted-claim path `_claimDefaultedInstantWithdrawRequest` [4](#0-3) . This mirrors the upstream bug precisely: one "flavor" of cleanup (the funded claim) skips releasing the tracked references that the other flavor (the default claim) relies on.

At default finalization, `defaultPendingClaimBasis` adds `instantWithdrawClaimsByEpoch[epochNumber]` to the haircut basis whenever `pendingInstantWithdraws != 0` [5](#0-4) , and `_defaultPrefundedInstantReserve` counts `instantWithdrawClaimsByEpoch[epochNumber] - pendingInstantWithdraws` as already-held strategy underlying [6](#0-5) . `finalizeDefaultRecovery` then prices recovery with `reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve` over `activeBasis + pendingBasis` [7](#0-6) .

Attack sequence (fixed-APR mode, epoch E running → stopped/defaulted):
1. Attacker calls `requestInstantWithdraw(amount)` during epoch E; CDO funds it via `getInstantWithdrawFunds` → `collectInstantWithdrawFunds` (`pendingInstantWithdraws` back to 0, but `instantWithdrawClaimsByEpoch[E]` stays `amount`).
2. Attacker calls `claimInstantWithdrawRequest` and receives `amount` underlying. The per-epoch records for epoch E remain set — the leaked references.
3. Victim V calls `requestInstantWithdraw(vAmount)` in the same epoch E; the CDO can only partially fund it, so `pendingInstantWithdraws != 0` at epoch end.
4. Borrower fails to repay; `stopEpoch` marks default and `finalizeDefaultRecovery` runs with `epochNumber == E` (no successful deposit bumps it in the defaulted stop). `instantWithdrawClaimsByEpoch[E]` now contains the attacker's already-paid `amount` plus V's `vAmount`.
5. `pendingBasis` is inflated by `amount` and `prefundedReserve` counts `amount` of underlying that was already paid out to the attacker and no longer sits in the strategy. `defaultRecoveryPrice` and `defaultRecoveryReserve` are computed on phantom funds.
6. When V claims via `_claimDefaultedInstantWithdrawRequest`, the payout is drawn from a reserve that is `amount` short relative to the priced claims; `_transferDefaultRecovery`/`safeTransfer` reverts for the last claimant, permanently freezing their recovery. The attacker's stale `instantWithdrawsRequestsByEpoch[E]` entry also makes `instantWithdrawsRequests[attacker] -= claimBasis` underflow (0 − amount), permanently bricking `claimInstantWithdrawRequest` for anyone holding a same-epoch stale entry [8](#0-7) .

### Impact Explanation
Broken invariant: funded-claim solvency / "one receipt one payout." Recovery funds equal to the attacker's already-claimed `amount` are double-counted as both prefunded reserve and claimable basis. The result is a shortfall in `defaultRecoveryReserve` exactly equal to the sum of all instant receipts that were claimed normally in the defaulted epoch: the last defaulted-epoch claimant(s) lose up to that full amount, and the reserve dust is unrecoverable. Additionally the underflow on stale `instantWithdrawsRequestsByEpoch` entries permanently freezes `claimInstantWithdrawRequest` for any user who both claimed an instant receipt in epoch E and holds another instant receipt — a permanent freezing of unclaimed yield with no privileged recovery path.

### Likelihood Explanation
Requires only unprivileged actions: an attacker instant-withdraws and claims during a running epoch (normal flow), and any one other instant request remains partially unfunded when the epoch ends in default. Instant withdrawals and borrower defaults are both first-class supported paths (`getInstantWithdrawFunds`, `stopEpoch` default, `finalizeDefault`). No privileged role is needed and no existing guard stops it: `defaultPendingClaimBasis` intentionally trusts `instantWithdrawClaimsByEpoch`, and nothing reconciles the leaked entries — the code comments even state per-epoch tracking exists precisely "so on finalization we can distinguish default-epoch pending instant receipts," which is the data that is never cleaned [9](#0-8) .

### Recommendation
Mirror the upstream fix — clean up both "flavors" of the record on every claim path. In `claimInstantWithdrawRequest`, after computing `amount`, iterate/lookup the request epoch(s) and clear `instantWithdrawsRequestsByEpoch[_user]` and decrement `instantWithdrawClaimsByEpoch`/`pendingInstantWithdraws`-related per-epoch totals consistently, or store the request epoch alongside the receipt so the funded claim can release the same per-epoch references that `_claimDefaultedInstantWithdrawRequest` releases. Equivalently, `defaultPendingClaimBasis`/`_defaultPrefundedInstantReserve` should derive the basis from still-outstanding receipts (e.g., recompute per-epoch claims net of already-claimed amounts) rather than a ledger that is only written and never cleared.

### Proof of Concept
Foundry fork test sketch (same setup as `test/foundry/IdleCreditVault.t.sol` instant-withdraw tests):

```solidity
function testInstantClaimLeakInflatesDefaultRecovery() external {
    // --- epoch E running ---
    _depositWithUser(attacker, amount, true);
    _depositWithUser(victim, amount, true);
    _startEpochAndCheckPrices(0);

    // attacker instant-withdraws; CDO funds it; attacker claims normally
    vm.prank(attacker);
    cdoEpoch.requestInstantWithdraw(amount);          // instantWithdrawClaimsByEpoch[E] += amount
    _fundInstantWithdraws(amount);                    // collectInstantWithdrawFunds: pendingInstantWithdraws -> 0
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest();           // pays amount; per-epoch ledgers LEAKED

    assertGt(strategy.instantWithdrawClaimsByEpoch(epochE), 0); // leaked basis
    assertEq(strategy.instantWithdrawsRequests(attacker), 0);

    // victim's instant request stays partially unfunded
    vm.prank(victim);
    cdoEpoch.requestInstantWithdraw(vAmount);         // pendingInstantWithdraws > 0 at epoch end

    // borrower repays less than owed -> default
    vm.warp(cdoEpoch.epochEndDate() + 1);
    _stopEpochAndCheckPrices(0, initialProvidedApr, 0);
    _checkDefault();

    uint256 recovered = /* partial recovery */;
    deal(defaultUnderlying, manager, recovered);
    vm.startPrank(manager);
    IERC20Detailed(defaultUnderlying).approve(address(strategy), recovered);
    cdoEpoch.finalizeDefault(recovered, manager);     // basis inflated by attacker's leaked `amount`
    vm.stopPrank();

    // victim's defaulted instant claim is underfunded -> last claimant reverts
    vm.prank(victim);
    vm.expectRevert();                                // ERC20 transfer exceeds defaultRecoveryReserve
    cdoEpoch.claimInstantWithdrawRequest();
}
```

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L366-374)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L387-392)
```text
    uint256 amount = instantWithdrawsRequests[_user];
    // burn strategy tokens from user
    _burn(_user, amount);

    instantWithdrawsRequests[_user] = 0;
    _transferFundedClaim(_user, amount);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L398-402)
```text
  function collectInstantWithdrawFunds(uint256 _amount) external {
    _onlyIdleCDO();
    if (_amount == 0) return;
    pendingInstantWithdraws -= _amount;
    underlyingToken.safeTransferFrom(idleCDO, address(this), _amount);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L644-648)
```text
  function defaultPendingClaimBasis() public view returns (uint256 basis) {
    basis = pendingWithdraws;
    if (pendingInstantWithdraws != 0) {
      basis += instantWithdrawClaimsByEpoch[epochNumber];
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L685-688)
```text
    uint256 prefundedReserve = _defaultPrefundedInstantReserve();
    uint256 reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve;
    // Recovery can be above par if the recovered funds exceed the computed basis.
    uint256 recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L716-722)
```text
  function _defaultPrefundedInstantReserve() internal view returns (uint256 prefundedReserve) {
    uint256 pendingInstant = pendingInstantWithdraws;
    if (pendingInstant == 0) return prefundedReserve;
    uint256 instantBasis = instantWithdrawClaimsByEpoch[epochNumber];
    if (instantBasis > pendingInstant) {
      prefundedReserve = instantBasis - pendingInstant;
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L844-855)
```text
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
