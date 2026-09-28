### Title
Claimed instant-withdraw receipts pollute `instantWithdrawClaimsByEpoch`, corrupting default-recovery price and freezing the recovery reserve - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`claimInstantWithdrawRequest` clears only the aggregate `instantWithdrawsRequests[_user]` but never clears the per-epoch ledgers `instantWithdrawsRequestsByEpoch[_user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]`. If the pool defaults in that same epoch while any other instant receipt is still unfunded, `defaultPendingClaimBasis()` and `_defaultPrefundedInstantReserve()` count the already-paid amount as outstanding claim basis and as strategy-held prefunded reserve. The finalized `defaultRecoveryPrice` is computed against a phantom basis/reserve, so recovery claims are mispriced and the last claimants' transfers revert, permanently freezing part of `defaultRecoveryReserve`.

### Finding Description
The analog of prototype pollution is an attacker writing a "property" (an epoch key) into shared accounting that the protocol never deletes and later treats as authoritative.

In `requestInstantWithdraw`, the vault records three things: `instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch[_user][epochNumber]`, and `instantWithdrawClaimsByEpoch[epochNumber]` [1](#0-0) . On a normal claim, only the aggregate is cleared: `instantWithdrawsRequests[_user] = 0` and the user's receipt tokens are burned, while both per-epoch mappings are left populated forever [2](#0-1) .

Those stale entries are read as live accounting during default finalization:

- `defaultPendingClaimBasis()` adds `instantWithdrawClaimsByEpoch[epochNumber]` to the claim basis whenever `pendingInstantWithdraws != 0` [3](#0-2) .
- `_defaultPrefundedInstantReserve()` returns `instantWithdrawClaimsByEpoch[epochNumber] - pendingInstantWithdraws`, i.e. it assumes every basis unit not in `pendingInstantWithdraws` is still-backed by underlying held in the strategy [4](#0-3) .
- `finalizeDefaultRecovery` folds that phantom amount into `reserveAmount` and `totalBasis`, producing a `recoveryPrice` and `defaultRecoveryReserve` that overstate the real tokens held by the contract [5](#0-4) .

The stale entries also remain readable by `_claimDefaultedInstantWithdrawRequest(_user)`, which looks up `instantWithdrawsRequestsByEpoch[_user][defaultEpoch]` — for the already-paid user this stays nonzero, so their defaulted-epoch claim path reverts on the `instantWithdrawsRequests[_user] -= claimBasis` underflow rather than being cleanly skipped [6](#0-5) .

Broken invariant: "one receipt, one payout" and reserve solvency. The default recovery reserve is debited for claims totaling `instantWithdrawClaimsByEpoch[defaultEpoch]` units of basis at `defaultRecoveryPrice`, but the contract only holds funds for `pendingInstantWithdraws` plus genuinely prefunded-but-unclaimed receipts — not for receipts already paid out.

### Impact Explanation
Direct loss and permanent freezing of unclaimed recovery:

- With claimed amount C, unfunded pending P, and external recovery R: `reserveAmount = R + (C + P) - P + other = R + C + ...` while `totalBasis = activeBasis + C + P + pendingWithdraws`. The recovery price is computed on a basis containing C phantom units, and the reserve is credited with C phantom units that were already transferred to the claimer. The contract's actual token balance is short by C relative to `defaultRecoveryReserve`.
- Result: claims are paid `claimBasis * defaultRecoveryPrice / 1e18` each until the underlying balance is exhausted; the tail claimants (honest instant-withdraw and normal-withdraw requesters of the defaulted epoch) hit an insufficient-balance `safeTransfer` and their claims are permanently frozen, even though `defaultRecoveryReserve` still shows them as owed.
- Quantified: the shortfall equals the full amount of same-epoch instant withdrawals that were funded and claimed before default finalization — up to the entire instant bucket for that epoch.

### Likelihood Explanation
Requires a specific but plausible sequence, all performed by honest privileged actors plus one unprivileged user:

1. Epoch E is running with instant withdrawals enabled (standard epoch variant with `setInstantWithdrawParams`).
2. Any unprivileged user (KYC-passing lender / tranche holder) requests an instant withdrawal; `collectInstantWithdrawFunds` funds it (`pendingInstantWithdraws` drops) and the user claims — leaving the stale per-epoch entries.
3. Another user requests an instant withdrawal in the same epoch E that remains unfunded (`pendingInstantWithdraws > 0`).
4. The borrower defaults and `finalizeDefaultRecovery` runs while `epochNumber == E` (default and finalization occur before the epoch advances — default is detected at `stopEpoch`/`_handleBorrowerDefault`, and the epoch counter only moves on a successful `deposit` during epoch rollover, so same-epoch finalization is the normal path).

No privileged misbehavior is required; the attacker only makes ordinary instant-withdraw requests and claims. Guard check: nothing in `claimInstantWithdrawRequest`, `collectInstantWithdrawFunds`, or `finalizeDefaultRecovery` clears or net-of-claims the per-epoch instant mappings, so no existing guard prevents it. (Caveat: I verified the write/clear sites shown above; if some other code path decrements `instantWithdrawClaimsByEpoch` on funding or claim, the issue would shrink to only unclaimed-but-funded receipts — the grep for related writes surfaced only the decrement inside `_claimDefaultedInstantWithdrawRequest` itself.)

### Recommendation
Keep per-epoch instant ledgers consistent with aggregate state:

- In `claimInstantWithdrawRequest`, also clear `instantWithdrawsRequestsByEpoch[_user][epoch]` entries and decrement `instantWithdrawClaimsByEpoch[epoch]` for each epoch that contributed to the claimed amount (or maintain a per-user "funded vs pending" split so only genuinely claimable basis remains in `instantWithdrawClaimsByEpoch`).
- Alternatively, decrement `instantWithdrawClaimsByEpoch[epochNumber]` inside `collectInstantWithdrawFunds` for the funded portion and track a separate `fundedInstantClaimsByEpoch` so `defaultPendingClaimBasis`/`_defaultPrefundedInstantReserve` only see basis actually backed by held tokens.
- Add an invariant test: after an instant claim, `instantWithdrawClaimsByEpoch[epoch]` must equal the sum of unclaimed, still-backed instant receipts for that epoch.

### Proof of Concept
Foundry fork-style test against the repo's own harness (`test/foundry/IdleCreditVault.t.sol` / `IdleCDOEpochQueue.t.sol` patterns):

```solidity
function testClaimedInstantReceiptPollutesDefaultRecovery() external {
    _useStandardEpochVariant();                       // standard (non-queue) epoch variant
    _stopCurrentEpochWithApr(10e18);                  // end epoch 0, set APR for epoch 1
    vm.prank(manager);
    cdoEpoch.setInstantWithdrawParams(100, 1e18, false);

    address alice = makeAddr('alice');
    address bob   = makeAddr('bob');
    _depositWithUser(alice, 100e6);                   // KYC'd lenders via helper
    _depositWithUser(bob,   100e6);
    vm.prank(manager);
    cdoEpoch.startEpoch();                            // epoch 1 running

    // Alice requests instant withdraw, it gets funded, she claims.
    uint256 aliceTranches = tranche.balanceOf(alice);
    vm.prank(alice);
    cdoEpoch.requestInstantWithdraw(aliceTranches, address(tranche)); // signature per variant
    skip(101);                                        // instantDelay passed
    // CDO/manager funding of instant queue happens at collectInstantWithdrawFunds
    // via the variant's instant-withdraw processing path.
    vm.prank(alice);
    cdoEpoch.claimInstantWithdrawRequest();

    // ASSERT POLLUTION: per-epoch ledgers still hold alice's paid amount.
    uint256 epoch = strategy.epochNumber();
    assertEq(strategy.instantWithdrawsRequests(alice), 0);              // cleared
    assertGt(strategy.instantWithdrawsRequestsByEpoch(alice, epoch), 0); // STALE
    assertGt(strategy.instantWithdrawClaimsByEpoch(epoch), 0);           // STALE

    // Bob requests instant withdraw in the SAME epoch; it stays unfunded.
    uint256 bobTranches = tranche.balanceOf(bob);
    vm.prank(bob);
    cdoEpoch.requestInstantWithdraw(bobTranches, address(tranche));
    assertGt(strategy.pendingInstantWithdraws(), 0);

    // Borrower defaults in this epoch; owner finalizes recovery with recovered funds R.
    _triggerBorrowerDefaultAndFinalize();   // helper: stopEpoch with default, finalizeDefaultRecovery(R)

    uint256 basis     = strategy.defaultPendingClaimBasis();
    uint256 reserve   = strategy.defaultRecoveryReserve();
    uint256 balance   = underlying.balanceOf(address(strategy));

    // basis includes alice's already-paid amount; reserve credits phantom prefunded funds.
    assertGt(basis, strategy.pendingInstantWithdraws() + strategy.pendingWithdraws());
    assertGt(reserve, balance, "reserve exceeds real tokens by alice's claimed amount");

    // Bob's defaulted instant claim is paid at the inflated-then-drained price;
    // the LAST claimant's transfer reverts on insufficient balance -> permanent freeze.
    vm.prank(bob);
    cdoEpoch.claimInstantWithdrawRequest();           // drains real funds
    // any subsequent defaulted-epoch claimant's claim reverts even though
    // defaultRecoveryReserve still shows them owed.
}
```

The key assertions are the two `assertGt` on the stale per-epoch entries after Alice's claim, and `reserve > balance` after finalization, which proves the recovery price was computed against tokens the contract no longer holds.

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L644-648)
```text
  function defaultPendingClaimBasis() public view returns (uint256 basis) {
    basis = pendingWithdraws;
    if (pendingInstantWithdraws != 0) {
      basis += instantWithdrawClaimsByEpoch[epochNumber];
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L685-692)
```text
    uint256 prefundedReserve = _defaultPrefundedInstantReserve();
    uint256 reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve;
    // Recovery can be above par if the recovered funds exceed the computed basis.
    uint256 recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;

    defaultRecoveryFinalized = true;
    defaultRecoveryReserve = reserveAmount;
    defaultRecoveryPrice = recoveryPrice;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L716-723)
```text
  function _defaultPrefundedInstantReserve() internal view returns (uint256 prefundedReserve) {
    uint256 pendingInstant = pendingInstantWithdraws;
    if (pendingInstant == 0) return prefundedReserve;
    uint256 instantBasis = instantWithdrawClaimsByEpoch[epochNumber];
    if (instantBasis > pendingInstant) {
      prefundedReserve = instantBasis - pendingInstant;
    }
  }
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
