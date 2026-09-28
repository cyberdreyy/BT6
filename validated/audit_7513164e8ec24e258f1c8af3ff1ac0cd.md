### Title
Stale-epoch instant-withdraw receipts are excluded from default recovery basis and then permanently frozen - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
Like CVE-2018-20546's `get_rgba_default` reading the wrong entry for the unhandled "default bpp" case, `IdleCreditVault` resolves a user's instant-withdraw claim by looking up only the *current* epoch key (`defaultRecoveryEpoch`/`epochNumber`). Instant receipts recorded under an earlier epoch via `instantWithdrawsRequestsByEpoch`/`instantWithdrawClaimsByEpoch` silently hit the "default" path (`instantWithdrawsRequests` aggregate), are excluded from the recovery `totalBasis`, and then revert in `_transferFundedClaim` because the reserve guard forbids spending `defaultRecoveryReserve`. The receipt is permanently unclaimable.

### Finding Description
`requestInstantWithdraw` keys per-epoch accounting by the request-time `epochNumber` (`instantWithdrawsRequestsByEpoch[_user][currentEpoch]`, `instantWithdrawClaimsByEpoch[currentEpoch]`) and adds to the epoch-agnostic `pendingInstantWithdraws`. Only `collectInstantWithdrawFunds` decrements `pendingInstantWithdraws`; `stopEpoch`/`startEpoch` do not zero it, so an instant request that is not (fully) collected at epoch start carries into later epochs under its original epoch key.

On default finalization, `defaultPendingClaimBasis` adds only `instantWithdrawClaimsByEpoch[epochNumber]` — i.e. receipts created in the *default* epoch — while `pendingInstantWithdraws != 0` can be entirely attributable to older-epoch receipts. The older receipts' claim basis is therefore omitted from `totalBasis` in `finalizeDefaultRecovery`, so `defaultRecoveryReserve` is not sized to pay them, even at the haircut price.

After `defaultRecoveryFinalized`, `claimInstantWithdrawRequest` calls `_claimDefaultedInstantWithdrawRequest` only when `defaultInstantWithdrawsFinalized`, and that function reads `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` — again only the current epoch. For an older-epoch receipt it returns 0 without clearing anything, so execution falls through to `amount = instantWithdrawsRequests[_user]; _burn(_user, amount); _transferFundedClaim(_user, amount)`. Because `defaultRecoveryReserve != 0`, `_transferFundedClaim` enforces `balance - reserve >= amount`; the reserve contains no backing for this claim, so the call reverts with `NotAllowed` (or pays and underflows `defaultRecoveryReserve` for rightful claimants, which also reverts for later claims). The user's receipt tokens can never be redeemed: the burned CDO strategy tokens are gone and no path clears the stale-epoch entry.

### Impact Explanation
Permanent freezing of lender funds. Any tranche holder whose instant-withdraw request remained unfunded across an epoch boundary and whose vault later finalizes a default loses their entire request principal (receipt tokens are burned-able but the claim always reverts on the reserve guard). Loss equals the full unfunded instant claim amount for each affected user; the same mismatch also mis-sizes `recoveryPrice` for all other claimants (the excluded basis is still inside `pendingInstantWithdraws`, so funded-instant recovery math and `defaultInstantWithdrawsFinalized` are computed against an inconsistent basis).

### Likelihood Explanation
Requires: (a) `allowInstantWithdraw` enabled, (b) an instant request that is not fully collected at `startEpoch` (partial CDO liquidity makes `collectInstantWithdrawFunds` cover only part of `pendingInstantWithdraws`, leaving a remainder that persists across `stopEpoch`/`startEpoch`), and (c) a later borrower default finalized via `finalizeDefaultRecovery`. All steps use only unprivileged user actions plus honest manager/borrower sequencing; no privileged misbehavior is needed.

### Recommendation
Track instant-request basis per user across all outstanding epochs (or store a per-user list of request epochs), and in `defaultPendingClaimBasis` include the *entire* unfunded instant remainder (`pendingInstantWithdraws` plus prefunded portions across epochs), not only `instantWithdrawClaimsByEpoch[epochNumber]`. In `_claimDefaultedInstantWithdrawRequest`, clear and haircut all epochs contributing to `instantWithdrawsRequests[_user]` rather than only `defaultRecoveryEpoch`. Add a regression test where an instant request survives a stop/start boundary unfunded and the pool then defaults.

### Proof of Concept
Foundry fork/harness sketch (pattern follows `test/foundry/IdleCreditVault.t.sol`):

```solidity
// setup: allowInstantWithdraw = true, user deposits AA, epoch starts
uint256 minted = idleCDO.depositAA(10_000e18);
_startEpochAndCheckPrices(0);

// borrower/programmable vault under-funds instant queue so
// collectInstantWithdrawFunds covers only part: pendingInstantWithdraws stays > 0
cdoEpoch.requestInstantWithdraw(halfOfMinted, address(AAtranche)); // epoch 0 key
// ... stopEpoch(0) then startEpoch(1) with insufficient liquidity to collect all
_stopEpochAndCheckPrices(0, apr, funds);   // epochNumber -> 1, pendingInstantWithdraws unchanged
// user cannot claim: instant receipt still unfunded, sits under epoch key 0

// borrower defaults during epoch 1; manager/keeper finalizes
_handleBorrowerDefault();
finalizeDefaultRecovery(recovered, source); // totalBasis misses user's epoch-0 instant basis
assert(strategy.defaultRecoveryFinalized());

// user claim: _claimDefaultedInstantWithdrawRequest reads epoch-1 key -> 0,
// falls through to _transferFundedClaim -> reserve guard reverts
vm.prank(user);
vm.expectRevert(NotAllowed.selector);
cdoEpoch.claimInstantWithdrawRequest();   // receipt permanently frozen
```

Uncertainty: I could not fully trace `IdleCDOEpochVariant`'s instant-funding path (the last grep only confirmed the references exist) to verify that a partial `collectInstantWithdrawFunds` is reachable while `pendingInstantWithdraws` persists across an epoch boundary. If the CDO always either fully collects or clears instant pendings before `stopEpoch`, the trigger narrows, but the epoch-keyed basis/claim mismatch in `defaultPendingClaimBasis` vs `_claimDefaultedInstantWithdrawRequest` remains a latent inconsistency worth fixing regardless.