### Title
Post-default instant-withdraw receipts bypass the recovery haircut and reserve accounting, paying out at par - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The external bug is a use-after-free: a stale reference keeps pointing at a resource the runtime has already reclaimed. The credit-vault analog lives in `IdleCreditVault`: after `defaultRecoveryFinalized` is set, the vault's default accounting has "reclaimed" all pending claims into the isolated `defaultRecoveryReserve` priced at `defaultRecoveryPrice`. `requestWithdraw` correctly refuses or reroutes new requests into `postDefaultRequests` (pre-haircut, reserve-backed), but `requestInstantWithdraw` has no post-default branch at all — it keeps minting receipts and mutating `pendingInstantWithdraws` as if the default epoch state were still live. The resulting receipt is a "dangling" claim that `claimInstantWithdrawRequest` then pays **at par** via `_transferFundedClaim`, completely outside the recovery-price waterfall that every other post-default claimant is subject to.

### Finding Description
`requestWithdraw` (lines 243-258) explicitly handles `defaultRecoveryFinalized`: it reverts if the user has any outstanding request and otherwise books the request into `postDefaultRequests`, paid later only from `defaultRecoveryReserve` via `_transferDefaultRecovery`. `requestInstantWithdraw` (lines 356-375) performs `_ensureDefaultRecoveryInitialized()` but never checks `defaultRecoveryFinalized`. It burns CDO strategy tokens, mints a receipt to the user, and increments `instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch[_user][epochNumber]` and `pendingInstantWithdraws`.

On the claim side, `claimInstantWithdrawRequest` (lines 380-393) calls `_claimDefaultedInstantWithdrawRequest` which only clears `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]`. A receipt created post-default sits under the *current* `epochNumber` (which keeps increasing or stays non-equal to `defaultRecoveryEpoch`), so it survives that clearing untouched. The function then burns the full `instantWithdrawsRequests[_user]` remainder and calls `_transferFundedClaim` (lines 897-907), which transfers underlying at 100% as long as `balance - defaultRecoveryReserve >= amount`.

So while every other post-default withdrawal channel is forced through `defaultRecoveryPrice` (e.g. 40 cents on the dollar) and the bounded reserve, an instant-withdraw receipt minted *after* finalization is honored at par from any residual vault balance — borrower recoveries, repayments, or donations that landed after finalization. The "freed" invariant is that post-default all claims share `defaultRecoveryPrice`; the stale receipt escapes it.

### Impact Explanation
- If the defaulted vault holds `B` underlying beyond `defaultRecoveryReserve` (e.g. a late borrower recovery transfer, a subsequent repayment, or skim-ineligible dust), an unprivileged attacker (any tranche-token holder / KYC-passing lender) calls `requestInstantWithdraw` post-finalization and immediately `claimInstantWithdrawRequest`, receiving `min(B, receipt)` at par.
- Those funds economically belong to the recovery waterfall / remaining claimants; the attacker extracts up to 100% of residual balance while honest defaulted-epoch claimants only recover `defaultRecoveryPrice` fraction of basis. Direct theft of residual funds, quantified as up to the full non-reserve balance.
- The same stale-epoch gap applies to the loss-adjusted path: `requestInstantWithdraw` also lacks the `lossRecoveryPriceByEpoch` guard that `requestWithdraw` enforces at lines 263-271, so instant receipts are never haircutted by `stopEpochWithDuration` losses (`previewLossAdjustedWithdrawFunds`/`collectWithdrawFunds` only split losses over `pendingWithdraws`, not `pendingInstantWithdraws`).

### Likelihood Explanation
Requires a finalized default (honest borrower/admin sequence — the fulfiller/finalization flow, not attacker action) followed by any residual balance in the vault, plus one unprivileged `requestInstantWithdraw` + `claim` call. The missing guard is unconditional — there is no flag, KYC tier, or epoch check stopping the post-default instant request, and `_transferFundedClaim`'s reserve check explicitly permits spending everything above `defaultRecoveryReserve`. Likelihood is bounded by whether the IdleCDO layer still routes instant-withdraw calls to the strategy after default (needs verification in `IdleCDOEpochVariant`), and by how much non-reserve balance typically remains.

### Recommendation
- In `requestInstantWithdraw`, revert when `defaultRecoveryFinalized` is true (mirroring the strict `requestWithdraw` path), or route post-default instant requests into `postDefaultRequests`/reserve-backed accounting at the haircut price.
- Add a symmetric guard for `lossRecoveryPriceByEpoch`-affected receipts: either subject instant receipts to per-epoch loss pricing or block new instant requests while the pool carries an unclaimed loss-adjusted epoch.
- Ensure `pendingInstantWithdraws`/`instantWithdrawClaimsByEpoch` cannot be incremented after `defaultInstantWithdrawsFinalized`, since `_claimDefaultedInstantWithdrawRequest`'s clamp (`claimBasis >= pending ? 0 : pending - claimBasis`) silently normalizes cross-epoch contamination.

### Proof of Concept
Foundry fork PoC sketch (assuming `IdleCDOEpochVariant` still forwards instant-withdraw calls post-default — this must be confirmed):

```solidity
// test/foundry/PostDefaultInstantWithdraw.t.sol
function test_postDefaultInstantWithdrawPaysAtPar() public {
    // 1. Running epoch: attacker deposits into AA/BB tranche via CDO.
    deposit(attacker, 1000e6);

    // 2. Honest sequence: stopEpoch, borrower defaults, admin finalizes
    //    default recovery (defaultRecoveryFinalized = true,
    //    defaultRecoveryPrice = e.g. 0.4e18, reserve funded).
    stopEpoch();
    handleBorrowerDefault();
    finalizeDefaultRecovery(); // honest fulfiller/admin call

    // 3. Someone (borrower recovery / late repayment) sends underlying to the
    //    strategy so balance > defaultRecoveryReserve.
    deal(address(underlying), address(strategy), reserve + 500e6);

    // 4. Attacker (still holding strategy tokens / a fresh deposit through CDO)
    //    requests an instant withdraw AFTER finalization.
    vm.prank(attacker);
    cdo.requestInstantWithdraw(500e6); // -> strategy.requestInstantWithdraw

    // 5. Claim: _claimDefaultedInstantWithdrawRequest only clears
    //    instantWithdrawsRequestsByEpoch[attacker][defaultRecoveryEpoch] (=0),
    //    then pays the new receipt at par from funded balance.
    vm.prank(attacker);
    cdo.claimInstantWithdrawRequest();

    // 6. Attacker received 500e6 at 100% while all defaulted-epoch claimants
    //    recover only 40% of basis from the capped reserve.
    assertEq(underlying.balanceOf(attacker), attackerBalanceBefore + 500e6);
}
```

Uncertainty: I could not fully trace `IdleCDOEpochVariant`'s post-default call routing for instant withdrawals (whether `defaulted()`/`defaultRecoveryFinalized` gates exist at the CDO layer) before exhausting tool iterations. If the CDO blocks instant requests after default, the exploit reduces to the loss-adjusted haircut escape described in Likelihood — instant receipts requested before `stopEpochWithDuration` are still paid at par because the loss split ignores `pendingInstantWithdraws`, diluting normal withdrawers who absorb the full haircut. That variant stands even if the post-default path is gated upstream.