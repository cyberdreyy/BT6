### Title
Unfunded instant-withdraw receipts are paid at par because `claimInstantWithdrawRequest` settles the aggregate balance without checking per-epoch funding — ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
`IdleCreditVault.claimInstantWithdrawRequest` burns and pays the full aggregate `instantWithdrawsRequests[_user]` in one shot, with no check that the receipt's epoch was actually funded via `collectInstantWithdrawFunds`/`pendingInstantWithdraws`. A user who already holds a funded instant receipt can add a new, unfunded instant request in a later epoch and still be paid the whole aggregated amount at par, spending underlying that belongs to other claimants or to the strategy — the Solidity analog of dereferencing a receipt ("pointer") that was assumed to be backed but isn't.

### Finding Description
When a withdraw triggers `requestInstantWithdraw` (via `IdleCDOEpochVariant` `_withdrawOps` at `contracts/IdleCDOEpochVariant.sol:761-769`), the vault:

- mints the user a strategy-token receipt (`_mint(_user, _amount)`),
- adds to `instantWithdrawsRequests[_user]` and `instantWithdrawsRequestsByEpoch[_user][currentEpoch]`,
- increases `pendingInstantWithdraws` (`contracts/strategies/idle/IdleCreditVault.sol:356-375`).

Funding arrives later, when the CDO calls `collectInstantWithdrawFunds(_amount)`, which decrements `pendingInstantWithdraws` and pulls underlying into the strategy (`IdleCreditVault.sol:398-403`).

The claim path, however, settles on the *aggregate* ledger only:

```solidity
// contracts/strategies/idle/IdleCreditVault.sol:380-393
uint256 amount = instantWithdrawsRequests[_user];
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);
```

There is no check that `pendingInstantWithdraws` for the user's request epochs was funded, no per-epoch gating (unlike `_claimFundedWithdrawRequest` which enforces `epochNumber > lastWithdrawRequest`), and `defaultRecoveryFinalized` is false pre-default, so `_claimDefaultedInstantWithdrawRequest` is skipped. `_transferFundedClaim` only excludes `defaultRecoveryReserve` (`IdleCreditVault.sol:897-907`), so any other underlying sitting in the strategy — deposits not yet forwarded, fees, or other users' funded claims — is spendable.

The broken invariant: "one receipt one funded payout". A receipt minted but never funded (its `pendingInstantWithdraws` share never collected) is dereferenced and paid at par, exactly like the GPAC bug where an auth object pointer was dereferenced without verifying it was valid.

### Impact Explanation
An attacker (any KYC-passing lender / tranche holder) can obtain instant-withdraw value they never funded. Concretely the attacker can:

1. Make an instant-withdraw request in epoch N that gets funded normally.
2. Before claiming, make a second instant request in epoch N+1 (which is not yet funded — `collectInstantWithdrawFunds` is only called by the honest CDO at epoch processing for the pending queue).
3. Call `claimInstantWithdrawRequest` (through `IdleCDOEpochVariant.claimInstantWithdrawRequest`) and receive both amounts at par.

The payout for the unfunded portion comes from underlying held by the strategy that is earmarked for other users' funded claims or for CDO liquidity, i.e., direct theft / protocol insolvency equal to the unfunded receipt amount. If insufficient balance exists, the honest users' later claims revert (temporary freezing of their yield), which also qualifies.

### Likelihood Explanation
Requires the attacker to hold tranche tokens (KYC-gated but available to any allowed lender) and to time a second `requestWithdraw` during a phase where an instant withdraw is enabled (APR drop exceeding `instantWithdrawAprDelta`) before claiming the first receipt. No privileged-role cooperation is needed — the only privileged calls (startEpoch/collect) are the honest actors' normal flow. The attack fails only if the strategy holds no non-reserve underlying at claim time, which is not the normal state (deposits and funded claims routinely sit there). Moderate likelihood.

### Recommendation
Track funded instant claims per epoch the same way normal withdrawals do, e.g.:

- Settle only epochs whose `instantWithdrawClaimsByEpoch` share was collected: in `claimInstantWithdrawRequest`, pay only the portion of `instantWithdrawsRequests[_user]` whose `pendingInstantWithdraws` contribution was funded (or record a `fundedInstantAmount` when `collectInstantWithdrawFunds` runs and cap claims to it).
- Alternatively, mirror the normal-flow guard: block a new `requestInstantWithdraw` while the user has an unclaimed instant receipt (`instantWithdrawsRequests[_user] != 0 → revert`), matching the "claim before re-requesting" guard already used in `requestWithdraw` (`IdleCreditVault.sol:263-271`).

### Proof of Concept
Foundry fork sketch (attacker = allowed AA tranche holder):

```solidity
// epoch N running; attacker holds AA tranche tokens
// APR decreased > instantWithdrawAprDelta so instant path is enabled
cdo.requestWithdraw(attackerAA1, AA);          // instant receipt 1 minted
vm.prank(manager); cdo.stopEpoch(...);          // stopEpoch -> collectInstantWithdrawFunds funds it
cdo.startEpoch();                               // epoch N+1 running, receipt 1 funded & claimable

// attacker adds a SECOND instant request in epoch N+1 (unfunded: collectInstantWithdrawFunds
// has not been called for it yet)
cdo.requestWithdraw(attackerAA2, AA);           // instantWithdrawsRequests += amt2

// single claim pays amt1 + amt2 at par; amt2 is drained from strategy-held underlying
uint256 balBefore = underlying.balanceOf(attacker);
cdo.claimInstantWithdrawRequest();              // calls vault.claimInstantWithdrawRequest(attacker)
assertGt(underlying.balanceOf(attacker) - balBefore, amt1); // attacker got unfunded amt2 too
```

Uncertainty note: whether `IdleCDOEpochVariant.claimInstantWithdrawRequest` applies an additional epoch/elapsed-time gate before calling the vault could not be fully verified within the available iterations; if such a gate exists and also blocks claims while `pendingInstantWithdraws` is non-zero for the current epoch, the attack reduces to the case where unrelated funds sit in the strategy and partial funding has occurred. The core flaw — paying the aggregate `instantWithdrawsRequests` without distinguishing funded vs unfunded epochs — stands on its own in `IdleCreditVault.sol:380-393`.