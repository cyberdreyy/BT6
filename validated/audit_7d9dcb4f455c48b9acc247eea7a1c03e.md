### Title
Unfunded instant-withdraw receipts paid from other users' matured claims due to missing funding validation - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
CVE-2017-9865 is a missing-validation bug: `GfxImageColorMap::getGray` trusts an unvalidated index and reads past the buffer. The vault analog is `IdleCreditVault.claimInstantWithdrawRequest`: it pays out `instantWithdrawsRequests[_user]` immediately via `_transferFundedClaim` without validating that the corresponding borrower funds were actually pulled for that receipt through `collectInstantWithdrawFunds`. An instant receipt created after the epoch's funding step has already run is therefore honored out of the strategy's underlying balance, which holds underlying reserved for *other* users' funded normal and instant withdraw claims.

### Finding Description
Funding for instant withdrawals is a separate, discrete step: `collectInstantWithdrawFunds` decrements `pendingInstantWithdraws` and pulls underlying from the IdleCDO, and is driven by the epoch lifecycle (`getInstantWithdrawFunds` / start-epoch processing).

`requestInstantWithdraw` mints the user a 1:1 strategy-token receipt and increments both `instantWithdrawsRequests[_user]` and `pendingInstantWithdraws` at any time it is invoked.

`claimInstantWithdrawRequest` then does:

- `_burn(_user, amount)` where `amount = instantWithdrawsRequests[_user]`
- `instantWithdrawsRequests[_user] = 0`
- `_transferFundedClaim(_user, amount)`

There is no check that this receipt's basis was ever collected — no per-epoch funding flag, no comparison against a funded-balance counter, and no epoch gating. The IdleCDO-side wrapper `IdleCDOEpochVariant.claimInstantWithdrawRequest` only checks `allowInstantWithdraw`. This is the analog of the missing color-map validation: a record (the instant receipt) is consumed as if it were backed, with no validation that the backing step ran.

If an instant request can be created in a phase where `collectInstantWithdrawFunds` will not run again before the claim (e.g., during a running epoch after the start-of-epoch funding pull, or between the funding pull and the claim window), the claim burns the receipt and transfers underlying that was collected to satisfy *previous* requesters' matured claims — the same reserve `_claimFundedWithdrawRequest` pays from.

Note on verification limits: I confirmed the claim path lacks a funding check (`IdleCreditVault.sol:380-393`, `398-403`), but the exact phase gating inside `IdleCDOEpochVariant.requestInstantWithdraw` / `getInstantWithdrawFunds` was not fully read in this pass; the finding holds wherever a request can outlive the funding step that precedes its claim.

### Impact Explanation
Direct theft of user funds. The strategy's underlying balance is the payout reserve for matured normal withdraw receipts (`_claimFundedWithdrawRequest` → `_transferFundedClaim`) and funded instant receipts. An attacker who posts an unfunded instant request and claims it withdraws underlying earmarked for honest users who already burned tranche tokens and are waiting to claim. Loss is bounded by the vault's claimable reserve; the victims' claims then either revert on insufficient balance or are permanently underfunded (permanent freezing of unclaimed withdrawals). One receipt — one payout is broken: the payout is real, but the funding leg never happened for that receipt.

### Likelihood Explanation
- Attacker needs only tranche tokens (a KYC-passing lender qualifies) and the `allowInstantWithdraw` flag enabled.
- Cost is one `requestInstantWithdraw` + one `claimInstantWithdrawRequest` in the same phase.
- The only mitigant is whether the CDO hard-blocks instant requests outside the pre-funding window; if requests are accepted during a running epoch (as the inline comment "funds will get transferred from borrower when epoch starts" implies they can be requested before, but nothing on the claim side enforces that ordering), the exploit is a single-transaction sequence.
- Even if requests are buffer-only, any path that lets a request survive past its intended funding pull (e.g., request → funding runs for a smaller snapshot → claim) hits the same missing check.

### Recommendation
- Track funded instant-withdraw basis explicitly (e.g., `fundedInstantWithdraws` incremented in `collectInstantWithdrawFunds` alongside `pendingInstantWithdraws -= _amount`), and in `claimInstantWithdrawRequest` pay only `min(instantWithdrawsRequests[_user], fundedInstantWithdraws)` or revert until funded.
- Alternatively, record instant requests per epoch (already partially done via `instantWithdrawsRequestsByEpoch`) and require `instantWithdrawClaimsByEpoch[reqEpoch]` to have been collected before allowing claims for that epoch — i.e., validate the index before consuming the record, exactly the fix applied to `GfxImageColorMap::getGray`.
- Add an invariant test: `claimInstantWithdrawRequest` must never transfer more underlying than cumulative `collectInstantWithdrawFunds` minus cumulative instant claims.

### Proof of Concept
Foundry fork sketch (mode: instant withdrawals enabled, non-defaulted, epoch running after start-of-epoch funding):

```solidity
function testUnfundedInstantWithdrawDrainsReserve() external {
    // Setup: victim deposits, requests normal withdraw, epoch runs, stopEpoch
    // pulls victim's pendingWithdraws into the strategy via collectWithdrawFunds.
    // Strategy now holds `victimClaim` underlying reserved for the victim.

    // Attacker (KYC'd tranche holder) requests an instant withdraw AFTER
    // getInstantWithdrawFunds/collectInstantWithdrawFunds already ran for
    // this epoch, so pendingInstantWithdraws grows but no funds are pulled.
    uint256 attackerShares = aaTranche.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.requestInstantWithdraw(attackerShares, address(aaTranche));

    // Claim immediately: burns receipt and transfers underlying from the
    // strategy reserve that belongs to the victim's funded claim.
    uint256 pre = underlying.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest();
    uint256 stolen = underlying.balanceOf(attacker) - pre;
    assertGt(stolen, 0);

    // Victim's previously funded claim now underflows/reverts on the same reserve.
    vm.prank(victim);
    vm.expectRevert();
    cdoEpoch.claimWithdrawRequest();
}
```

If `requestInstantWithdraw` reverts during a running epoch (i.e., the CDO gates it to the buffer window before the funding pull), this specific exploit path is closed and the finding reduces to a defense-in-depth gap; that gating line should be confirmed before treating this as exploitable.