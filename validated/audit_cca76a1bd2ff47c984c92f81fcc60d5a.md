### Title
Post-default instant-withdraw receipts minted by `requestInstantWithdraw` can never be funded or claimed, permanently freezing user funds — ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
The external report describes a payout path that only transfers one "bucket" of value, leaving the rest permanently stuck in the contract. The direct analog in idle-tranches is `IdleCreditVault.requestInstantWithdraw`: after `finalizeDefaultRecovery` has run, the function still mints instant-withdraw receipts and increments `pendingInstantWithdraws`, but no post-default code path can ever fund those receipts. `claimInstantWithdrawRequest` then either reverts on the `defaultRecoveryReserve` guard in `_transferFundedClaim` (freezing the receipt, and in the same tx also blocking the user's defaulted-epoch instant claim) or pays the receipt at par out of unrelated funded-claim balances.

### Finding Description
`requestWithdraw` explicitly handles the post-default state: it requires the user to clear all old receipts, then creates a `postDefaultRequests` entry that is paid 1:1 from `defaultRecoveryReserve` via `_transferDefaultRecovery` (lines 247–257, 760–767). `requestInstantWithdraw` has no such handling (lines 356–375):

```solidity
function requestInstantWithdraw(uint256 _amount, address _user) external {
  _onlyIdleCDO();
  _ensureDefaultRecoveryInitialized();   // no-op once initialized
  _burn(msg.sender, _amount);            // burns CDO strategy tokens worth only defaultRecoveryPrice
  _mint(_user, _amount);
  instantWithdrawsRequests[_user] += _amount;
  instantWithdrawsRequestsByEpoch[_user][epochNumber] += _amount;
  instantWithdrawClaimsByEpoch[epochNumber] += _amount;
  pendingInstantWithdraws += _amount;
}
```

No underlying is collected for this receipt. Funding normally comes from `collectInstantWithdrawFunds`, which is driven by the epoch machinery (`getInstantWithdrawFunds` at `startEpoch`), and that machinery does not run once the CDO is `defaulted` and `defaultRecoveryFinalized` is set.

On `claimInstantWithdrawRequest` (lines 380–393), `_claimDefaultedInstantWithdrawRequest` only clears receipts keyed to `defaultRecoveryEpoch`; the post-default receipt sits in `instantWithdrawsRequestsByEpoch[user][epochNumber]` for a different (or the same-but-unfunded) epoch and falls through to `_transferFundedClaim`. `_transferFundedClaim` (lines 897–907) reverts unless `balance - defaultRecoveryReserve >= amount`, i.e. it can only pay from non-reserve cash that belongs to already-funded legacy/instant claims — and if none exists it reverts, so the user's receipt is unclaimable forever while their strategy tokens were already burned from the CDO.

### Impact Explanation
- Permanent freezing: a lender who instant-withdraws after default finalization has their CDO strategy tokens burned and receives a receipt that can never be redeemed (no funding path exists post-default). Loss equals the full receipt amount in underlying terms at the recovery price.
- Theft of yield/funded claims: if the strategy happens to hold non-reserve underlying (e.g., funded receipts not yet claimed), the post-default instant receipt is paid at par (1:1) even though the burned CDO tokens were only worth `defaultRecoveryPrice`, diluting other funded claimants.
- Griefing/DoS amplifier: a user holding a defaulted-epoch instant receipt who submits a new post-default instant request will have `claimInstantWithdrawRequest` revert in `_transferFundedClaim` after `_claimDefaultedInstantWithdrawRequest` already ran — the revert rolls back the whole transaction, blocking even their legitimate recovery payout until the state can be resolved.

### Likelihood Explanation
An unprivileged KYC'd lender can trigger this with a single `requestInstantWithdraw` call through the CDO after `finalizeDefault`/`finalizeDefaultRecovery`. No privileged misbehavior is required; the missing post-default branch in `requestInstantWithdraw` (contrast with `requestWithdraw` lines 247–257) makes the receipt minting unconditional. One caveat I could not fully confirm within the search limits: whether `IdleCDOEpochVariant` gates the instant-withdraw entry point on `defaulted()`/`defaultRecoveryFinalized` upstream of the strategy call. If it does not (matching the strategy's own lack of a check), the bug is directly reachable; the inconsistency between `requestWithdraw` (explicit post-default path) and `requestInstantWithdraw` (none) strongly suggests an oversight rather than a deliberate guard.

### Recommendation
Mirror the `requestWithdraw` post-default logic in `requestInstantWithdraw`: when `defaultRecoveryFinalized` is set, either revert outright, or require the user to have no pending receipts and record the request as a `postDefaultRequests`-style reserve-backed claim (amount already haircut by `defaultRecoveryPrice`), and ensure `claimInstantWithdrawRequest` clears post-default instant receipts through `_transferDefaultRecovery` instead of `_transferFundedClaim`.

### Proof of Concept
Foundry fork PoC outline (same harness style as `testFinalizeDefaultHaircutsPendingInstantRedeems` in `test/foundry/IdleCreditVault.t.sol`):

1. Deposit via `cdoEpoch.depositAA`, run `_startEpochAndCheckPrices(0)`, then `_stopEpochAndCheckPrices`.
2. Trigger borrower default (`_checkDefault()`) and call `cdoEpoch.finalizeDefault(recovered, manager)` with a partial `recoveryRatio` (e.g. 7e17), so `defaultRecoveryFinalized == true`.
3. As an unprivileged user, call the instant-withdraw request path on the CDO. Observe `creditVault.instantWithdrawsRequests(user) == amount` and `pendingInstantWithdraws` increased, with no underlying transferred to the strategy.
4. Call `cdoEpoch.claimInstantWithdrawRequest()`. Assert it either reverts in `_transferFundedClaim` (`balance - defaultRecoveryReserve < amount`) — receipt permanently frozen — or pays `amount` at par while the burned CDO tokens were only worth `amount * defaultRecoveryPrice`, measured against `underlying.balanceOf(strategy)` and `defaultRecoveryReserve` before/after.
5. Variant: pre-seed the user with a defaulted-epoch instant receipt (request before default, partially funded), then add a post-default instant request; assert `claimInstantWithdrawRequest` reverts, blocking the legitimate recovery claim.

If step 3 is unreachable because the CDO blocks instant requests while `defaulted()`, the finding degrades to unavailable and should be treated as no-vulnerability; that gating could not be verified within the available tool iterations.