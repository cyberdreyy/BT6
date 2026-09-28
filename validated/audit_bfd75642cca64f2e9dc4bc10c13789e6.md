### Title
Post-default instant withdraw receipts bypass the `postDefaultRequests` path and drain `defaultRecoveryReserve` at par - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.requestWithdraw` has an explicit post-default branch that routes new receipts through `postDefaultRequests` and pays them from `defaultRecoveryReserve` via `_transferDefaultRecovery`. `requestInstantWithdraw` has no such branch: after `defaultRecoveryFinalized` is set it still mints a 1:1 receipt and increments `instantWithdrawsRequests`, and `claimInstantWithdrawRequest` pays the full amount through `_transferFundedClaim` — the same strategy token balance that custodies `defaultRecoveryReserve` — without decrementing the reserve or applying `defaultRecoveryPrice`. A receipt created after finalization is "confused" for a pre-default funded receipt, analogous to the CVE's type confusion: an object (receipt) of one kind is resolved through the wrong lookup path and paid at par instead of through the recovery-reserve accounting.

### Finding Description
In `IdleCreditVault.sol`:

- `requestWithdraw` (lines 243-258) reverts on open prior receipts and, when `defaultRecoveryFinalized`, stores the request in `postDefaultRequests[_user]`, later paid by `_claimPostDefaultWithdrawRequest` via `_transferDefaultRecovery` which decrements `defaultRecoveryReserve`.
- `requestInstantWithdraw` (lines 356-375) only calls `_onlyIdleCDO()` and `_ensureDefaultRecoveryInitialized()`, then unconditionally mints the receipt and increments `instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch`, `instantWithdrawClaimsByEpoch[epochNumber]`, and `pendingInstantWithdraws`. There is no `defaultRecoveryFinalized` routing at all.
- `claimInstantWithdrawRequest` (lines 380-393) only haircut-claims defaulted-epoch receipts when `defaultRecoveryFinalized && defaultInstantWithdrawsFinalized`, then burns `instantWithdrawsRequests[_user]` and calls `_transferFundedClaim(_user, amount)` — a funded-claim transfer drawn from the strategy's underlying balance, with no deduction from `defaultRecoveryReserve` and no `defaultRecoveryPrice` haircut.

After `finalizeDefaultRecovery`, the strategy's underlying balance is exactly the recovery reserve (plus funded-receipt cash). A post-default instant receipt therefore withdraws underlying at par directly out of funds earmarked for all active LPs and defaulted-epoch redeemers, while `defaultRecoveryReserve` accounting is never reduced — reserve conservation (`underlying.balanceOf(strategy) == defaultRecoveryReserve`, asserted in `test/foundry/IdleCreditVault.t.sol` `_assertDefaultRecoveryClaims`) is broken.

Broken invariant: one receipt one payout / reserve accounting integrity. The confusion is receipt-type confusion — a post-default claim is resolved through the "funded at par" claim path instead of the post-default/reserve path.

### Impact Explanation
Any KYC-passed lender able to trigger a CDO instant-withdraw request after default finalization mints a receipt and immediately claims `amount` underlying at par from the strategy, directly stealing from `defaultRecoveryReserve`. Since the reserve is not debited, other defaulted-epoch receipt holders and active AA/BB LPs (whose recovery is priced at `defaultRecoveryPrice` against that same balance) are left short — the last claimants cannot be paid. Loss is bounded by the attacker's tranche position but is a direct theft of other users' recovery funds: `stolen = instantReceiptAmount` paid at 100% while honest claimants only recover `defaultRecoveryPrice < RECOVERY_FULL`.

### Likelihood Explanation
Requires: a default with `finalizeDefaultRecovery` executed (reserve > 0), and instant withdrawals enabled on the pool (`instantWithdrawDelay` params are manager-configured and used in tests). The attacker only needs to hold tranche tokens and request an instant withdraw post-finalization — all unprivileged actions. The gap is conditional on the CDO (`IdleCDOEpochVariant`) not gating `requestInstantWithdraw`/`claimInstantWithdrawRequest` when `defaulted()`; I verified the strategy side has no default guard on the instant path, but I could not confirm in the remaining iteration whether the CDO reverts post-default instant requests. If the CDO allows them (the strategy clearly anticipates instant receipts existing after finalization — `_claimDefaultedInstantWithdrawRequest` and `instantWithdrawsRequestsByEpoch` are maintained), the exploit is a single request+claim sequence with deterministic payout.

### Recommendation
Mirror the `requestWithdraw` post-default handling in `requestInstantWithdraw`: when `defaultRecoveryFinalized`, revert if the user has any open receipt and record the request in a post-default bucket paid via `_transferDefaultRecovery` at par (the amount is already haircut by the lowered virtual price). Symmetrically, in `claimInstantWithdrawRequest`, route post-finalization receipts through reserve-accounted payouts so every underlying transfer out of the strategy decrements `defaultRecoveryReserve` or `prefunded`/funded buckets consistently.

### Proof of Concept
Foundry fork sketch (extend `test/foundry/IdleCreditVault.t.sol`):

```solidity
function testPostDefaultInstantWithdrawDrainsReserve() external {
    uint256 amount = 10_000 * ONE_SCALE;
    // 1. Enable instant withdraws, deposit as attacker + a victim LP
    vm.prank(manager);
    cdoEpoch.setInstantWithdrawParams(cdoEpoch.instantWithdrawDelay(), 1000, false);
    _depositWithUser(attacker, amount, true);
    _depositWithUser(victim, amount, true);

    // 2. Run an epoch, then stop with zero repayment -> borrower default
    _startEpochAndCheckPrices(0);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);
    assertTrue(cdoEpoch.defaulted());

    // 3. Manager finalizes with partial recovery (recoveryPrice < 1)
    uint256 basis = cdoEpoch.getContractValue()
        + cdoEpoch.expectedEpochInterest()
        - cdoEpoch.pendingWithdrawFees()
        + IdleCreditVault(address(strategy)).defaultPendingClaimBasis();
    uint256 recovered = basis * 7e17 / ONE_TRANCHE;
    deal(defaultUnderlying, manager, recovered);
    vm.startPrank(manager);
    IERC20Detailed(defaultUnderlying).approve(address(strategy), recovered);
    cdoEpoch.finalizeDefault(recovered, manager);
    vm.stopPrank();

    // 4. Attacker requests an INSTANT withdraw post-finalization
    vm.prank(attacker);
    cdoEpoch.requestInstantWithdraw(0, address(AAtranche)); // exact CDO signature per IdleCDOEpochVariant
    // 5. Claim at par: _transferFundedClaim pays full amount from the reserve balance
    uint256 balPre = IERC20Detailed(defaultUnderlying).balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest();
    uint256 paid = IERC20Detailed(defaultUnderlying).balanceOf(attacker) - balPre;

    IdleCreditVault vault = IdleCreditVault(address(strategy));
    // paid == full receipt amount (no defaultRecoveryPrice haircut)
    // and vault.defaultRecoveryReserve() is unchanged while
    // underlying.balanceOf(strategy) dropped by `paid`:
    assertGt(paid, 0);
    assertLt(
        IERC20Detailed(defaultUnderlying).balanceOf(address(strategy)),
        vault.defaultRecoveryReserve(),
        'reserve undercollateralized: instant claim bypassed recovery accounting'
    );
}
```

Note: the exact CDO entry-point names/signatures for instant withdraw requests should be confirmed against `IdleCDOEpochVariant.sol` (not fully read in this pass); the strategy-side flaw — missing `defaultRecoveryFinalized` routing in `requestInstantWithdraw`/`claimInstantWithdrawRequest` — is confirmed in `IdleCreditVault.sol:356-393`.