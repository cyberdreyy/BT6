### Title
`claimInstantWithdrawRequest` is not bound to the requesting epoch: receipts created after default finalization are paid at par from the funded-claim pool - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The tss-lib bug is a replay enabled because the proof is not bound to any session context. The structural analog in `IdleCreditVault` is `claimInstantWithdrawRequest`, whose funded-claim path pays the *aggregate* `instantWithdrawsRequests[_user]` with no check that the receipts were actually funded in a prior epoch. A user can mint a fresh instant-withdraw receipt after default recovery is finalized — a receipt that can never be funded because no further `stopEpoch`/`collectInstantWithdrawFunds` will run — and immediately claim it, with `_transferFundedClaim` releasing underlyings earmarked for earlier, legitimately funded claimants.

### Finding Description
In `claimInstantWithdrawRequest` (IdleCreditVault.sol:380-393), once `defaultRecoveryFinalized && defaultInstantWithdrawsFinalized`, the contract first clears the defaulted-epoch receipt via `_claimDefaultedInstantWithdrawRequest` (which is correctly epoch-bound to `defaultRecoveryEpoch`, lines 842-856). It then reads the *remaining aggregate* `instantWithdrawsRequests[_user]` and pays it at par through `_transferFundedClaim`, burning the receipt tokens.

`requestInstantWithdraw` (lines 356-375) increments `instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch[_user][currentEpoch]`, and `pendingInstantWithdraws` with no check that the pool is not defaulted/finalized, and crucially no requirement that the request will ever be collected via `collectInstantWithdrawFunds` (lines 398-403, only callable by the honest IdleCDO during a stop flow that no longer occurs post-default).

So the receipt is a "replayable claim": the funded-claim path never distinguishes a receipt created in epoch N and funded at `stopEpoch` from a receipt created in epoch N+k that was never funded. The only guard, `_transferFundedClaim` (lines 897-907), protects `defaultRecoveryReserve` but happily spends any other underlying balance — which, post-default, is exactly the funded-but-unclaimed claims of honest users. This mirrors the advisory precisely: a claim ("proof") that is not bound to its funding session/epoch is accepted as valid.

### Impact Explanation
Direct theft of funded withdrawal reserves. An attacker holding tranche tokens calls `requestInstantWithdraw` after default finalization, then `claimInstantWithdrawRequest`, and receives underlying 1:1 from the strategy balance, diluting or draining payouts owed to users whose earlier instant/normal requests were actually funded. Loss equals the attacker's receipt amount, bounded only by the non-reserve underlying balance of the vault.

### Likelihood Explanation
Requires that the IdleCDO still routes `requestInstantWithdraw`/`claimInstantWithdrawRequest` to the strategy after default finalization (the vault itself imposes no such gate — `_onlyIdleCDO` and `_ensureDefaultRecoveryInitialized` pass; `requestInstantWithdraw` has no `defaulted`/`closed` check). If the CDO variant blocks instant withdrawals once defaulted, the same shape of bug persists for the normal funded path: `_claimFundedWithdrawRequest` also pays the un-epoch-bound aggregate `withdrawsRequests[_user]`, and `requestWithdraw` after finalization is only diverted to `postDefaultRequests` when `defaultRecoveryFinalized` is set inside `requestWithdraw`'s branching — I could not fully verify that every post-finalization request path is diverted to the haircutted `postDefaultRequests` bucket rather than the par-paid `withdrawsRequests` aggregate. Caveat: the exact exploitability depends on CDO-side epoch gating I was unable to fully trace within the indexed context.

### Recommendation
Epoch-bind every claim path like the defaulted paths already are: after default finalization, refuse to add receipts to `instantWithdrawsRequests`/`withdrawsRequests` (route them to `postDefaultRequests` with the haircut, or revert), and in the funded paths pay only receipts whose request epoch was actually funded (e.g., track `fundedThroughEpoch` and require `requestEpoch < fundedThroughEpoch`), instead of paying the global aggregate. The fix mirrors tss-lib's: include the "session" (funding epoch) in what the claim is allowed to spend.

### Proof of Concept
Foundry fork PoC sketch (assumes CDO forwards `requestInstantWithdraw` post-finalization):

```solidity
// Post-default state: defaultRecoveryFinalized && defaultInstantWithdrawsFinalized
// Vault holds B underlying = funded-but-unclaimed receipts of honest users (reserve-excluded).

// 1. Attacker (tranche holder) requests an instant withdraw post-default.
vm.prank(attacker);
uint256 receipt = cdoEpoch.requestInstantWithdraw(attackerAmount, address(AAtranche));
// instantWithdrawsRequests[attacker] += receipt; never funded (no stopEpoch will run)

// 2. Attacker claims immediately at par.
uint256 pre = underlying.balanceOf(attacker);
vm.prank(attacker);
cdoEpoch.claimInstantWithdrawRequest(); // -> strategy.claimInstantWithdrawRequest(attacker)

// _claimDefaultedInstantWithdrawRequest finds no defaulted-epoch receipt (created after finalization),
// then _transferFundedClaim pays `receipt` from the funded pool:
assertEq(underlying.balanceOf(attacker) - pre, receipt, "unfunded receipt paid at par");
// Honest funded claimants later revert in _transferFundedClaim or receive less.
```

If the CDO blocks post-default instant requests, substitute `requestWithdraw` and check whether the post-finalization normal request lands in `withdrawsRequests` (par-paid aggregate) rather than `postDefaultRequests`; the finding stands on whichever route leaves an unfunded receipt in a par-paid aggregate.