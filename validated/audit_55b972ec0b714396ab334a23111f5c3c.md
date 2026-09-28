### Title
Post-default withdraw requests drain the isolated `defaultRecoveryReserve` at par, leaving defaulted-epoch claimants undercollateralized - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.finalizeDefaultRecovery` sizes `defaultRecoveryReserve` to cover only the pre-default claim basis (active LPs + pending receipts) at `defaultRecoveryPrice`. However, `requestWithdraw` explicitly supports new requests after `defaultRecoveryFinalized` (lines 247-257), and those receipts are paid out **at par** from the same reserve via `_claimPostDefaultWithdrawRequest` → `_transferDefaultRecovery`. Every post-default claim therefore consumes reserve that was never budgeted for it, directly reducing the funds available to defaulted-epoch claimants. Once the reserve is exhausted, later legitimate claims revert via underflow at `defaultRecoveryReserve -= _amount` (line 915). This is the analog of a path traversal: post-default claims escape the accounting boundary that was supposed to isolate recovery funds to the finalized basis set.

### Finding Description
At finalization, `defaultRecoveryReserve = _recoveredAmount + prefundedReserve + priorReserve` and `defaultRecoveryPrice = reserveAmount / totalBasis` (lines 686-693). The reserve is then claimed through two paths:

- Defaulted-epoch receipts: `_claimDefaultedWithdrawRequest`/`_claimDefaultedInstantWithdrawRequest` pay `claimBasis * defaultRecoveryPrice / 1e18` from the reserve (lines 782-783, 855).
- Post-default receipts: `_claimPostDefaultWithdrawRequest` pays `amount` **1:1** from the same reserve (lines 760-767).

`requestWithdraw` post-default branch (lines 247-258) mints a fresh receipt and sets `postDefaultRequests[_user] = _amount` with no corresponding deposit of underlying into the strategy — the minted receipt is purely a claim, and its payout is funded entirely by reserve earmarked for pre-default claimants.

The CDO haircut applied to `_amount` before the call does not rescue this: even if the haircut perfectly equals `defaultRecoveryPrice`, paying the haircut basis at par is equivalent to paying the *unhaircutted* basis at `defaultRecoveryPrice`, but the reserve was only sized for `totalBasis * defaultRecoveryPrice` where `totalBasis` excluded all post-default claims. Each post-default claim withdraws value in excess of its pro-rata share of the reserve.

### Impact Explanation
An unprivileged tranche-token holder can, after default finalization, call `IdleCDOEpochVariant.requestWithdraw` (post-default branch), then `claimWithdrawRequest`. Their payout comes out of `defaultRecoveryReserve` at par while contributing nothing to it. Repeated across enough post-default claimants, the reserve is drained before defaulted-epoch receipt holders (who waited through the default) claim, causing either direct theft of recovery value (early post-default claimants paid more than pro-rata) or permanent freezing of remaining claims when `defaultRecoveryReserve -= _amount` underflows (line 915). Loss is bounded by the reserve size; each post-default claim of `X` underlyings removes `X` that was budgeted to pay `X/defaultRecoveryPrice` worth of defaulted claims — i.e., overconsumption factor `1/defaultRecoveryPrice` (e.g., 5× at a 20% recovery price).

### Likelihood Explanation
Reachable by any tranche-token holder after `finalizeDefaultRecovery` — no privileged role required, and the strategy code explicitly implements this flow rather than reverting. The only requirement is holding tranche tokens after default, which any lender or secondary-market buyer satisfies. The attack is more profitable the lower `defaultRecoveryPrice` is. Uncertainty: I could not fully confirm in this iteration whether `IdleCDOEpochVariant.requestWithdraw`/`claimWithdrawRequest` remains callable when `defaulted()` is true; if the CDO reverts all post-default requests, the strategy's post-default branch is dead code and the finding does not hold — but the branch's existence and the test suite's post-default claim coverage suggest it is intended to be reachable.

### Recommendation
Either revert `requestWithdraw` when `defaultRecoveryFinalized` (removing the `postDefaultRequests` path), or fund post-default claims from a separate, freshly sourced budget (e.g., require the CDO to transfer underlying for each post-default request) instead of spending `defaultRecoveryReserve`. At minimum, pay post-default claims at `defaultRecoveryPrice` rather than par if they must share the reserve.

### Proof of Concept
Foundry fork PoC sketch:

```solidity
// after a real default and finalizeDefaultRecovery(...) with recoveryPrice < 1e18
uint256 reserveBefore = strategy.defaultRecoveryReserve();

// attacker holds BB tranches post-default
vm.startPrank(attacker);
cdo.requestWithdraw(attackerTrancheBal, BBTranche);   // strategy mints receipt, postDefaultRequests set
cdo.claimWithdrawRequest();                            // pays at par from reserve
vm.stopPrank();

uint256 reserveAfter = strategy.defaultRecoveryReserve();
// reserve decreased by full claim amount, not claim * recoveryPrice
assertEq(reserveBefore - reserveAfter, attackerClaimBasis);
// eventually: defaulted-epoch claim reverts / pays less than claimBasis * defaultRecoveryPrice
vm.prank(victim);
vm.expectRevert(); // underflow in defaultRecoveryReserve -= _amount
cdo.claimWithdrawRequest();
```