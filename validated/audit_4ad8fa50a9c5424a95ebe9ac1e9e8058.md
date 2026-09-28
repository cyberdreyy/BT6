### Title
Unfunded instant-withdraw receipts paid from vault balance — released before upper layer delivers funds - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
CVE-2021-46963 is a use-after-free/double-free analog: the driver freed an `srb` object that was actually owned (and later freed) by the upper SCSI layer — a layer-violating release of a resource the callee did not own. The matching surface in `IdleCreditVault` is `claimInstantWithdrawRequest`: it treats the vault-held `underlyingToken` balance as backing for every recorded instant-withdraw receipt and pays it out, even though the funds for that receipt are supposed to be delivered by the upper layer (the IdleCDO) via `collectInstantWithdrawFunds`, which decrements `pendingInstantWithdraws` and performs the `safeTransferFrom` only when the CDO actually collected liquidity at instant-withdraw trigger time. Nothing in `claimInstantWithdrawRequest` verifies that the claim's basis was ever funded.

### Finding Description
- `requestInstantWithdraw` burns the CDO's strategy tokens, mints a receipt to `_user`, and records `instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch`, `instantWithdrawClaimsByEpoch`, and `pendingInstantWithdraws` (lines 356–375).
- `collectInstantWithdrawFunds(_amount)` is the only path that moves underlying from `idleCDO` into the vault and decrements `pendingInstantWithdraws` (lines 398–403).
- `claimInstantWithdrawRequest(_user)` then burns the receipt and calls `_transferFundedClaim(_user, instantWithdrawsRequests[_user])` unconditionally (lines 380–393). There is no check that the paid amount corresponds to underlying actually received through `collectInstantWithdrawFunds`; the receipt is "freed" against assets held by the vault for other purposes — exactly the "callee frees a resource owned by the upper layer" shape of the kernel bug.
- On the default path the same pattern repeats: `_claimDefaultedInstantWithdrawRequest` clears the defaulted-epoch receipt and clamps `pendingInstantWithdraws` to 0 when the claim exceeds the unfunded remainder (lines 842–856), after which `claimInstantWithdrawRequest` still pays the remaining `instantWithdrawsRequests[_user]` at par from the vault balance rather than from a bounded funded bucket.

Caveat: `claimInstantWithdrawRequest` is gated by `_onlyIdleCDO()`, so exploitability depends on the honest `IdleCDOEpochVariant` forwarding the call before/independently of funding. I could not fully confirm the CDO-side gating (whether the CDO blocks claims until `collectInstantWithdrawFunds` has run for the full `instantWithdrawsRequests` amount). If the CDO allows the claim once the instant delay elapses while funding lags or partially fails, the vault pays from its own balance.

### Impact Explanation
The vault's `underlyingToken` balance is the asset pool backing active tranche holders and pending claims. Paying an unfunded instant receipt from that balance transfers value belonging to other depositors/receipt-holders to the claimant — direct theft/insolvency of up to the vault's underlying balance for each unbacked receipt claimed. Broken invariant: one receipt, one funded payout; claim payouts must be isolated to the collected instant-withdraw bucket.

### Likelihood Explanation
The attacker only needs a KYC-allowed wallet to deposit tranche tokens and call `requestWithdraw`/instant-withdraw flow during a running epoch; owner/manager/borrower calls (startEpoch, stopEpoch, instant trigger) happen normally. The window exists whenever a claim can reach the strategy before full `collectInstantWithdrawFunds` settlement, e.g., when instant liquidity is partially available or the claim path is invoked in a later epoch after `pendingInstantWithdraws` was already consumed by other claims.

### Recommendation
Track a funded instant-claim bucket (e.g., `fundedInstantClaims`) incremented in `collectInstantWithdrawFunds` and decremented in `claimInstantWithdrawRequest`, paying only `min(instantWithdrawsRequests[_user], fundedInstantClaims)` or reverting when the claim exceeds funded amounts. Analogously, bind `_claimFundedWithdrawRequest`/`_transferFundedClaim` payouts to the actual collected balance rather than the recorded request amount, so a receipt can never be "freed" against assets owned by the upper layer's other obligations.

### Proof of Concept
Foundry fork PoC sketch (mode: standard epoch variant, instant withdrawals enabled, attacker = KYC-passing lender):

```solidity
// 1. Stop epoch 0, manager sets instant withdraw params (delay, minAprDelta).
// 2. Victim deposits large amount (seeds vault underlying balance indirectly).
// 3. Attacker deposits, receives tranches; epoch starts.
// 4. Attacker calls cdoEpoch.requestInstantWithdraw(trancheAmount) ->
//    vault burns CDO tokens, mints receipt, records
//    instantWithdrawsRequests[attacker] += amount; pendingInstantWithdraws += amount.
// 5. Trigger instant withdrawal with insufficient collected liquidity:
//    getInstantWithdrawFunds/collectInstantWithdrawFunds move only a partial
//    amount (or none for this user) into the vault.
// 6. After instantWithdrawDelay, call
//    cdoEpoch.claimInstantWithdrawRequest() ->
//    strategy.claimInstantWithdrawRequest(attacker) burns the receipt and pays
//    instantWithdrawsRequests[attacker] IN FULL from vault underlying balance.
// Assert: attacker received more underlying than collectInstantWithdrawFunds
// ever moved for that receipt; vault underlying balance decreased by the
// unfunded delta, reducing backing for remaining depositors.
```

Verify funding delta with `underlying.balanceOf(address(strategy))` before/after the claim versus cumulative `collectInstantWithdrawFunds` amounts; if paid amount exceeds collected funds by any positive delta, the double-free analog is confirmed.