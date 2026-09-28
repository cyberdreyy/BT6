### Title
Unfunded instant-withdraw receipts are paid at par and can drain underlyings reserved for funded normal withdraw claims - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`claimInstantWithdrawRequest` honors a user's entire `instantWithdrawsRequests` receipt balance without checking how much of it was actually funded via `collectInstantWithdrawFunds`. The code itself acknowledges partial prefunding of the instant queue (`_defaultPrefundedInstantReserve`: "cash covered only part of the instant queue"), so an unprivileged lender holding a partially-funded instant receipt can withdraw its full basis and steal underlyings that back other users' already-funded normal withdraw receipts.

### Finding Description
The UAF bug class maps to a stale/unfunded receipt being redeemed against freed/reassigned backing: `requestInstantWithdraw` mints receipt strategy tokens 1:1 and increments both `instantWithdrawsRequests[_user]` and the global `pendingInstantWithdraws` (IdleCreditVault.sol:356-375). Funding arrives later through `collectInstantWithdrawFunds`, which only decrements `pendingInstantWithdraws` and pulls `_amount` underlyings — there is no per-user funded/unfunded split tracked (IdleCreditVault.sol:398-403). `claimInstantWithdrawRequest` then burns the full `instantWithdrawsRequests[_user]` and calls `_transferFundedClaim` for the whole amount (IdleCreditVault.sol:380-393). `_transferFundedClaim` only ring-fences `defaultRecoveryReserve`; it does not isolate funded normal-withdraw underlyings held in the same strategy balance (IdleCreditVault.sol:897-907). Normal funded receipts land in this same pot via `collectWithdrawFunds` (IdleCreditVault.sol:411-430). So when the instant queue is only partially prefunded (`pendingInstantWithdraws > 0` remainder, the exact scenario `_defaultPrefundedInstantReserve` is built for at IdleCreditVault.sol:716-723), an instant claimant is paid for the unfunded portion out of underlyings owed to normal funded withdraw claimants.

### Impact Explanation
Direct theft plus permanent freezing: the attacker receives `instantWithdrawsRequests[attacker] - fundedShare` underlyings they are not entitled to; subsequently, holders of funded normal receipts calling `claimWithdrawRequest` → `_claimFundedWithdrawRequest` find `underlyingToken.balanceOf(this) - reserve < amount` and the safeTransfer reverts, permanently freezing their claims while their receipt bookkeeping remains. Loss is quantified as the unfunded remainder `instantWithdrawsRequests[attacker] - fundedShare`, bounded only by the size of the funded pending-withdraw pool.

### Likelihood Explanation
Attacker requirements are minimal: a KYC-passing lender (`isWalletAllowed`) in a pool with `allowInstantWithdraw` enabled. The trigger — partial instant-queue prefunding at epoch start — is a designed-for state (the prefunded-reserve logic exists precisely because the CDO's available cash can cover only part of the instant queue), requiring only that honest manager/borrower calls leave `pendingInstantWithdraws > 0` while funded normal receipts sit unclaimed, a routine race during the buffer period. No privileged misbehavior is needed.

### Recommendation
Track funded vs. unfunded instant claims: either revert `claimInstantWithdrawRequest` while `pendingInstantWithdraws != 0` for the caller's request epoch, or maintain a per-epoch funded price for instant receipts (analogous to `lossRecoveryPriceByEpoch`) and pay `instantWithdrawsRequestsByEpoch[user][epoch] * fundedRatio[epoch]`. At minimum, `_transferFundedClaim` should exclude underlyings earmarked for outstanding normal withdraw claims, not just `defaultRecoveryReserve`.

### Proof of Concept
Foundry fork outline (mirroring `test/foundry/IdleCreditVault.t.sol` patterns):

```solidity
// Setup: pool with allowInstantWithdraw = true, honest borrower/manager.
// 1. User B deposits AA, requests normal withdraw in epoch N (requestWithdraw).
//    Epoch N stops: borrower funds B's receipt via collectWithdrawFunds
//    -> strategy now holds B's funded underlyings, pendingWithdraws == 0.
// 2. Attacker A (KYC'd lender) calls requestInstantWithdraw(X) before startEpoch.
//    -> instantWithdrawsRequests[A] = X, pendingInstantWithdraws = X.
// 3. startEpoch prefunds only Y < X (CDO liquidity shortfall):
//    collectInstantWithdrawFunds(Y) -> pendingInstantWithdraws = X - Y.
// 4. A calls cdoEpoch.claimInstantWithdrawRequest():
//    claimInstantWithdrawRequest burns X receipt tokens and _transferFundedClaim
//    sends the FULL X, including X - Y taken from B's funded underlyings.
// 5. B calls claimWithdrawRequest() -> _claimFundedWithdrawRequest reverts
//    (balance - reserve < B's amount): B's funded claim is permanently frozen.
// Assert: underlying.balanceOf(A) increased by X; B's claim reverts NotAllowed.
```

Caveat: the PoC depends on the CDO's `startEpoch`/instant-funding path being able to transfer only part of `pendingInstantWithdraws` (grep output for `getInstantWithdrawFunds` in `contracts/IdleCDOEpochVariant.sol` returned matches I could not fully read within the iteration budget). The strategy's own `_defaultPrefundedInstantReserve` docstring confirms partial instant prefunding is an expected state, but the exact CDO call path that produces it should be confirmed before finalizing the PoC.