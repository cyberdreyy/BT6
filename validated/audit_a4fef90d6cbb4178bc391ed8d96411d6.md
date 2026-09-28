### Title
`claimInstantWithdrawRequest` pays the aggregate receipt balance while funding is collected per-epoch, letting a second instant request drain vault-held funds earmarked for others — (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The credit vault tracks instant-withdraw receipts both as a per-user aggregate (`instantWithdrawsRequests[_user]`) and per-epoch (`instantWithdrawsRequestsByEpoch`, `instantWithdrawClaimsByEpoch`, `pendingInstantWithdraws`). However, `claimInstantWithdrawRequest` interprets the aggregate as fully funded: it burns the user's entire receipt balance and pays it out of the vault's underlying balance, with no check that the corresponding epoch's funds were actually collected via `collectInstantWithdrawFunds`. Funding is per-epoch; claiming is aggregate — the same class of interpretation mismatch as CVE-2018-6560 (two layers parsing the same quantity differently), mapped onto the receipt/claim surface.

### Finding Description
- `requestInstantWithdraw` mints receipt tokens and increases `instantWithdrawsRequests[_user]` and `pendingInstantWithdraws` (lines 356–375).
- Funds only enter the vault when the CDO calls `collectInstantWithdrawFunds(_amount)` during epoch stop, which pulls exactly `_amount` underlying from the CDO and decrements `pendingInstantWithdraws` (lines 398–403).
- `claimInstantWithdrawRequest` (lines 380–393) burns `instantWithdrawsRequests[_user]` — the aggregate over *all* epochs — and calls `_transferFundedClaim(_user, amount)` for the full aggregate, with no analogue of the normal-claim gating (`epochNumber <= lastWithdrawRequest[_user]` check used in `_claimFundedWithdrawRequest`, lines 326–328) and no per-epoch funded check.

Sequence (unprivileged KYC'd lender, fixed-APR mode with instant withdrawals enabled via `setInstantWithdrawParams`, attacker is `msg.sender` — honest manager/owner sequence normally):

1. Epoch N buffer: APR dropped vs `lastEpochApr + instantWithdrawAprDelta`, so `requestWithdraw` routes to `requestInstantWithdraw(X)`. Attacker has receipt X, `pendingInstantWithdraws = X`.
2. `stopEpoch` runs: `collectInstantWithdrawFunds(X)` moves X into the vault.
3. Epoch N+1 buffer: attacker calls `requestWithdraw` again while the APR condition still holds → receipt Y, `instantWithdrawsRequests[user] = X + Y`, vault balance still only X (plus other users' funded claims / recovery reserve).
4. Attacker calls `claimInstantWithdrawRequest` → burns X+Y receipts and transfers X+Y underlying. If the vault holds other users' funded withdraw claims or reserve, the call succeeds and Y is stolen; if the vault holds exactly X the call reverts, but once epoch N+1's `collectInstantWithdrawFunds(Y)` runs, the Y sits in the vault with no receipt left to claim it — permanently frozen.

### Impact Explanation
Direct theft of up to Y underlying from other claimants' funded receipts / reserve, or permanent freezing of Y in the strategy vault with the corresponding receipt tokens already burned. Either way the "one receipt, one funded payout" invariant is broken because the claim path treats a never-funded receipt as funded.

### Likelihood Explanation
Requires instant withdrawals enabled and a repeated APR-drop condition across consecutive epochs so an attacker can stack an unfunded receipt on a funded one — a configuration explicitly supported (`setInstantWithdrawParams`, `instantWithdrawAprDelta`). No privileged misbehavior needed; the attacker only sequences their own request/claim around honest `stopEpoch` calls.

### Recommendation
Gate instant claims per epoch like normal claims: record a per-epoch funded marker (or `lastInstantWithdrawRequest` epoch) and only pay receipts whose epoch's `collectInstantWithdrawFunds` has executed; alternatively burn and pay only the funded prefix (`instantWithdrawsRequestsByEpoch` for settled epochs) rather than the aggregate.

### Proof of Concept
Foundry fork PoC (against the test harness style in `test/foundry/IdleCreditVault.t.sol`):

```solidity
// setup: deposits made, instant params enabled, attacker = allowed wallet
// epoch N buffer
cdoEpoch.requestInstantWithdrawViaRequestWithdraw(xTranche, AAtranche); // requestWithdraw routes to instant
// manager stops epoch N -> collectInstantWithdrawFunds(X) funds vault with X
// epoch N+1 buffer, APR condition still true
cdoEpoch.requestInstantWithdrawViaRequestWithdraw(yTranche, AAtranche); // receipts now X+Y, vault holds X
// vault also holds funded claims of other users >= Y
cdoEpoch.claimInstantWithdrawRequest(); // pays X+Y, attacker net +Y stolen
assertEq(strategy.instantWithdrawsRequests(attacker), 0);
assertEq(underlying.balanceOf(attacker) - balPre, X + Y);
```

If the vault balance is exactly X, the same flow run past epoch N+1's stop shows Y underlying stuck in the vault with zero outstanding receipts — permanent freezing.